# Unit tests do Risk Gateway (F2): handle_brief/handle_execute via cache Redis + auth.
#
# Migra os casos de tests/integration/test_cycle.py para o gateway. DI real nos módulos
# internos; mocks SÓ nas fronteiras de I/O: HTTP betrader via respx, Redis via fakeredis.
# As funções puras (handle_brief/handle_execute) são invocadas diretamente, sem socket —
# o handler HTTP fino não é exercido aqui (a auth é testada via require_auth).
# Payloads HTTP reutilizam os shapes reais do doc de verificação (mesmos do test_cycle).
import fakeredis
import httpx
import pytest
import respx

from schemas import ExecutionMode, StrategyProposal

from observability import Observability

from risk_gateway import handle_brief, handle_execute, require_auth

BASE_URL = "https://betrader.example.test"
TOKEN = "bht_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd"
GATEWAY_TOKEN = "gwt_test_0123456789abcdef"
BRIEF_KEY = "binance:strategist:brief:BTCUSDT"


# --- Payloads (shapes reais do doc de verificação) ---


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
    # POST /api/market → {timeframes:[{interval, indicators, candles}]}.
    return {
        "timeframes": [
            {
                "interval": "15m",
                "indicators": {"RSI": 55.0},
                "candles": [
                    {"timestamp": 1781100900000, "open": 60000.0, "high": 60500.0, "low": 59800.0, "close": 60100.0, "volume": 100.0},
                    {"timestamp": 1781101800000, "open": 60100.0, "high": 60300.0, "low": 59900.0, "close": 60050.0, "volume": 90.0},
                ],
            }
        ]
    }


def _balance_future_payload() -> dict:
    return {"assets": {"USDT": {"available": 10000}}, "fiatEstimate": "~USDT 10000.00"}


def _balance_spot_payload() -> dict:
    return {"assets": {"USDT": {"available": 8000}}, "fiatEstimate": "~USDT 8000.00"}


def _futures_payload() -> list[dict]:
    return []


def _mock_brief_endpoints(router: respx.Router) -> None:
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
    router.get(f"{BASE_URL}/api/futures").mock(
        return_value=httpx.Response(200, json=_futures_payload())
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


# --- Fixtures de proposta (schema StrategyProposal) ---


def _proposal_aprovada() -> dict:
    # LIMIT BUY: ref=limit_price=60000, stop=59000 (1.67% > min 0.5%, lado correto),
    # sizing 5% <= teto 10%, leverage 3 <= teto 5, symbol permitido.
    return {
        "reasoning": "RSI oversold; entrada long com stop abaixo do suporte.",
        "entries": [
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "sizing_pct": 5.0,
                "order_type": "LIMIT",
                "limit_price": 60000.0,
                "stop_loss": 59000.0,
                "take_profit": 62000.0,
                "leverage": 3,
            }
        ],
        "automations": [],
        "teardown": [],
    }


def _proposal_leverage_estourado() -> dict:
    p = _proposal_aprovada()
    p["entries"][0]["leverage"] = 20  # > max_leverage 5
    return p


def _proposal_sem_stop() -> dict:
    # stop_loss ausente → ValidationError do schema (SL é obrigatório por construção).
    return {
        "reasoning": "entrada sem stop (inválida).",
        "entries": [
            {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "sizing_pct": 5.0,
                "order_type": "LIMIT",
                "limit_price": 60000.0,
                "leverage": 3,
            }
        ],
    }


def _setup_env(monkeypatch) -> None:
    monkeypatch.setenv("BETRADER_BASE_URL", BASE_URL)
    monkeypatch.setenv("BETRADER_TOKEN", TOKEN)
    monkeypatch.setenv("EXECUTION_MODE", "DRY_RUN")
    monkeypatch.setenv("SYMBOL", "BTCUSDT")
    monkeypatch.setenv("TIMEFRAME", "15m")
    monkeypatch.setenv("GATEWAY_TOKEN", GATEWAY_TOKEN)
    monkeypatch.delenv("EMERGENCY_STOP", raising=False)


@pytest.fixture
def redis_client():
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def obs():
    # Registry dedicado por teste para isolamento das métricas.
    return Observability()


def _run_brief(redis_client, obs) -> dict:
    return handle_brief(
        symbol="BTCUSDT",
        timeframe="15m",
        mode=ExecutionMode.DRY_RUN,
        redis_client=redis_client,
        observability=obs,
    )


def _run_execute(redis_client, obs, payload: dict) -> dict:
    proposal = StrategyProposal.model_validate(payload)
    symbol = proposal.entries[0].symbol if proposal.entries else "BTCUSDT"
    return handle_execute(
        proposal=proposal,
        symbol=symbol,
        redis_client=redis_client,
        observability=obs,
    )


# --- handle_brief: monta o brief, retorna dict e cacheia no Redis ---


@respx.mock
def test_handle_brief_cacheia_no_redis(respx_mock, monkeypatch, redis_client, obs):
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)

    result = _run_brief(redis_client, obs)

    assert result["mode"] == "DRY_RUN"
    assert result["market"]["symbol"] == "BTCUSDT"
    # Brief cacheado no Redis sob a chave por símbolo.
    assert redis_client.get(BRIEF_KEY) is not None


@respx.mock
def test_handle_brief_espelha_no_redis_do_agente(respx_mock, monkeypatch, redis_client, obs):
    """Dual-write: brief vai p/ o risk-redis (autoritativo, o gate relê) E p/ o
    espelho binance-redis (o agente lê redis-first). Conteúdo idêntico nos dois."""
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)
    brief_mirror = fakeredis.FakeStrictRedis(decode_responses=True)

    result = handle_brief(
        symbol="BTCUSDT",
        timeframe="15m",
        mode=ExecutionMode.DRY_RUN,
        redis_client=redis_client,
        observability=obs,
        brief_mirror=brief_mirror,
    )

    assert result["market"]["symbol"] == "BTCUSDT"
    # Autoritativo (risk-redis) e espelho (binance-redis) têm o mesmo brief serializado.
    assert redis_client.get(BRIEF_KEY) is not None
    assert brief_mirror.get(BRIEF_KEY) == redis_client.get(BRIEF_KEY)


# --- Happy path DRY_RUN: brief → execute via cache → ordem+stop → estado/métricas ---


@respx.mock
def test_handle_execute_via_cache_happy_path(respx_mock, monkeypatch, redis_client, obs):
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)
    respx_mock.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload(is_testnet=True))
    )
    respx_mock.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[{"leverage": 3}])
    )
    orders_route = respx_mock.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED"}),
            httpx.Response(200, json={"orderId": 222, "status": "NEW"}),
        ]
    )

    _run_brief(redis_client, obs)
    out = _run_execute(redis_client, obs, _proposal_aprovada())

    assert out["executed"] is True
    assert out["errors"] == []
    assert len(out["orders"]) == 1
    assert out["orders"][0]["entry_order_id"] == 111
    assert out["orders"][0]["stop_order_id"] == 222
    assert orders_route.call_count == 2  # entrada + stop

    # Estado financeiro persistido + decisão auditada + ciclo contabilizado.
    assert redis_client.get("binance:strategist:financial_state") is not None
    assert redis_client.xlen("binance:strategist:decisions") == 1
    assert obs._cycles._value.get() == 1.0


# --- Gate rejeitado: leverage estourado → violations, nenhuma escrita HTTP ---


@respx.mock
def test_gate_rejected_leverage(respx_mock, monkeypatch, redis_client, obs):
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)
    users_route = respx_mock.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload())
    )
    put_route = respx_mock.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[])
    )
    post_orders = respx_mock.post(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json={})
    )

    _run_brief(redis_client, obs)
    out = _run_execute(redis_client, obs, _proposal_leverage_estourado())

    assert out["executed"] is False
    assert out["reason"] == "gate_rejected"
    assert any("leverage" in v for v in out["violations"])
    # Nenhuma call de escrita HTTP.
    assert not users_route.called
    assert not put_route.called
    assert not post_orders.called
    # Decisão de rejeição auditada.
    assert redis_client.xlen("binance:strategist:decisions") == 1


# --- emergency_stop: nenhuma call HTTP, JSON específico ---


@respx.mock
def test_emergency_stop(respx_mock, monkeypatch, redis_client, obs):
    _setup_env(monkeypatch)
    monkeypatch.setenv("EMERGENCY_STOP", "true")
    out = _run_execute(redis_client, obs, _proposal_aprovada())
    assert out == {"executed": False, "reason": "emergency_stop"}
    assert respx_mock.calls.call_count == 0


# --- brief_missing: execute sem brief no cache ---


@respx.mock
def test_brief_missing(respx_mock, monkeypatch, redis_client, obs):
    _setup_env(monkeypatch)
    # Sem handle_brief antes: cache vazio.
    out = _run_execute(redis_client, obs, _proposal_aprovada())
    assert out == {"executed": False, "reason": "brief_missing"}
    assert respx_mock.calls.call_count == 0


# --- Proposta inválida (sem stop_loss) → reason invalid_proposal ---


@respx.mock
def test_proposal_invalida_sem_stop(respx_mock, monkeypatch, redis_client, obs):
    _setup_env(monkeypatch)
    # A validação ocorre no handler HTTP; aqui replicamos o contrato: model_validate
    # da proposta inválida levanta ValidationError, que o handler mapeia para
    # invalid_proposal antes de chegar ao handle_execute.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StrategyProposal.model_validate(_proposal_sem_stop())
    assert respx_mock.calls.call_count == 0


# --- Rollback do client vira errors[]; estado ainda persistido ---


@respx.mock
def test_rollback_vira_errors_com_estado_persistido(
    respx_mock, monkeypatch, redis_client, obs
):
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)
    respx_mock.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload(is_testnet=True))
    )
    respx_mock.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[{"leverage": 3}])
    )
    # Entrada OK, stop falha (500) → rollback via DELETE → entry_rolled_back_no_stop.
    respx_mock.post(f"{BASE_URL}/api/orders").mock(
        side_effect=[
            httpx.Response(200, json={"orderId": 111, "status": "FILLED"}),
            httpx.Response(500, text="stop error"),
        ]
    )
    close_route = respx_mock.delete(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json={"orderId": 333, "status": "FILLED"})
    )

    _run_brief(redis_client, obs)
    out = _run_execute(redis_client, obs, _proposal_aprovada())

    assert out["executed"] is True  # ciclo completou; a entry específica falhou
    assert out["orders"] == []  # entry não confirmou
    assert "entry_rolled_back_no_stop" in out["errors"]
    assert close_route.called  # rollback executado
    # Estado persistido apesar do erro da entry (integridade bot.md).
    assert redis_client.get("binance:strategist:financial_state") is not None


# --- Auth: require_auth aceita o token correto e rejeita errado/ausente ---


def test_require_auth_aceita_token_correto(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", GATEWAY_TOKEN)
    assert require_auth(f"Bearer {GATEWAY_TOKEN}") is True


def test_require_auth_rejeita_token_errado(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", GATEWAY_TOKEN)
    assert require_auth("Bearer wrong-token") is False


def test_require_auth_rejeita_ausente(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", GATEWAY_TOKEN)
    assert require_auth(None) is False
