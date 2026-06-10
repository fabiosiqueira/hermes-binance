# Unit tests do módulo de observabilidade e estado financeiro persistido.
# Fronteira Redis mockada via fakeredis; nada mais é mockado.
import datetime
import hashlib
import hmac
import json
import os

import fakeredis
import httpx
import pytest
import respx
from prometheus_client import CollectorRegistry

from observability import FinancialState, Observability, maybe_trigger_drawdown_wake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def redis_client() -> fakeredis.FakeRedis:
    """FakeRedis isolado por teste (servidor dedicado)."""
    server = fakeredis.FakeServer()
    return fakeredis.FakeRedis(server=server, decode_responses=True)


@pytest.fixture()
def registry() -> CollectorRegistry:
    """Registry Prometheus isolado por teste."""
    return CollectorRegistry()


@pytest.fixture()
def obs(registry: CollectorRegistry) -> Observability:
    return Observability(registry=registry)


# ---------------------------------------------------------------------------
# FinancialState: persistência sobrevive "restart"
# ---------------------------------------------------------------------------


def test_persist_e_load_restauram_valores(redis_client: fakeredis.FakeRedis) -> None:
    """Estado persiste no Redis e é carregado idêntico num novo FinancialState."""
    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(200.0)
    state.record_trade(-50.0)
    state.persist(redis_client)

    # Simula restart: novo objeto carrega do Redis
    restored = FinancialState.load(redis_client, initial_equity_env="99999")
    assert restored.initial_equity == 10000.0
    assert restored.cum_pnl == pytest.approx(150.0)
    assert restored.wins == 1
    assert restored.losses == 1


def test_initial_equity_nao_relida_do_env_quando_ja_persistida(
    redis_client: fakeredis.FakeRedis,
) -> None:
    """initial_equity vem SEMPRE do Redis após primeira persistência; env é ignorado."""
    state = FinancialState.load(redis_client, initial_equity_env="5000")
    state.persist(redis_client)

    # Muda o env (simula restart com env diferente) — deve ser ignorado
    reloaded = FinancialState.load(redis_client, initial_equity_env="99999")
    assert reloaded.initial_equity == 5000.0


# ---------------------------------------------------------------------------
# FinancialState: equity-curve e MaxDD
# ---------------------------------------------------------------------------


def test_equity_calculada_sobre_equity_curve(redis_client: fakeredis.FakeRedis) -> None:
    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(500.0)
    assert state.equity == pytest.approx(10500.0)


def test_max_drawdown_calculado_sobre_equity_curve(
    redis_client: fakeredis.FakeRedis,
) -> None:
    """Sequência +100, -50, -100: peak=10100, equity=9950, drawdown=(10100-9950)/10100."""
    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(100.0)   # equity=10100, peak=10100
    state.record_trade(-50.0)   # equity=10050, peak=10100
    state.record_trade(-100.0)  # equity=9950,  peak=10100
    expected_dd = (10100.0 - 9950.0) / 10100.0 * 100
    assert state.drawdown_pct == pytest.approx(expected_dd)


def test_drawdown_zero_quando_peak_zero() -> None:
    """Sem trades: peak_equity=initial_equity; drawdown deve ser 0 (sem divisão por zero)."""
    # Com initial_equity=10000, peak=10000, equity=10000 → drawdown=0
    state = FinancialState(initial_equity=10000.0)
    assert state.drawdown_pct == pytest.approx(0.0)


def test_drawdown_zero_sem_estado(redis_client: fakeredis.FakeRedis) -> None:
    """Sem trades gravados, drawdown é 0."""
    state = FinancialState.load(redis_client, initial_equity_env="1000")
    assert state.drawdown_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# FinancialState: daily_pnl com clock injetável
# ---------------------------------------------------------------------------


def test_daily_pnl_reseta_na_virada_de_dia(redis_client: fakeredis.FakeRedis) -> None:
    """daily_pnl reseta quando a data UTC muda; clock é injetável (não mockado global)."""
    dia1 = datetime.date(2026, 6, 9)
    dia2 = datetime.date(2026, 6, 10)

    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(300.0, trade_date=dia1)
    assert state.daily_pnl == pytest.approx(300.0)
    assert state.daily_date == dia1

    # Virada de dia: daily_pnl zera
    state.record_trade(-100.0, trade_date=dia2)
    assert state.daily_pnl == pytest.approx(-100.0)
    assert state.daily_date == dia2

    # cum_pnl continua acumulando
    assert state.cum_pnl == pytest.approx(200.0)


def test_daily_pnl_acumula_no_mesmo_dia(redis_client: fakeredis.FakeRedis) -> None:
    dia = datetime.date(2026, 6, 9)
    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(100.0, trade_date=dia)
    state.record_trade(50.0, trade_date=dia)
    assert state.daily_pnl == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# FinancialState: win_rate sem divisão por zero
# ---------------------------------------------------------------------------


def test_win_rate_zero_trades_nao_divide_por_zero(
    redis_client: fakeredis.FakeRedis,
) -> None:
    state = FinancialState.load(redis_client, initial_equity_env="10000")
    assert state.win_rate == pytest.approx(0.0)


def test_win_rate_calculado_corretamente(redis_client: fakeredis.FakeRedis) -> None:
    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(100.0)
    state.record_trade(-50.0)
    state.record_trade(200.0)
    # 2 wins, 1 loss → 2/3
    assert state.win_rate == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# FinancialState: to_risk_state
# ---------------------------------------------------------------------------


def test_to_risk_state_retorna_riskstate(redis_client: fakeredis.FakeRedis) -> None:
    from schemas import RiskState

    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(50.0)
    rs = state.to_risk_state()
    assert isinstance(rs, RiskState)
    assert rs.daily_pnl == pytest.approx(50.0)
    assert rs.equity_curve_ref == "binance:strategist:financial_state"


# ---------------------------------------------------------------------------
# Observability: restore_metrics re-popula gauges e counters
# ---------------------------------------------------------------------------


def test_restore_metrics_popula_gauges(
    redis_client: fakeredis.FakeRedis,
    registry: CollectorRegistry,
) -> None:
    obs = Observability(registry=registry)

    state = FinancialState.load(redis_client, initial_equity_env="10000")
    state.record_trade(100.0)
    state.record_trade(-30.0)
    state.record_trade(200.0)
    # wins=2, losses=1, cum_pnl=270, equity=10270

    obs.restore_metrics(state)

    samples = {s.name: s.value for s in registry.collect() for s in s.samples}
    assert samples["strategist_pnl_usd"] == pytest.approx(270.0)
    assert samples["strategist_equity_usd"] == pytest.approx(10270.0)
    assert samples["strategist_wins_total"] == pytest.approx(2.0)
    assert samples["strategist_losses_total"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Observability: record_decision escreve no stream Redis
# ---------------------------------------------------------------------------


def test_record_decision_escreve_no_stream(
    redis_client: fakeredis.FakeRedis,
    obs: Observability,
) -> None:
    proposal_summary = {"reasoning": "RSI saiu de sobrevenda", "entries": 1}
    obs.record_decision(
        proposal_summary=proposal_summary,
        gate_ok=True,
        reason=None,
        redis=redis_client,
    )
    entries = redis_client.xrange("binance:strategist:decisions")
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields["gate_ok"] == "true"
    assert "reasoning" in fields


def test_record_decision_gate_rejeitado(
    redis_client: fakeredis.FakeRedis,
    obs: Observability,
) -> None:
    obs.record_decision(
        proposal_summary={"reasoning": "proposta perigosa"},
        gate_ok=False,
        reason="leverage_exceeded",
        redis=redis_client,
    )
    entries = redis_client.xrange("binance:strategist:decisions")
    assert len(entries) == 1
    _, fields = entries[0]
    assert fields["gate_ok"] == "false"
    assert fields["reason"] == "leverage_exceeded"


def test_record_decision_stream_limita_maxlen(
    redis_client: fakeredis.FakeRedis,
    obs: Observability,
) -> None:
    """Stream não deve crescer indefinidamente; MAXLEN ~1000 aplicado."""
    for i in range(1010):
        obs.record_decision(
            proposal_summary={"reasoning": f"ciclo {i}"},
            gate_ok=True,
            reason=None,
            redis=redis_client,
        )
    length = redis_client.xlen("binance:strategist:decisions")
    # MAXLEN ~ é aproximado (trimming lazy); garantimos que fica abaixo de 1100
    assert length <= 1100


# ---------------------------------------------------------------------------
# Observability: record_error e record_cycle contabilizados
# ---------------------------------------------------------------------------


def test_record_error_incrementa_counter(registry: CollectorRegistry) -> None:
    obs = Observability(registry=registry)
    obs.record_error("redis_timeout")
    obs.record_error("redis_timeout")
    obs.record_error("http_error")

    samples = {
        (s.name, s.labels.get("type", "")): s.value
        for s in registry.collect()
        for s in s.samples
    }
    assert samples[("strategist_errors_total", "redis_timeout")] == 2.0
    assert samples[("strategist_errors_total", "http_error")] == 1.0


def test_record_cycle_incrementa_counter(registry: CollectorRegistry) -> None:
    obs = Observability(registry=registry)
    obs.record_cycle()
    obs.record_cycle()

    samples = {s.name: s.value for s in registry.collect() for s in s.samples}
    assert samples["strategist_cycles_total"] == 2.0


# ---------------------------------------------------------------------------
# maybe_trigger_drawdown_wake
# ---------------------------------------------------------------------------

_WEBHOOK_URL = "https://shim.example.test/webhook"
_WEBHOOK_SECRET = "testsecret"
_MAX_DD_PCT = 5.0  # dogmas.max_daily_drawdown_pct = 5 %


def _state_with_drawdown(drawdown_pct_target: float) -> FinancialState:
    """Constrói FinancialState com drawdown_pct aproximadamente igual ao alvo."""
    # peak=10000, equity = peak * (1 - drawdown_pct_target/100)
    initial = 10000.0
    loss = initial * drawdown_pct_target / 100.0
    state = FinancialState(initial_equity=initial, peak_equity=initial)
    state.cum_pnl = -loss
    return state


@respx.mock
def test_drawdown_breach_dispara_post_com_assinatura(monkeypatch: pytest.MonkeyPatch) -> None:
    """DD ≥ 80% do limite → POST disparado com X-Beholder-Signature válido; retorna True."""
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", _WEBHOOK_URL)
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    # drawdown_pct = 4.2 % → threshold = 0.8 * 5.0 = 4.0 % → breach
    state = _state_with_drawdown(4.2)

    route = respx.post(_WEBHOOK_URL).mock(return_value=httpx.Response(200))

    result = maybe_trigger_drawdown_wake(state, _MAX_DD_PCT)

    assert result is True
    assert route.called

    # Valida assinatura HMAC-SHA256
    sent_request = route.calls[0].request
    raw_body = sent_request.content
    expected_sig = hmac.new(_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    sig_header = sent_request.headers["x-beholder-signature"]
    assert sig_header == f"sha256={expected_sig}"

    # Valida payload
    payload = json.loads(raw_body)
    assert payload["source"] == "drawdown_monitor"
    assert payload["type"] == "drawdown.threshold"
    assert payload["drawdown_pct"] == pytest.approx(state.drawdown_pct)
    assert payload["limit_pct"] == pytest.approx(_MAX_DD_PCT)


@respx.mock
def test_drawdown_abaixo_threshold_nao_dispara_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """DD < 80% do limite → nenhum POST, retorna False."""
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", _WEBHOOK_URL)
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    # drawdown_pct = 3.9 % → threshold = 4.0 % → sem breach
    state = _state_with_drawdown(3.9)

    route = respx.post(_WEBHOOK_URL).mock(return_value=httpx.Response(200))

    result = maybe_trigger_drawdown_wake(state, _MAX_DD_PCT)

    assert result is False
    assert not route.called


def test_drawdown_env_ausente_nao_dispara(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env WEBHOOK_PUBLIC_URL ou BETRADER_WEBHOOK_SECRET ausente → no-op, retorna False."""
    monkeypatch.delenv("WEBHOOK_PUBLIC_URL", raising=False)
    monkeypatch.delenv("BETRADER_WEBHOOK_SECRET", raising=False)

    state = _state_with_drawdown(5.0)  # 100% do limite
    result = maybe_trigger_drawdown_wake(state, _MAX_DD_PCT)
    assert result is False


@respx.mock
def test_drawdown_falha_de_rede_chama_on_error_sem_propagar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha de rede no POST → on_error chamado com 'drawdown_wake_error'; sem exceção; False."""
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", _WEBHOOK_URL)
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    state = _state_with_drawdown(4.5)  # breach

    respx.post(_WEBHOOK_URL).mock(side_effect=httpx.ConnectError("timeout"))

    errors: list[str] = []
    result = maybe_trigger_drawdown_wake(state, _MAX_DD_PCT, on_error=errors.append)

    assert result is False
    assert errors == ["drawdown_wake_error"]


@respx.mock
def test_drawdown_resposta_nao_2xx_chama_on_error_e_retorna_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shim/engine responde não-2xx → on_error chamado com 'drawdown_wake_error'; False."""
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", _WEBHOOK_URL)
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", _WEBHOOK_SECRET)

    state = _state_with_drawdown(4.5)  # breach

    respx.post(_WEBHOOK_URL).mock(return_value=httpx.Response(500))

    errors: list[str] = []
    result = maybe_trigger_drawdown_wake(state, _MAX_DD_PCT, on_error=errors.append)

    assert result is False
    assert errors == ["drawdown_wake_error"]
