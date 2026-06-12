# E2E de lifecycle DRY_RUN — narrativa de dias de operação do estrategista Hermes.
#
# Cobertura: Ciclo 1 (entrada BTC), Ciclo 2 (gestão com posição aberta), saída com lucro,
# Ciclo 3 com perda, integridade financeira (equity-curve), restart (resiliência bot.md),
# kill switch EMERGENCY_STOP.
#
# Fronteiras mockadas: HTTP betrader via respx, Redis via fakeredis, env via monkeypatch.
# DI real em todos os módulos internos (risk_engine, schemas, observability).
# Asserts: comportamento observável (dict retornado, calls respx, valores fakeredis, gauges).
import json

import fakeredis
import httpx
import pytest
import respx

from observability import FinancialState, Observability
from schemas import ExecutionMode, StrategyProposal
from risk_gateway import handle_brief, handle_execute

BASE_URL = "https://betrader.example.test"
TOKEN = "bht_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd"

# Capital inicial para todo o lifecycle E2E.
INITIAL_EQUITY = 10000.0


# ---------------------------------------------------------------------------
# Payloads HTTP (shapes reais — espelham o doc de verificação)
# ---------------------------------------------------------------------------


def _users_payload(*, is_testnet: bool = True) -> dict:
    return {"rows": [{"name": "hermes", "isTestnet": is_testnet}], "count": 1}


def _monitors_payload() -> dict:
    return {
        "rows": [
            {
                "id": "mon-1",
                "symbol": "BTCUSDT",
                "type": "CANDLES",
                "interval": "15m",
                "isActive": True,
            }
        ],
        "count": 1,
    }


def _indicators_payload() -> dict:
    return {"RSI": {"params": "period", "name": "RSI"}}


def _market_payload() -> dict:
    # POST /api/market → {timeframes:[{interval, indicators, candles}]}; RSI disponível.
    return {
        "timeframes": [
            {
                "interval": "15m",
                "indicators": {"RSI": 45.0},
                "candles": [
                    {"timestamp": 1781100900000, "open": 60000.0, "high": 60500.0, "low": 59800.0, "close": 60100.0, "volume": 100.0},
                    {"timestamp": 1781101800000, "open": 60100.0, "high": 60300.0, "low": 59900.0, "close": 60050.0, "volume": 90.0},
                ],
            }
        ]
    }


def _balance_future_payload(equity: float = INITIAL_EQUITY) -> dict:
    return {
        "assets": {"USDT": {"available": equity}},
        "fiatEstimate": f"~USDT {equity:.2f}",
    }


def _balance_spot_payload(balance: float = 8000.0) -> dict:
    return {"assets": {"USDT": {"available": balance}}, "fiatEstimate": f"~USDT {balance:.2f}"}


def _futures_payload_vazio() -> list:
    """Sem posições abertas (antes da entrada no Ciclo 1)."""
    return []


def _futures_payload_com_posicao() -> list:
    """Posição BTC long aberta (reflete estado após entrada do Ciclo 1)."""
    return [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.00833",   # ~500 USDT a 60000
            "entryPrice": "60000.0",
            "unRealizedProfit": "0.0",
            "leverage": "3",
        }
    ]


# ---------------------------------------------------------------------------
# Helpers de mock
# ---------------------------------------------------------------------------


def _mock_brief_endpoints(router: respx.Router, *, com_posicao: bool = False) -> None:
    """Registra todos os GETs do brief no router respx."""
    router.get(f"{BASE_URL}/api/monitors").mock(
        return_value=httpx.Response(200, json=_monitors_payload())
    )
    router.get(f"{BASE_URL}/api/indicators").mock(
        return_value=httpx.Response(200, json=_indicators_payload())
    )
    router.post(f"{BASE_URL}/api/market").mock(
        return_value=httpx.Response(200, json=_market_payload())
    )
    router.get(
        url=f"{BASE_URL}/api/exchange/balance", params={"isFuture": "true"}
    ).mock(return_value=httpx.Response(200, json=_balance_future_payload()))
    router.get(
        url=f"{BASE_URL}/api/exchange/balance", params={"isFuture": "false"}
    ).mock(return_value=httpx.Response(200, json=_balance_spot_payload()))
    futures_resp = _futures_payload_com_posicao() if com_posicao else _futures_payload_vazio()
    router.get(f"{BASE_URL}/api/futures").mock(
        return_value=httpx.Response(200, json=futures_resp)
    )
    router.get(f"{BASE_URL}/api/beholder/memory").mock(
        return_value=httpx.Response(200, json={})
    )
    router.get(f"{BASE_URL}/api/automations/indexes").mock(
        return_value=httpx.Response(200, json={})
    )
    router.get(f"{BASE_URL}/api/automations").mock(
        return_value=httpx.Response(200, json=[])
    )
    router.get(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json={"rows": [], "count": 0})
    )


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


def _proposal_entrada_btc() -> dict:
    """Proposta de entrada: BTCUSDT BUY LIMIT, stop válido (1.67% > min 0.5%)."""
    return {
        "reasoning": "RSI oversold dia 1; entrada long BTCUSDT com stop abaixo do suporte.",
        "entries": [
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "sizing_pct": 5.0,
                "order_type": "LIMIT",
                "limit_price": 60000.0,
                "stop_loss": 59000.0,
                "take_profit": 63000.0,
                "leverage": 3,
            }
        ],
        "automations": [],
        "teardown": [],
    }


def _proposal_gestao_trailing() -> dict:
    """Proposta de gestão: automation trailing/saída sem entrada nova."""
    return {
        "reasoning": "Posição aberta; instalando automation de trailing stop.",
        "entries": [],
        "automations": [
            {
                "name": "btc_trailing_stop",
                "condition": "MEMORY['BTCUSDT:RSI_period'] > 70",
                "action": {"type": "ORDER", "side": "SELL", "reduceOnly": True},
            }
        ],
        "teardown": [],
    }


def _proposal_sem_acao() -> dict:
    """Proposta vazia (Ciclo 3: sem entrada após perda — prudência)."""
    return {
        "reasoning": "Perda registrada; aguardando setup mais claro. Sem entrada.",
        "entries": [],
        "automations": [],
        "teardown": [],
    }


# ---------------------------------------------------------------------------
# Helpers de setup
# ---------------------------------------------------------------------------


def _setup_env(monkeypatch) -> None:
    monkeypatch.setenv("BETRADER_BASE_URL", BASE_URL)
    monkeypatch.setenv("BETRADER_TOKEN", TOKEN)
    monkeypatch.setenv("EXECUTION_MODE", "DRY_RUN")
    monkeypatch.setenv("SYMBOL", "BTCUSDT")
    monkeypatch.setenv("TIMEFRAME", "15m")
    monkeypatch.setenv("INITIAL_EQUITY", str(INITIAL_EQUITY))
    monkeypatch.delenv("EMERGENCY_STOP", raising=False)


def _run_brief(redis_client, obs) -> dict:
    """Chama handle_brief diretamente e retorna o dict do brief."""
    return handle_brief(
        symbol="BTCUSDT",
        timeframe="15m",
        mode=ExecutionMode.DRY_RUN,
        redis_client=redis_client,
        observability=obs,
    )


def _run_execute(redis_client, obs, payload: dict) -> dict:
    """Valida a proposta e chama handle_execute diretamente; retorna o dict de resultado."""
    proposal = StrategyProposal.model_validate(payload)
    symbol = proposal.entries[0].symbol if proposal.entries else "BTCUSDT"
    return handle_execute(
        proposal=proposal,
        symbol=symbol,
        redis_client=redis_client,
        observability=obs,
    )


# ---------------------------------------------------------------------------
# Fixture: fakeredis compartilhado pelo lifecycle inteiro
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_lc():
    """Redis isolado para o lifecycle (compartilhado entre todos os ciclos do cenário)."""
    return fakeredis.FakeStrictRedis(decode_responses=True)


# ---------------------------------------------------------------------------
# Teste principal: lifecycle completo DRY_RUN
# ---------------------------------------------------------------------------


@respx.mock
def test_lifecycle_dry_run_completo(respx_mock, monkeypatch, redis_lc):
    """Narrativa: dias de operação em DRY_RUN.

    Ciclo 1: brief (sem posição) → entrada BTCUSDT aprovada → entry+stop emitidos.
    Ciclo 2: brief (com posição aberta) → automation de gestão instalada.
    Saída com lucro: record_trade(+300) → win contabilizado, equity sobe.
    Ciclo 3 com perda: record_trade(-500) → equity cai; integridade financeira.
    Restart: novo FinancialState.load + restore_metrics → estado/métricas idênticos.
    """
    _setup_env(monkeypatch)

    # =========================================================================
    # CICLO 1: brief sem posição → entrada BTCUSDT → entry+stop OK
    # =========================================================================

    _mock_brief_endpoints(respx_mock, com_posicao=False)
    respx_mock.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload(is_testnet=True))
    )
    respx_mock.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[{"leverage": 3}])
    )
    orders_c1 = respx_mock.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            # (c) entrada LIMIT BUY
            httpx.Response(200, json={"orderId": 1001, "status": "FILLED"}),
            # (d) stop STOP_MARKET reduceOnly=true → NEW (confirmado)
            httpx.Response(200, json={"orderId": 1002, "status": "NEW"}),
        ]
    )

    obs_c1 = Observability()
    brief_c1 = _run_brief(redis_lc, obs_c1)

    # brief retorna o dict com as chaves esperadas.
    assert brief_c1["mode"] == "DRY_RUN", "Ciclo 1: brief mode correto"
    assert brief_c1["market"]["symbol"] == "BTCUSDT", "Ciclo 1: brief symbol correto"

    out_c1 = _run_execute(redis_lc, obs_c1, _proposal_entrada_btc())

    # --- Asserts Ciclo 1 ---
    assert out_c1["executed"] is True, "Ciclo 1: execução deve ter sido confirmada"
    assert out_c1["errors"] == [], "Ciclo 1: sem erros"
    assert len(out_c1["orders"]) == 1, "Ciclo 1: 1 entry resultou em 1 registro de ordem"

    ordem_c1 = out_c1["orders"][0]
    assert ordem_c1["entry_order_id"] == 1001, "Ciclo 1: id da entrada correto"
    assert ordem_c1["stop_order_id"] == 1002, "Ciclo 1: id do stop correto"
    assert orders_c1.call_count == 2, "Ciclo 1: 2 POSTs (entrada + stop)"

    # Valida payload do stop: deve ter reduceOnly=True (campo crítico do contrato).
    stop_request = orders_c1.calls[1].request
    stop_body = json.loads(stop_request.content)
    assert stop_body.get("reduceOnly") is True, "Ciclo 1: stop deve ser reduceOnly=true"
    assert stop_body.get("type") == "STOP_MARKET", "Ciclo 1: stop deve ser STOP_MARKET"
    assert stop_body.get("side") == "SELL", "Ciclo 1: stop de BUY deve ser SELL"

    # Valida payload da entrada LIMIT.
    entry_request = orders_c1.calls[0].request
    entry_body = json.loads(entry_request.content)
    assert entry_body.get("type") == "LIMIT", "Ciclo 1: entrada deve ser LIMIT"
    assert entry_body.get("side") == "BUY", "Ciclo 1: lado da entrada correto"
    assert "limitPrice" in entry_body, "Ciclo 1: entrada LIMIT deve ter limitPrice"

    # Estado financeiro persistido no Redis após Ciclo 1.
    assert redis_lc.get("binance:strategist:financial_state") is not None, (
        "Ciclo 1: estado financeiro deve estar persistido no Redis"
    )
    # Decisão auditada no stream.
    assert redis_lc.xlen("binance:strategist:decisions") == 1, (
        "Ciclo 1: 1 decisão auditada no stream"
    )
    # Ciclo contabilizado.
    assert obs_c1._cycles._value.get() == 1.0, "Ciclo 1: contador de ciclos = 1"

    # =========================================================================
    # CICLO 2: brief com posição aberta → automation de gestão instalada
    # =========================================================================

    # Reseta o router para o Ciclo 2 (posição aberta no brief).
    respx_mock.reset()
    _mock_brief_endpoints(respx_mock, com_posicao=True)
    # Não há escrita de ordens no Ciclo 2 (só automation).
    automation_post = respx_mock.post(f"{BASE_URL}/api/automations").mock(
        return_value=httpx.Response(200, json={"id": "auto-42"})
    )
    automation_start = respx_mock.post(f"{BASE_URL}/api/automations/auto-42/start").mock(
        return_value=httpx.Response(200, json={})
    )

    obs_c2 = Observability()
    brief_c2 = _run_brief(redis_lc, obs_c2)

    # Brief do Ciclo 2 reflete posição aberta: valida via dict retornado e via Redis.
    assert len(brief_c2["portfolio"]["positions"]) == 1, (
        "Ciclo 2: brief deve refletir posição aberta"
    )
    assert brief_c2["portfolio"]["positions"][0]["symbol"] == "BTCUSDT"

    # Validação adicional via Redis (chave de cache do brief).
    brief_cached = json.loads(redis_lc.get("binance:strategist:brief:BTCUSDT"))
    assert len(brief_cached["portfolio"]["positions"]) == 1, (
        "Ciclo 2: brief cacheado no Redis deve refletir posição aberta"
    )

    out_c2 = _run_execute(redis_lc, obs_c2, _proposal_gestao_trailing())

    # --- Asserts Ciclo 2 ---
    assert out_c2["executed"] is True, "Ciclo 2: execução confirmada"
    assert out_c2["errors"] == [], "Ciclo 2: sem erros"
    assert out_c2["orders"] == [], "Ciclo 2: sem entradas novas"
    assert "auto-42" in out_c2["automations"], "Ciclo 2: automation instalada com id correto"
    assert automation_post.called, "Ciclo 2: POST /api/automations chamado"
    assert automation_start.called, "Ciclo 2: POST .../start chamado"

    # Stream cresceu em 1 decisão.
    assert redis_lc.xlen("binance:strategist:decisions") == 2, (
        "Ciclo 2: 2 decisões auditadas no stream"
    )

    # =========================================================================
    # SAÍDA COM LUCRO: record_trade(+300) → win contabilizado, equity sobe
    # =========================================================================

    state_apos_c2 = FinancialState.load(redis_lc)
    state_apos_c2.record_trade(+300.0)
    state_apos_c2.persist(redis_lc)

    state_win = FinancialState.load(redis_lc)
    assert state_win.wins == 1, "Saída com lucro: wins = 1"
    assert state_win.losses == 0, "Saída com lucro: losses = 0"
    assert state_win.cum_pnl == pytest.approx(300.0), "Saída com lucro: cum_pnl = +300"
    assert state_win.equity == pytest.approx(INITIAL_EQUITY + 300.0), (
        "Saída com lucro: equity == initial_equity + cum_pnl"
    )
    assert state_win.peak_equity == pytest.approx(INITIAL_EQUITY + 300.0), (
        "Saída com lucro: peak_equity atualizado para nova máxima"
    )
    assert state_win.drawdown_pct == pytest.approx(0.0), (
        "Saída com lucro: sem drawdown (equity == peak)"
    )

    # =========================================================================
    # CICLO 3 COM PERDA: record_trade(-500) → equity cai; integridade financeira
    # =========================================================================

    # Executa Ciclo 3: proposta sem ação (prudência após vitória).
    respx_mock.reset()
    _mock_brief_endpoints(respx_mock, com_posicao=False)

    obs_c3 = Observability()
    _run_brief(redis_lc, obs_c3)
    out_c3 = _run_execute(redis_lc, obs_c3, _proposal_sem_acao())
    assert out_c3["executed"] is True, "Ciclo 3: ciclo sem ação executa com sucesso"
    assert out_c3["orders"] == [], "Ciclo 3: sem ordens (proposta vazia)"

    # Registra perda de 500 USDT (simula fechamento de posição com loss).
    state_c3 = FinancialState.load(redis_lc)
    state_c3.record_trade(-500.0)
    state_c3.persist(redis_lc)

    state_final = FinancialState.load(redis_lc)

    # --- Asserts de integridade financeira (4 asserts) ---

    # 1. Invariante equity-curve: equity == initial_equity + cum_pnl
    expected_cum_pnl = 300.0 - 500.0  # = -200.0
    assert state_final.equity == pytest.approx(INITIAL_EQUITY + expected_cum_pnl), (
        "Integridade: equity == initial_equity + cum_pnl "
        f"({INITIAL_EQUITY} + {expected_cum_pnl} = {INITIAL_EQUITY + expected_cum_pnl})"
    )

    # 2. cum_pnl correto
    assert state_final.cum_pnl == pytest.approx(expected_cum_pnl), (
        f"Integridade: cum_pnl = {expected_cum_pnl}"
    )

    # 3. max_drawdown calculado sobre equity-curve:
    #    peak = 10300 (após win), equity_final = 9800 (após loss)
    #    drawdown = (10300 - 9800) / 10300 * 100 ≈ 4.8544%
    peak_esperado = INITIAL_EQUITY + 300.0   # 10300.0
    equity_final_esperada = INITIAL_EQUITY + expected_cum_pnl  # 9800.0
    max_dd_esperado = (peak_esperado - equity_final_esperada) / peak_esperado * 100
    assert state_final.drawdown_pct == pytest.approx(max_dd_esperado, rel=1e-4), (
        f"Integridade: max_drawdown calculado sobre equity-curve ≈ {max_dd_esperado:.4f}%"
    )

    # 4. win_rate correto: 1 win, 1 loss → 50%
    assert state_final.wins == 1, "Integridade: 1 win registrado"
    assert state_final.losses == 1, "Integridade: 1 loss registrado"
    assert state_final.win_rate == pytest.approx(0.5), "Integridade: win_rate = 0.5"

    # =========================================================================
    # RESTART: novo FinancialState.load + restore_metrics → resiliência bot.md
    # =========================================================================

    # Simula restart: novo Observability (nova instância de Prometheus registry)
    # carregado do MESMO fakeredis (estado já persistido).
    obs_restart = Observability()
    state_restart = FinancialState.load(redis_lc)
    obs_restart.restore_metrics(state_restart)

    # Estado idêntico ao pré-restart.
    assert state_restart.initial_equity == pytest.approx(INITIAL_EQUITY), (
        "Restart: initial_equity preservado"
    )
    assert state_restart.cum_pnl == pytest.approx(expected_cum_pnl), (
        "Restart: cum_pnl preservado"
    )
    assert state_restart.equity == pytest.approx(INITIAL_EQUITY + expected_cum_pnl), (
        "Restart: equity preservada"
    )
    assert state_restart.peak_equity == pytest.approx(peak_esperado), (
        "Restart: peak_equity preservado"
    )
    assert state_restart.wins == 1, "Restart: wins preservados"
    assert state_restart.losses == 1, "Restart: losses preservados"
    assert state_restart.win_rate == pytest.approx(0.5), "Restart: win_rate preservado"

    # Métricas restauradas nos gauges (comportamento observável do registry).
    assert obs_restart._pnl._value.get() == pytest.approx(expected_cum_pnl), (
        "Restart: gauge pnl restaurado"
    )
    assert obs_restart._equity._value.get() == pytest.approx(INITIAL_EQUITY + expected_cum_pnl), (
        "Restart: gauge equity restaurado"
    )
    assert obs_restart._max_drawdown._value.get() == pytest.approx(max_dd_esperado, rel=1e-4), (
        "Restart: gauge max_drawdown restaurado"
    )
    assert obs_restart._win_rate._value.get() == pytest.approx(0.5), (
        "Restart: gauge win_rate restaurado"
    )
    # Counters restaurados (wins + losses).
    assert obs_restart._wins._value.get() == pytest.approx(1.0), (
        "Restart: counter wins restaurado"
    )
    assert obs_restart._losses._value.get() == pytest.approx(1.0), (
        "Restart: counter losses restaurado"
    )


# ---------------------------------------------------------------------------
# Kill switch: EMERGENCY_STOP=true → sem call HTTP de escrita
# ---------------------------------------------------------------------------


@respx.mock
def test_kill_switch_emergency_stop(respx_mock, monkeypatch, redis_lc):
    """EMERGENCY_STOP=true → handle_execute retorna {executed:false, reason:emergency_stop}
    sem NENHUMA call HTTP (kill switch é verificado antes de qualquer I/O).
    """
    _setup_env(monkeypatch)
    monkeypatch.setenv("EMERGENCY_STOP", "true")

    # Registra rotas para provar que NÃO são chamadas.
    users_route = respx_mock.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload())
    )
    put_route = respx_mock.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[])
    )
    post_orders = respx_mock.post(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json={})
    )
    post_automations = respx_mock.post(f"{BASE_URL}/api/automations").mock(
        return_value=httpx.Response(200, json={})
    )

    obs = Observability()
    proposal = StrategyProposal.model_validate(_proposal_entrada_btc())
    out = handle_execute(
        proposal=proposal,
        symbol="BTCUSDT",
        redis_client=redis_lc,
        observability=obs,
    )

    assert out == {"executed": False, "reason": "emergency_stop"}, (
        "Kill switch: dict exato {executed:false, reason:emergency_stop}"
    )

    # Nenhuma call HTTP executada.
    assert not users_route.called, "Kill switch: GET /api/users não deve ser chamado"
    assert not put_route.called, "Kill switch: PUT /api/futures não deve ser chamado"
    assert not post_orders.called, "Kill switch: POST /api/orders não deve ser chamado"
    assert not post_automations.called, "Kill switch: POST /api/automations não deve ser chamado"

    # Total de calls HTTP: zero.
    assert respx_mock.calls.call_count == 0, "Kill switch: zero calls HTTP"
