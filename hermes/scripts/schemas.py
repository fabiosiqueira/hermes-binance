# Contratos de dados do estrategista Hermes (pydantic v2).
#
# São o que torna a liberdade do LLM "catálogo + composição", não texto livre:
# - Brief: o que o Hermes LÊ (estado de mercado/portfólio/risco montado pelo adapter).
# - StrategyProposal: o que o Hermes ESCREVE, validado pelo risk_engine vs Dogmas.
# - Dogmas: constituição de risco determinística, preenchida pelo operador.
#
# Os campos de Brief.market/portfolio refletem o que é REALMENTE derivável dos
# endpoints betrader-hydra (ver docs/superpowers/specs/2026-06-09-betrader-api-verification.md);
# a origem de cada campo está documentada no docstring do field.
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class ExecutionMode(StrEnum):
    """Modo de execução do estrategista; controla isTestnet/gradação no betrader."""

    DRY_RUN = "DRY_RUN"
    HOM = "HOM"
    PROD = "PROD"


class IndicatorSpec(BaseModel):
    """Item do catálogo de indicadores disponíveis (origem: GET /api/indicators)."""

    name: str
    params: dict = Field(default_factory=dict)
    description: str | None = None


class Candle(BaseModel):
    """Candle OHLCV.

    Origem: processado internamente pelo monitor CANDLES do betrader (não exposto
    via GET público de /api/market). `open_time` é epoch em milissegundos, no
    formato kline da Binance; demais campos são floats.
    """

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketState(BaseModel):
    """Estado de mercado do par/timeframe primário.

    - `symbol`/`timeframe`: par e timeframe da revisão (M1: BTCUSDT, fechamento de candle).
    - `candles`: série OHLCV (origem: monitor CANDLES do betrader).
    - `indicators`: valores correntes (origem: POST /api/market, indicatorsParams por timeframe),
      chaveados por nome do indicador. `None` = dado indisponível (cache miss); o betrader
      retorna `{}`/`0` nesse caso, o adapter normaliza para `None`.
    """

    symbol: str
    timeframe: str
    candles: list[Candle] = Field(default_factory=list)
    indicators: dict[str, float | None] = Field(default_factory=dict)


class Position(BaseModel):
    """Posição aberta em futuros.

    Origem: GET /api/futures?symbol= (array FuturesPosition[] da Binance).
    Mapeamento: side derivado do sinal de `positionAmt`; `entry_price`=entryPrice;
    `quantity`=abs(positionAmt); `unrealized_pnl`=unRealizedProfit; `leverage`=leverage.
    """

    symbol: str
    side: Literal["BUY", "SELL"]
    entry_price: float
    quantity: float
    unrealized_pnl: float
    leverage: int


class Portfolio(BaseModel):
    """Estado de portfólio/conta.

    - `equity`: derivado de GET /api/exchange/balance?isFuture=true (campo `fiatEstimate`,
      string "~USDT N" parseada para float pelo adapter).
    - `balance`: GET /api/exchange/balance?isFuture=false → assets.USDT.available.
    - `positions`: GET /api/futures?symbol= (FuturesPosition[]).
    - `used_leverage`: agregado do campo `leverage` das posições abertas (GET /api/futures).
    """

    equity: float
    balance: float
    positions: list[Position] = Field(default_factory=list)
    used_leverage: float


class RiskState(BaseModel):
    """Estado de risco corrente, sobre a equity-curve persistida.

    - `daily_pnl`/`drawdown_pct`: computados pela observability sobre a equity-curve
      (initialEquity + cumPnL), nunca PnL isolado.
    - `equity_curve_ref`: chave Redis onde a equity-curve persistida vive.
    """

    daily_pnl: float
    drawdown_pct: float
    equity_curve_ref: str


class ActiveItem(BaseModel):
    """Automation/order vigente no betrader, com performance opcional.

    Origem: GET /api/automations e GET /api/orders. `kind` distingue as duas fontes.
    """

    id: str
    kind: Literal["automation", "order"]
    summary: str
    performance: dict | None = None


class Brief(BaseModel):
    """O que o Hermes LÊ a cada ciclo: snapshot tipado de mercado/portfólio/risco.

    Montado pelo adapter a partir de /api/market, /api/futures,
    /api/exchange/balance, /api/beholder/memory, /api/indicators.
    """

    timestamp: str
    mode: ExecutionMode
    catalog: list[IndicatorSpec] = Field(default_factory=list)
    market: MarketState
    portfolio: Portfolio
    risk_state: RiskState
    active: list[ActiveItem] = Field(default_factory=list)
    # Índices VIVOS do Beholder (GET /api/automations/indexes): nomes exatos
    # (com sufixos de interval/userId, ex. RSI_14_15m, LIQ_PROXIMITY_PCT_<uid>)
    # para compor conditions de automation. Passthrough não-tipado e tolerante:
    # falha do endpoint → lista vazia, o Brief não quebra.
    memory_indexes: list[dict] = Field(default_factory=list)


class EntryOrder(BaseModel):
    """Ordem de entrada proposta pelo Hermes.

    `stop_loss` é OBRIGATÓRIO por construção — entrada sem SL é inválida antes mesmo
    do gate. `sizing_pct` é % do equity (0, 100]. Para LIMIT, `limit_price` é exigido
    e a coerência SL↔preço é validada; para MARKET o preço corrente não está no schema,
    então valida-se apenas positividade do SL.
    """

    symbol: str
    side: Literal["BUY", "SELL"]
    sizing_pct: float = Field(gt=0, le=100)
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None = None
    stop_loss: float = Field(gt=0)
    take_profit: float | None = None
    leverage: int = Field(ge=1)

    @model_validator(mode="after")
    def _validar_coerencia(self) -> "EntryOrder":
        if self.order_type == "LIMIT":
            if self.limit_price is None:
                raise ValueError("limit_price é obrigatório quando order_type=LIMIT")
            # BUY: stop fica abaixo do preço de entrada; SELL: acima.
            if self.side == "BUY" and self.stop_loss >= self.limit_price:
                raise ValueError("BUY: stop_loss deve ser < limit_price")
            if self.side == "SELL" and self.stop_loss <= self.limit_price:
                raise ValueError("SELL: stop_loss deve ser > limit_price")
        return self


# Formato de condition do Beholder: MEMORY['SYMBOL:INDICATOR_params'](.path)* <op> valor.
# LHS aceita dot-path de propriedades (memórias de indicador são objetos
# {current, previous} no Beholder); RHS é SEMPRE literal numérico — sem aritmética,
# sem outro MEMORY: a condition vira corpo de Function() no Beholder e este regex é
# a barreira anti-injeção do lado do estrategista (gate soberano).
_AUTOMATION_CONDITION_RE = re.compile(
    r"^(MEMORY\['([A-Z0-9]+):([A-Za-z0-9_]+)'\](?:\.[A-Za-z_][A-Za-z0-9_]*)*)"
    r"\s*(>=|<=|===|!=|>|<)\s*(-?[0-9.]+)$"
)


def parse_automation_condition(condition: str) -> dict:
    """Decompõe a condition no shape do betrader (AutomationCondition + Automation).

    Retorna {eval, operator, variable, symbol, index_key}: eval/operator/variable
    são os campos do AutomationCondition; symbol e index_key (SYMBOL:INDICATOR,
    sem dot-path) alimentam Automation.symbol e Automation.indexes — sem indexes
    o brain do Beholder nunca dispara a automation.
    """
    match = _AUTOMATION_CONDITION_RE.match(condition)
    if match is None:
        raise ValueError(
            "condition fora do formato Beholder: "
            "MEMORY['SYMBOL:INDICATOR_params'] <op> valor"
        )
    lhs, symbol, indicator, operator, variable = match.groups()
    return {
        "eval": lhs,
        "operator": operator,
        "variable": variable,
        "symbol": symbol,
        "index_key": f"{symbol}:{indicator}",
    }


class AutomationSpec(BaseModel):
    """Automation de gestão/saída a instalar no Beholder.

    `condition` segue o formato exato do Beholder (validado por regex); `action` é o
    payload livre da action betrader (ex.: {"type": "ORDER", "side": "SELL",
    "reduceOnly": true}), repassado ao POST /api/automations. `schedule` opcional
    (cron) para automations agendadas.
    """

    name: str
    condition: str
    action: dict
    schedule: str | None = None

    @model_validator(mode="after")
    def _validar_condition(self) -> "AutomationSpec":
        if not _AUTOMATION_CONDITION_RE.match(self.condition):
            raise ValueError(
                "condition fora do formato Beholder: "
                "MEMORY['SYMBOL:INDICATOR_params'] <op> valor"
            )
        return self


class StrategyProposal(BaseModel):
    """O que o Hermes ESCREVE; o risk_engine valida contra os Dogmas.

    `reasoning` é obrigatório (rastro da decisão). Entradas/automations/teardown
    default vazios — ciclo sem ação é uma proposta válida.
    """

    reasoning: str = Field(min_length=1)
    entries: list[EntryOrder] = Field(default_factory=list)
    automations: list[AutomationSpec] = Field(default_factory=list)
    teardown: list[str] = Field(default_factory=list)


class Dogmas(BaseModel):
    """Constituição de risco determinística (operador preenche em dogmas.yaml).

    `mandatory_stop_loss` é Literal[True]: o SL obrigatório não pode ser desligado
    por configuração — desligá-lo é um caminho que não existe.
    """

    max_leverage: int
    max_position_pct_equity: float
    max_daily_drawdown_pct: float
    mandatory_stop_loss: Literal[True] = True
    min_stop_distance_pct: float
    allowed_symbols: list[str]


def load_dogmas(path: str | Path) -> Dogmas:
    """Carrega e valida os Dogmas de um arquivo YAML (yaml.safe_load + Dogmas)."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return Dogmas.model_validate(data)
