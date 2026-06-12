# Unit tests dos contratos de dados do estrategista (pydantic v2).
# Cobre os invariantes centrais do design: SL obrigatório, formato MEMORY das
# automations, limites de sizing/leverage e round-trip JSON do Brief/Proposal.
from pathlib import Path

import pytest
from pydantic import ValidationError

from schemas import (
    ActiveItem,
    AutomationSpec,
    Brief,
    Candle,
    Dogmas,
    EntryOrder,
    ExecutionMode,
    IndicatorSpec,
    MarketState,
    Portfolio,
    Position,
    RiskState,
    StrategyProposal,
    load_dogmas,
)

DOGMAS_PATH = Path(__file__).resolve().parents[2] / "hermes" / "dogmas.yaml"


# --- EntryOrder: stop_loss obrigatório (invariante central do design) ---


def test_entry_order_sem_stop_loss_falha() -> None:
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="BUY",
            sizing_pct=5.0,
            order_type="MARKET",
            leverage=2,
        )  # type: ignore[call-arg]


def test_entry_order_stop_loss_nao_positivo_falha() -> None:
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="BUY",
            sizing_pct=5.0,
            order_type="MARKET",
            stop_loss=0.0,
            leverage=2,
        )


def test_entry_order_market_valido() -> None:
    order = EntryOrder(
        symbol="BTCUSDT",
        side="BUY",
        sizing_pct=5.0,
        order_type="MARKET",
        stop_loss=50000.0,
        leverage=2,
    )
    assert order.stop_loss == 50000.0
    assert order.limit_price is None


# --- EntryOrder: LIMIT exige limit_price + coerência SL/preço ---


def test_entry_order_limit_sem_limit_price_falha() -> None:
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="BUY",
            sizing_pct=5.0,
            order_type="LIMIT",
            stop_loss=50000.0,
            leverage=2,
        )


def test_entry_order_limit_buy_stop_acima_do_preco_falha() -> None:
    # BUY: stop_loss deve ficar ABAIXO do limit_price.
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="BUY",
            sizing_pct=5.0,
            order_type="LIMIT",
            limit_price=60000.0,
            stop_loss=61000.0,
            leverage=2,
        )


def test_entry_order_limit_buy_valido() -> None:
    order = EntryOrder(
        symbol="BTCUSDT",
        side="BUY",
        sizing_pct=5.0,
        order_type="LIMIT",
        limit_price=60000.0,
        stop_loss=59000.0,
        take_profit=63000.0,
        leverage=2,
    )
    assert order.limit_price == 60000.0


def test_entry_order_limit_sell_stop_abaixo_do_preco_falha() -> None:
    # SELL: stop_loss deve ficar ACIMA do limit_price.
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="SELL",
            sizing_pct=5.0,
            order_type="LIMIT",
            limit_price=60000.0,
            stop_loss=59000.0,
            leverage=2,
        )


def test_entry_order_limit_sell_valido() -> None:
    order = EntryOrder(
        symbol="BTCUSDT",
        side="SELL",
        sizing_pct=5.0,
        order_type="LIMIT",
        limit_price=60000.0,
        stop_loss=61000.0,
        leverage=2,
    )
    assert order.side == "SELL"


# --- EntryOrder: sizing_pct e leverage ---


@pytest.mark.parametrize("sizing", [0.0, -1.0, 100.1, 150.0])
def test_entry_order_sizing_pct_fora_do_intervalo_falha(sizing: float) -> None:
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="BUY",
            sizing_pct=sizing,
            order_type="MARKET",
            stop_loss=50000.0,
            leverage=2,
        )


def test_entry_order_sizing_pct_limite_superior_valido() -> None:
    order = EntryOrder(
        symbol="BTCUSDT",
        side="BUY",
        sizing_pct=100.0,
        order_type="MARKET",
        stop_loss=50000.0,
        leverage=1,
    )
    assert order.sizing_pct == 100.0


def test_entry_order_leverage_menor_que_um_falha() -> None:
    with pytest.raises(ValidationError):
        EntryOrder(
            symbol="BTCUSDT",
            side="BUY",
            sizing_pct=5.0,
            order_type="MARKET",
            stop_loss=50000.0,
            leverage=0,
        )


# --- AutomationSpec: condition no formato MEMORY do Beholder ---


@pytest.mark.parametrize(
    "condition",
    [
        "MEMORY['BTCUSDT:RSI_14'] > 70",
        "MEMORY['BTCUSDT:MACD_12_26_9'] <= -1.5",
        "MEMORY['BTCUSDT:CLOSE'] === 60000",
        "MEMORY['ETHUSDT:EMA_50'] != 3000",
        # Dot-path no LHS (#7): memórias de indicador são objetos {current, previous}
        # no Beholder; sem o acesso à propriedade a comparação nunca dispara.
        "MEMORY['BTCUSDT:RSI_14_15m'].current > 70",
        "MEMORY['BTCUSDT:MARK_PRICE'].current.markPrice > 60000",
        # Índice derivado de liq-proximity (#7): valor plano, threshold relativo (%).
        "MEMORY['BTCUSDT:LIQ_PROXIMITY_PCT_clw1abc23'] < 2",
    ],
)
def test_automation_condition_formato_valido(condition: str) -> None:
    spec = AutomationSpec(
        name="exit_overbought",
        condition=condition,
        action={"type": "ORDER", "side": "SELL"},
    )
    assert spec.condition == condition


@pytest.mark.parametrize(
    "condition",
    [
        "rsi > 70",  # sem prefixo MEMORY
        "MEMORY['btcusdt:rsi'] > 70",  # symbol minúsculo
        "MEMORY['BTCUSDT:RSI_14'] => 70",  # operador inválido
        "MEMORY['BTCUSDT:RSI_14'] > abc",  # valor não numérico
        "MEMORY[BTCUSDT:RSI_14] > 70",  # sem aspas
        "MEMORY['BTCUSDT:RSI_14'].current() > 70",  # chamada de função no path
        "MEMORY['BTCUSDT:RSI_14'] > 70 * 2",  # aritmética no RHS (gate soberano)
        "MEMORY['BTCUSDT:RSI_14'] > MEMORY['BTCUSDT:EMA_50']",  # RHS indexado
    ],
)
def test_automation_condition_formato_invalido_falha(condition: str) -> None:
    with pytest.raises(ValidationError):
        AutomationSpec(
            name="bad",
            condition=condition,
            action={"type": "ORDER"},
        )


# --- Brief.memory_indexes (#7): índices vivos do Beholder no Brief ---


def test_brief_memory_indexes_default_vazio() -> None:
    brief = Brief(
        timestamp="2026-06-12T22:00:00Z",
        mode=ExecutionMode.DRY_RUN,
        market=MarketState(symbol="BTCUSDT", timeframe="15m"),
        portfolio=Portfolio(equity=10000.0, balance=10000.0, used_leverage=0.0),
        risk_state=RiskState(daily_pnl=0.0, drawdown_pct=0.0, equity_curve_ref="k"),
    )
    assert brief.memory_indexes == []


def test_brief_memory_indexes_roundtrip() -> None:
    brief = Brief(
        timestamp="2026-06-12T22:00:00Z",
        mode=ExecutionMode.DRY_RUN,
        market=MarketState(symbol="BTCUSDT", timeframe="15m"),
        portfolio=Portfolio(equity=10000.0, balance=10000.0, used_leverage=0.0),
        risk_state=RiskState(daily_pnl=0.0, drawdown_pct=0.0, equity_curve_ref="k"),
        memory_indexes=[
            {
                "symbol": "BTCUSDT",
                "variable": "LIQ_PROXIMITY_PCT_u1",
                "eval": "MEMORY['BTCUSDT:LIQ_PROXIMITY_PCT_u1']",
            }
        ],
    )
    reloaded = Brief.model_validate_json(brief.model_dump_json())
    assert reloaded.memory_indexes[0]["variable"] == "LIQ_PROXIMITY_PCT_u1"


# --- parse_automation_condition (#7): split eval/operator/variable p/ o betrader ---


def test_parse_automation_condition_decompoe_partes() -> None:
    from schemas import parse_automation_condition

    parsed = parse_automation_condition("MEMORY['BTCUSDT:RSI_14_15m'].current > 70")
    assert parsed == {
        "eval": "MEMORY['BTCUSDT:RSI_14_15m'].current",
        "operator": ">",
        "variable": "70",
        "symbol": "BTCUSDT",
        "index_key": "BTCUSDT:RSI_14_15m",
    }


def test_parse_automation_condition_sem_dot_path() -> None:
    from schemas import parse_automation_condition

    parsed = parse_automation_condition("MEMORY['BTCUSDT:LIQ_PROXIMITY_PCT_u1'] < 2")
    assert parsed["eval"] == "MEMORY['BTCUSDT:LIQ_PROXIMITY_PCT_u1']"
    assert parsed["operator"] == "<"
    assert parsed["variable"] == "2"
    assert parsed["symbol"] == "BTCUSDT"
    assert parsed["index_key"] == "BTCUSDT:LIQ_PROXIMITY_PCT_u1"


def test_parse_automation_condition_invalida_levanta() -> None:
    from schemas import parse_automation_condition

    with pytest.raises(ValueError):
        parse_automation_condition("rsi > 70")


# --- StrategyProposal: reasoning obrigatório + defaults ---


def test_strategy_proposal_reasoning_vazio_falha() -> None:
    with pytest.raises(ValidationError):
        StrategyProposal(reasoning="")


def test_strategy_proposal_defaults_vazios() -> None:
    proposal = StrategyProposal(reasoning="sem ação neste ciclo")
    assert proposal.entries == []
    assert proposal.automations == []
    assert proposal.teardown == []


# --- Dogmas: mandatory_stop_loss não pode ser desligado ---


def test_dogmas_mandatory_stop_loss_false_falha() -> None:
    with pytest.raises(ValidationError):
        Dogmas(
            max_leverage=5,
            max_position_pct_equity=10.0,
            max_daily_drawdown_pct=3.0,
            mandatory_stop_loss=False,
            min_stop_distance_pct=0.5,
            allowed_symbols=["BTCUSDT"],
        )


def test_load_dogmas_carrega_yaml_exemplo() -> None:
    dogmas = load_dogmas(DOGMAS_PATH)
    assert dogmas.max_leverage == 5
    assert dogmas.max_position_pct_equity == 10.0
    assert dogmas.max_daily_drawdown_pct == 3.0
    assert dogmas.mandatory_stop_loss is True
    assert dogmas.min_stop_distance_pct == 0.5
    assert dogmas.allowed_symbols == ["BTCUSDT"]


# --- Round-trip JSON: Brief e StrategyProposal completos ---


def _brief_completo() -> Brief:
    return Brief(
        timestamp="2026-06-09T12:00:00Z",
        mode=ExecutionMode.DRY_RUN,
        catalog=[
            IndicatorSpec(name="RSI", params={"period": 14}, description="Relative Strength Index"),
            IndicatorSpec(name="EMA", params={"period": 50}),
        ],
        market=MarketState(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=[
                Candle(
                    open_time=1717934400000,
                    open=60000.0,
                    high=60500.0,
                    low=59800.0,
                    close=60200.0,
                    volume=123.45,
                ),
            ],
            indicators={"RSI_14": 55.3, "EMA_50": None},
        ),
        portfolio=Portfolio(
            equity=10000.0,
            balance=9500.0,
            positions=[
                Position(
                    symbol="BTCUSDT",
                    side="BUY",
                    entry_price=59000.0,
                    quantity=0.01,
                    unrealized_pnl=12.0,
                    leverage=2,
                ),
            ],
            used_leverage=2.0,
        ),
        risk_state=RiskState(
            daily_pnl=15.0,
            drawdown_pct=1.2,
            equity_curve_ref="hermes:equity_curve:BTCUSDT",
        ),
        active=[
            ActiveItem(
                id="auto-1",
                kind="automation",
                summary="exit on RSI>70",
                performance={"pnl": 5.0},
            ),
            ActiveItem(id="order-9", kind="order", summary="SL @ 58000"),
        ],
    )


def test_brief_round_trip_json() -> None:
    brief = _brief_completo()
    dumped = brief.model_dump_json()
    restored = Brief.model_validate_json(dumped)
    assert restored == brief


def test_strategy_proposal_round_trip_json() -> None:
    proposal = StrategyProposal(
        reasoning="RSI saiu de sobrevenda; entrada long com SL sob o swing low.",
        entries=[
            EntryOrder(
                symbol="BTCUSDT",
                side="BUY",
                sizing_pct=5.0,
                order_type="LIMIT",
                limit_price=60000.0,
                stop_loss=59000.0,
                take_profit=63000.0,
                leverage=3,
            ),
        ],
        automations=[
            AutomationSpec(
                name="exit_overbought",
                condition="MEMORY['BTCUSDT:RSI_14'] > 70",
                action={"type": "ORDER", "side": "SELL", "reduceOnly": True},
                schedule="*/5 * * * *",
            ),
        ],
        teardown=["auto-old-1", "order-old-2"],
    )
    dumped = proposal.model_dump_json()
    restored = StrategyProposal.model_validate_json(dumped)
    assert restored == proposal
