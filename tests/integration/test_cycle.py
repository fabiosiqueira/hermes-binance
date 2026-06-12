# Testes de integração do ciclo do estrategista (thin-client HTTP do Risk Gateway).
#
# Handoff 100% Redis (sem filesystem). Mocks SÓ nas fronteiras de I/O: gateway HTTP via
# respx + httpx.Client injetado; Redis do agente via fakeredis injetado.
# main(argv, http_client=, redis_client=) é invocado diretamente; capsys captura stdout.
import json

import fakeredis
import httpx
import pytest
import respx

from strategist_cycle import main

GATEWAY_URL = "http://risk-gateway.test:8647"
GATEWAY_TOKEN = "gw_test_token_abc123"
BRIEF_KEY = "binance:strategist:brief:BTCUSDT"
PROPOSAL_KEY = "binance:strategist:proposal:BTCUSDT"


# --- Payloads de exemplo ---


def _brief_payload() -> dict:
    return {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "mode": "DRY_RUN",
        "market": {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "candles": [],
            "indicators": {"RSI": 55.0},
        },
        "portfolio": {
            "equity": 10000.0,
            "positions": [],
            "automations": [],
            "spot_balance": 8000.0,
            "beholder_memory": {},
            "automation_indexes": {},
        },
        "risk": {
            "daily_pnl": 0.0,
            "drawdown_pct": 0.0,
            "equity_curve_ref": "binance:strategist:financial_state",
        },
    }


def _proposal_payload() -> dict:
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


def _proposal_sem_stop() -> dict:
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


# --- Helpers ---


def _setup_env(monkeypatch) -> None:
    monkeypatch.setenv("GATEWAY_URL", GATEWAY_URL)
    monkeypatch.setenv("GATEWAY_TOKEN", GATEWAY_TOKEN)
    monkeypatch.setenv("EXECUTION_MODE", "DRY_RUN")
    monkeypatch.setenv("SYMBOL", "BTCUSDT")
    monkeypatch.setenv("TIMEFRAME", "15m")
    monkeypatch.delenv("BETRADER_TOKEN", raising=False)


@pytest.fixture
def redis_client():
    return fakeredis.FakeStrictRedis(decode_responses=True)


def _seed_proposal(redis_client, payload: dict) -> str:
    """Grava a proposta no Redis do agente e devolve a referência redis:KEY."""
    redis_client.set(PROPOSAL_KEY, json.dumps(payload))
    return f"redis:{PROPOSAL_KEY}"


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    # Isola cwd: o handoff é 100% Redis, então nada deve ser escrito em disco aqui.
    monkeypatch.chdir(tmp_path)


# --- Testes ---


def test_brief_imprime_chave_redis_sem_arquivo(monkeypatch, capsys, tmp_path, redis_client):
    """(a) brief → POST /brief; stdout = CHAVE Redis do brief; nenhum arquivo escrito."""
    _setup_env(monkeypatch)

    with respx.mock(base_url=GATEWAY_URL) as mock:
        mock.post("/brief").mock(return_value=httpx.Response(200, json=_brief_payload()))

        with httpx.Client() as client:
            rc = main(["brief"], http_client=client, redis_client=redis_client)

    assert rc == 0
    assert capsys.readouterr().out.strip() == BRIEF_KEY
    # Handoff redis-first: o thin-client não escreve arquivo algum.
    assert list(tmp_path.iterdir()) == []


def test_execute_happy_path_repassa_gateway(monkeypatch, capsys, redis_client):
    """(b) execute happy → lê proposal do Redis; stdout == JSON do gateway; corpo enviado == proposal."""
    _setup_env(monkeypatch)
    gateway_response = {
        "executed": True,
        "orders": [{"entry_order_id": 111, "stop_order_id": 222}],
        "automations": [],
        "errors": [],
    }
    proposal_ref = _seed_proposal(redis_client, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        execute_route = mock.post("/execute").mock(
            return_value=httpx.Response(200, json=gateway_response)
        )

        with httpx.Client() as client:
            rc = main(["execute", proposal_ref], http_client=client, redis_client=redis_client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == gateway_response

    # Corpo enviado ao gateway deve ser o JSON da proposta (lido do Redis).
    sent_body = json.loads(execute_route.calls[0].request.content)
    assert sent_body == _proposal_payload()


def test_execute_gate_rejected_repassa_fielmente(monkeypatch, capsys, redis_client):
    """(c) execute gate_rejected → gateway retorna violations; cliente repassa sem alteração."""
    _setup_env(monkeypatch)
    gateway_response = {
        "executed": False,
        "reason": "gate_rejected",
        "violations": ["leverage 20 > max_leverage 5"],
    }
    proposal_ref = _seed_proposal(redis_client, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        mock.post("/execute").mock(return_value=httpx.Response(200, json=gateway_response))

        with httpx.Client() as client:
            rc = main(["execute", proposal_ref], http_client=client, redis_client=redis_client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == gateway_response


def test_execute_proposal_invalida_nao_chama_gateway(monkeypatch, capsys, redis_client):
    """(d) proposal sem stop_loss → invalid_proposal local; gateway NÃO é chamado."""
    _setup_env(monkeypatch)
    proposal_ref = _seed_proposal(redis_client, _proposal_sem_stop())

    # assert_all_called=False: registramos a rota só para poder checar call_count;
    # o teste prova que ela NÃO foi chamada.
    with respx.mock(base_url=GATEWAY_URL, assert_all_called=False) as mock:
        execute_route = mock.post("/execute").mock(
            return_value=httpx.Response(200, json={"executed": True})
        )

        with httpx.Client() as client:
            rc = main(["execute", proposal_ref], http_client=client, redis_client=redis_client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["executed"] is False
    assert out["reason"] == "invalid_proposal"
    assert execute_route.call_count == 0


def test_execute_redis_key_ausente_vira_invalid(monkeypatch, capsys, redis_client):
    """(d2) referência redis: sem chave no Redis → invalid_proposal, gateway não chamado."""
    _setup_env(monkeypatch)

    with respx.mock(base_url=GATEWAY_URL, assert_all_called=False) as mock:
        execute_route = mock.post("/execute").mock(return_value=httpx.Response(200, json={}))

        with httpx.Client() as client:
            rc = main(["execute", f"redis:{PROPOSAL_KEY}"], http_client=client, redis_client=redis_client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["reason"] == "invalid_proposal"
    assert "redis key not found" in out["detail"]
    assert execute_route.call_count == 0


def test_execute_ref_nao_redis_rejeitada(monkeypatch, capsys, redis_client):
    """(d3) referência fora do padrão redis:KEY → invalid_proposal (handoff é redis-first)."""
    _setup_env(monkeypatch)

    with respx.mock(base_url=GATEWAY_URL, assert_all_called=False) as mock:
        execute_route = mock.post("/execute").mock(return_value=httpx.Response(200, json={}))

        with httpx.Client() as client:
            rc = main(["execute", "workspace/proposal.json"], http_client=client, redis_client=redis_client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["reason"] == "invalid_proposal"
    assert execute_route.call_count == 0


def test_auth_header_enviado_no_brief_e_execute(monkeypatch, capsys, redis_client):
    """(e) cliente envia 'Authorization: Bearer <GATEWAY_TOKEN>' em /brief e /execute."""
    _setup_env(monkeypatch)
    proposal_ref = _seed_proposal(redis_client, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        brief_route = mock.post("/brief").mock(
            return_value=httpx.Response(200, json=_brief_payload())
        )
        execute_route = mock.post("/execute").mock(
            return_value=httpx.Response(200, json={"executed": True, "orders": [], "automations": [], "errors": []})
        )

        with httpx.Client() as client:
            main(["brief"], http_client=client, redis_client=redis_client)
            capsys.readouterr()  # descarta stdout do brief
            main(["execute", proposal_ref], http_client=client, redis_client=redis_client)

    expected_auth = f"Bearer {GATEWAY_TOKEN}"
    assert brief_route.calls[0].request.headers["Authorization"] == expected_auth
    assert execute_route.calls[0].request.headers["Authorization"] == expected_auth


def test_execute_gateway_502_vira_gateway_error(monkeypatch, capsys, redis_client):
    """(f) /execute responde 502 → cliente imprime {"executed":false,"reason":"gateway_error"}."""
    _setup_env(monkeypatch)
    proposal_ref = _seed_proposal(redis_client, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        mock.post("/execute").mock(return_value=httpx.Response(502, text="bad gateway"))

        with httpx.Client() as client:
            rc = main(["execute", proposal_ref], http_client=client, redis_client=redis_client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["executed"] is False
    assert out["reason"] == "gateway_error"
    assert "detail" in out
