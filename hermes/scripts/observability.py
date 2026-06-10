# Observabilidade do estrategista Hermes: métricas Prometheus + estado financeiro
# persistido em Redis.
#
# Namespace de chaves Redis: binance:strategist:*
# Porta /metrics: 9468  (prometheus_client start_http_server)
# Porta /health:  9469  (HTTP simples, responde 200 {"status":"ok"})
#
# Decisão de arquitetura: /health fica numa porta separada (9469) em vez de uma
# rota única porque prometheus_client.start_http_server não expõe rotas custom —
# seria necessário um servidor WSGI completo só para isso. Porta única separada
# é mais simples e mantém cada responsabilidade isolada.
import datetime
import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Callable, Optional

import httpx
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    start_http_server,
)

from schemas import RiskState

# ---------------------------------------------------------------------------
# Chaves Redis (namespace binance:strategist:*)
# ---------------------------------------------------------------------------

_KEY_FINANCIAL_STATE = "binance:strategist:financial_state"
_KEY_DECISIONS_STREAM = "binance:strategist:decisions"
_DECISIONS_MAXLEN = 1000


# ---------------------------------------------------------------------------
# FinancialState
# ---------------------------------------------------------------------------


class FinancialState:
    """Estado financeiro persistido na equity-curve.

    Invariante: initial_equity é lido do env APENAS na primeira inicialização
    (quando não existe ainda no Redis). Após persistido, SEMPRE vem do Redis —
    env é ignorado, mesmo que seu valor mude.

    Campos:
    - initial_equity: capital inicial (USDT)
    - cum_pnl: PnL acumulado desde a inicialização
    - peak_equity: maior equity já atingida (para cálculo de MaxDD)
    - daily_pnl: PnL do dia corrente UTC
    - daily_date: data do dia corrente UTC (reset automático em virada de dia)
    - wins: trades com PnL > 0
    - losses: trades com PnL <= 0
    """

    def __init__(
        self,
        initial_equity: float = 0.0,
        cum_pnl: float = 0.0,
        peak_equity: Optional[float] = None,
        daily_pnl: float = 0.0,
        daily_date: Optional[datetime.date] = None,
        wins: int = 0,
        losses: int = 0,
    ) -> None:
        self.initial_equity = initial_equity
        self.cum_pnl = cum_pnl
        # peak começa igual à equity inicial se não informado
        self.peak_equity: float = peak_equity if peak_equity is not None else initial_equity
        self.daily_pnl = daily_pnl
        self.daily_date: Optional[datetime.date] = daily_date
        self.wins = wins
        self.losses = losses

    # ------------------------------------------------------------------
    # Propriedades derivadas
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float:
        """Equity atual = initial_equity + cum_pnl."""
        return self.initial_equity + self.cum_pnl

    @property
    def drawdown_pct(self) -> float:
        """Drawdown percentual sobre a equity-curve. 0 se peak == 0."""
        if self.peak_equity == 0.0:
            return 0.0
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100
        return max(dd, 0.0)

    @property
    def win_rate(self) -> float:
        """Taxa de acerto. 0 se nenhum trade registrado (sem divisão por zero)."""
        total = self.wins + self.losses
        if total == 0:
            return 0.0
        return self.wins / total

    # ------------------------------------------------------------------
    # Atualização de estado
    # ------------------------------------------------------------------

    def record_trade(
        self,
        pnl: float,
        trade_date: Optional[datetime.date] = None,
    ) -> None:
        """Registra resultado de um trade na equity-curve.

        Args:
            pnl: resultado do trade em USDT (positivo = lucro, negativo = prejuízo).
            trade_date: data UTC do trade. Quando None, usa datetime.date.today() (UTC).
        """
        today = trade_date if trade_date is not None else datetime.datetime.now(datetime.timezone.utc).date()

        # Reset de daily_pnl na virada de dia UTC
        if self.daily_date is None or today != self.daily_date:
            self.daily_pnl = 0.0
            self.daily_date = today

        self.cum_pnl += pnl
        self.daily_pnl += pnl

        # Atualiza peak_equity
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity

        # Contabiliza win/loss
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    # ------------------------------------------------------------------
    # Persistência Redis (hash JSON, atômico)
    # ------------------------------------------------------------------

    def persist(self, redis: object) -> None:  # type: ignore[type-arg]
        """Persiste o estado como hash JSON no Redis. Operação atômica (single key)."""
        payload = {
            "initial_equity": self.initial_equity,
            "cum_pnl": self.cum_pnl,
            "peak_equity": self.peak_equity,
            "daily_pnl": self.daily_pnl,
            "daily_date": self.daily_date.isoformat() if self.daily_date else None,
            "wins": self.wins,
            "losses": self.losses,
        }
        redis.set(_KEY_FINANCIAL_STATE, json.dumps(payload))  # type: ignore[attr-defined]

    @classmethod
    def load(
        cls,
        redis: object,  # type: ignore[type-arg]
        initial_equity_env: Optional[str] = None,
    ) -> "FinancialState":
        """Carrega o estado do Redis. Se não existir, inicializa com initial_equity_env.

        Args:
            redis: cliente Redis (real ou fakeredis).
            initial_equity_env: valor de INITIAL_EQUITY do env (str ou None). Usado
                APENAS quando não há estado persistido no Redis. Após a primeira
                persistência, este parâmetro é ignorado.
        """
        raw = redis.get(_KEY_FINANCIAL_STATE)  # type: ignore[attr-defined]
        if raw is None:
            # Primeira inicialização: usa env (ou 0 se não configurado)
            equity_str = initial_equity_env or os.environ.get("INITIAL_EQUITY", "0")
            initial_equity = float(equity_str)
            return cls(initial_equity=initial_equity)

        data = json.loads(raw)
        daily_date: Optional[datetime.date] = None
        if data.get("daily_date"):
            daily_date = datetime.date.fromisoformat(data["daily_date"])

        return cls(
            initial_equity=float(data["initial_equity"]),
            cum_pnl=float(data["cum_pnl"]),
            peak_equity=float(data["peak_equity"]),
            daily_pnl=float(data["daily_pnl"]),
            daily_date=daily_date,
            wins=int(data["wins"]),
            losses=int(data["losses"]),
        )

    # ------------------------------------------------------------------
    # Integração com schemas
    # ------------------------------------------------------------------

    def to_risk_state(self) -> RiskState:
        """Converte para RiskState (usado no Brief a cada ciclo)."""
        return RiskState(
            daily_pnl=self.daily_pnl,
            drawdown_pct=self.drawdown_pct,
            equity_curve_ref=_KEY_FINANCIAL_STATE,
        )


# ---------------------------------------------------------------------------
# Drawdown wake trigger
# ---------------------------------------------------------------------------

_DRAWDOWN_THRESHOLD_RATIO = 0.8


def maybe_trigger_drawdown_wake(
    state: FinancialState,
    max_daily_drawdown_pct: float,
    *,
    on_error: Optional[Callable[[str], None]] = None,
) -> bool:
    """Dispara POST ao shim do webhook se o drawdown cruzar 80% do limite dos dogmas.

    Retorna True se o POST foi disparado com sucesso; False caso contrário (sem exceção).
    Nunca loga BETRADER_WEBHOOK_SECRET.
    """
    webhook_url = os.environ.get("WEBHOOK_PUBLIC_URL")
    webhook_secret = os.environ.get("BETRADER_WEBHOOK_SECRET")

    if not webhook_url or not webhook_secret:
        return False

    threshold = _DRAWDOWN_THRESHOLD_RATIO * max_daily_drawdown_pct
    if threshold <= 0 or state.drawdown_pct < threshold:
        return False

    body = json.dumps(
        {
            "source": "drawdown_monitor",
            "type": "drawdown.threshold",
            "drawdown_pct": state.drawdown_pct,
            "limit_pct": max_daily_drawdown_pct,
        }
    ).encode()

    sig = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Beholder-Signature": f"sha256={sig}",
    }

    try:
        response = httpx.post(webhook_url, content=body, headers=headers, timeout=10.0)
    except httpx.HTTPError:
        if on_error is not None:
            on_error("drawdown_wake_error")
        return False

    if not (200 <= response.status_code < 300):
        if on_error is not None:
            on_error("drawdown_wake_error")
        return False

    return True


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


class Observability:
    """Métricas Prometheus + auditoria de decisões do estrategista.

    Registry injetável para isolamento em testes (cada teste usa um CollectorRegistry
    dedicado; em produção usa o registry global padrão do prometheus_client).
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None) -> None:
        reg = registry or CollectorRegistry()

        # Counters
        self._wins = Counter(
            "strategist_wins_total",
            "Total de trades com PnL positivo",
            registry=reg,
        )
        self._losses = Counter(
            "strategist_losses_total",
            "Total de trades com PnL negativo ou zero",
            registry=reg,
        )
        self._cycles = Counter(
            "strategist_cycles_total",
            "Total de ciclos executados pelo estrategista",
            registry=reg,
        )
        self._proposals_rejected = Counter(
            "strategist_proposals_rejected_total",
            "Total de propostas rejeitadas pelo gate, por motivo",
            ["reason"],
            registry=reg,
        )
        self._errors = Counter(
            "strategist_errors_total",
            "Total de erros por tipo",
            ["type"],
            registry=reg,
        )

        # Gauges
        self._pnl = Gauge(
            "strategist_pnl_usd",
            "PnL acumulado em USDT",
            registry=reg,
        )
        self._equity = Gauge(
            "strategist_equity_usd",
            "Equity atual em USDT (initial_equity + cum_pnl)",
            registry=reg,
        )
        self._max_drawdown = Gauge(
            "strategist_max_drawdown_pct",
            "Drawdown máximo atual em % sobre a equity-curve",
            registry=reg,
        )
        self._win_rate = Gauge(
            "strategist_win_rate",
            "Taxa de acerto (wins / total_trades)",
            registry=reg,
        )

        self._registry = reg

    # ------------------------------------------------------------------
    # Resiliência: restaura métricas do estado persistido pós-restart
    # ------------------------------------------------------------------

    def restore_metrics(self, state: FinancialState) -> None:
        """Re-popula gauges e counters a partir do FinancialState carregado do Redis.

        Chamado no início do processo para restaurar observabilidade após restart
        (requisito bot.md: métricas restauradas a partir do estado persistido).
        """
        self._pnl.set(state.cum_pnl)
        self._equity.set(state.equity)
        self._max_drawdown.set(state.drawdown_pct)
        self._win_rate.set(state.win_rate)

        # Counters não são decrementáveis; incrementamos pela diferença persistida
        if state.wins > 0:
            self._wins.inc(state.wins)
        if state.losses > 0:
            self._losses.inc(state.losses)

    # ------------------------------------------------------------------
    # Registro de eventos do ciclo
    # ------------------------------------------------------------------

    def record_error(self, type: str) -> None:  # noqa: A002
        """Registra erro por tipo descritivo (alvo do on_error do BetraderClient)."""
        self._errors.labels(type=type).inc()

    def record_cycle(self) -> None:
        """Incrementa o contador de ciclos executados (chamado 1x por execução)."""
        self._cycles.inc()

    def record_decision(
        self,
        proposal_summary: dict,  # type: ignore[type-arg]
        gate_ok: bool,
        reason: Optional[str],
        redis: object,  # type: ignore[type-arg]
    ) -> None:
        """Auditoria de decisão: XADD no stream binance:strategist:decisions.

        Campos gravados: timestamp ISO, reasoning (truncado a 500 chars), gate_ok,
        reason (empty string se None). MAXLEN ~1000 aplicado (trimming lazy).
        """
        reasoning = str(proposal_summary.get("reasoning", ""))[:500]
        fields = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "reasoning": reasoning,
            "gate_ok": "true" if gate_ok else "false",
            "reason": reason or "",
        }
        redis.xadd(  # type: ignore[attr-defined]
            _KEY_DECISIONS_STREAM,
            fields,
            maxlen=_DECISIONS_MAXLEN,
            approximate=True,
        )

        # Atualiza contador de propostas rejeitadas
        if not gate_ok and reason:
            self._proposals_rejected.labels(reason=reason).inc()

    # ------------------------------------------------------------------
    # Servidores HTTP
    # ------------------------------------------------------------------

    def start_servers(self, port: int = 9468) -> None:
        """Inicia servidor /metrics (porta port) e /health (porta port+1).

        /metrics: prometheus_client.start_http_server (porta 9468 padrão).
        /health:  servidor HTTP mínimo na porta 9469, retorna 200 {"status":"ok"}.
        """
        start_http_server(port, registry=self._registry)

        health_port = port + 1

        class _HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass  # silencia logs do BaseHTTPRequestHandler

        server = HTTPServer(("0.0.0.0", health_port), _HealthHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
