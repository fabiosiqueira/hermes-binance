# Testes de integração do ciclo do estrategista (brief + execute).
#
# DI real nos módulos internos; mocks SÓ nas fronteiras de I/O: HTTP betrader via
# respx, Redis via fakeredis. main(argv, redis_client=, observability=) é invocado
# diretamente (capsys captura o stdout JSON/path). Os payloads HTTP reutilizam os
# shapes reais do doc de verificação (espelham os do unit test do betrader_client).
import json

import fakeredis
import httpx
import pytest
import respx

from observability import Observability

from strategist_cycle import main

BASE_URL = "https://betrader.example.test"
TOKEN = "bht_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd"


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
    return {"indicators": {"RSI": 55.0}}


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
    router.get(f"{BASE_URL}/api/market").mock(
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


# --- Fixtures de proposta (escritas em workspace, schema StrategyProposal) ---


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
    monkeypatch.delenv("EMERGENCY_STOP", raising=False)


def _write_proposal(tmp_path, payload: dict) -> str:
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture
def redis_client():
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def obs():
    # Registry dedicado por teste para isolamento das métricas.
    return Observability()


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    # workspace/ é relativo ao cwd; isola cada teste num tmp dir.
    monkeypatch.chdir(tmp_path)


def _run_brief(redis_client, obs, capsys) -> str:
    rc = main(["brief"], redis_client=redis_client, observability=obs)
    assert rc == 0
    return capsys.readouterr().out.strip()


# --- Happy path DRY_RUN: brief → proposta aprovada → ordem+stop → estado/métricas ---


@respx.mock
def test_happy_path_dry_run(respx_mock, monkeypatch, redis_client, obs, capsys, tmp_path):
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)
    # Escrita: assert_testnet (GET /api/users), leverage (PUT), entrada+stop (POST orders).
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

    brief_path = _run_brief(redis_client, obs, capsys)
    assert brief_path.endswith("workspace/brief.json")

    proposal_path = _write_proposal(tmp_path, _proposal_aprovada())
    rc = main(["execute", proposal_path], redis_client=redis_client, observability=obs)
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())

    assert out["executed"] is True
    assert out["errors"] == []
    assert len(out["orders"]) == 1
    assert out["orders"][0]["entry_order_id"] == 111
    assert out["orders"][0]["stop_order_id"] == 222
    assert orders_route.call_count == 2  # entrada + stop

    # Estado financeiro persistido no Redis (integridade bot.md).
    assert redis_client.get("binance:strategist:financial_state") is not None
    # Decisão auditada no stream + ciclo contabilizado.
    assert redis_client.xlen("binance:strategist:decisions") == 1
    assert obs._cycles._value.get() == 1.0


# --- Gate rejeitado: leverage estourado → violations, nenhuma escrita HTTP ---


@respx.mock
def test_gate_rejected_leverage(respx_mock, monkeypatch, redis_client, obs, capsys, tmp_path):
    _setup_env(monkeypatch)
    _mock_brief_endpoints(respx_mock)
    # Rotas de ESCRITA: registradas para provar que NÃO são chamadas no gate-reject.
    users_route = respx_mock.get(f"{BASE_URL}/api/users").mock(
        return_value=httpx.Response(200, json=_users_payload())
    )
    put_route = respx_mock.put(f"{BASE_URL}/api/futures/BTCUSDT").mock(
        return_value=httpx.Response(200, json=[])
    )
    post_orders = respx_mock.post(f"{BASE_URL}/api/orders").mock(
        return_value=httpx.Response(200, json={})
    )

    _run_brief(redis_client, obs, capsys)
    proposal_path = _write_proposal(tmp_path, _proposal_leverage_estourado())
    rc = main(["execute", proposal_path], redis_client=redis_client, observability=obs)
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())

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
def test_emergency_stop(respx_mock, monkeypatch, redis_client, obs, capsys, tmp_path):
    _setup_env(monkeypatch)
    monkeypatch.setenv("EMERGENCY_STOP", "true")
    # Qualquer rota: provar que NENHUMA é chamada (respx levantaria em call não-mockada).
    proposal_path = _write_proposal(tmp_path, _proposal_aprovada())
    rc = main(["execute", proposal_path], redis_client=redis_client, observability=obs)
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"executed": False, "reason": "emergency_stop"}
    assert respx_mock.calls.call_count == 0


# --- Proposta inválida (sem stop_loss) → reason invalid_proposal ---


@respx.mock
def test_proposal_invalida_sem_stop(respx_mock, monkeypatch, redis_client, obs, capsys, tmp_path):
    _setup_env(monkeypatch)
    proposal_path = _write_proposal(tmp_path, _proposal_sem_stop())
    rc = main(["execute", proposal_path], redis_client=redis_client, observability=obs)
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["executed"] is False
    assert out["reason"] == "invalid_proposal"
    assert "detail" in out
    # Nenhuma call HTTP (falhou antes de tocar o betrader).
    assert respx_mock.calls.call_count == 0


# --- Rollback do client propaga como errors[]; estado ainda persistido ---


@respx.mock
def test_rollback_vira_errors_com_estado_persistido(
    respx_mock, monkeypatch, redis_client, obs, capsys, tmp_path
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

    _run_brief(redis_client, obs, capsys)
    proposal_path = _write_proposal(tmp_path, _proposal_aprovada())
    rc = main(["execute", proposal_path], redis_client=redis_client, observability=obs)
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())

    assert out["executed"] is True  # ciclo completou; a entry específica falhou
    assert out["orders"] == []  # entry não confirmou
    assert "entry_rolled_back_no_stop" in out["errors"]
    assert close_route.called  # rollback executado
    # Estado persistido apesar do erro da entry (integridade bot.md).
    assert redis_client.get("binance:strategist:financial_state") is not None
