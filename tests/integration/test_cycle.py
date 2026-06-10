# Testes de integração do ciclo do estrategista (thin-client HTTP do Risk Gateway).
#
# Mocks SÓ na fronteira de I/O: gateway HTTP via respx + httpx.Client injetado.
# fakeredis NÃO é mais necessário — o cliente não toca Redis.
# main(argv, http_client=) é invocado diretamente; capsys captura stdout.
import json

import httpx
import pytest
import respx

from strategist_cycle import main

GATEWAY_URL = "http://risk-gateway.test:8647"
GATEWAY_TOKEN = "gw_test_token_abc123"


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


def _write_proposal(tmp_path, payload: dict) -> str:
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _chdir_tmp(tmp_path, monkeypatch):
    # workspace/ é relativo ao cwd; isola cada teste num tmp dir.
    monkeypatch.chdir(tmp_path)


# --- Testes ---


def test_brief_escreve_arquivo_e_imprime_path(monkeypatch, capsys, tmp_path):
    """(a) brief → POST /brief retorna Brief JSON; workspace/brief.json escrito; stdout = path."""
    _setup_env(monkeypatch)
    brief_data = _brief_payload()

    with respx.mock(base_url=GATEWAY_URL) as mock:
        mock.post("/brief").mock(return_value=httpx.Response(200, json=brief_data))

        with httpx.Client() as client:
            rc = main(["brief"], http_client=client)

    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.endswith("workspace/brief.json")

    written = json.loads((tmp_path / "workspace" / "brief.json").read_text())
    assert written == brief_data


def test_execute_happy_path_repassa_gateway(monkeypatch, capsys, tmp_path):
    """(b) execute happy → POST /execute retorna resultado; stdout == esse JSON; corpo enviado == proposal.json."""
    _setup_env(monkeypatch)
    gateway_response = {
        "executed": True,
        "orders": [{"entry_order_id": 111, "stop_order_id": 222}],
        "automations": [],
        "errors": [],
    }
    proposal_path = _write_proposal(tmp_path, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        execute_route = mock.post("/execute").mock(
            return_value=httpx.Response(200, json=gateway_response)
        )

        with httpx.Client() as client:
            rc = main(["execute", proposal_path], http_client=client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == gateway_response

    # Corpo enviado ao gateway deve ser o JSON da proposta.
    sent_body = json.loads(execute_route.calls[0].request.content)
    assert sent_body == _proposal_payload()


def test_execute_gate_rejected_repassa_fielmente(monkeypatch, capsys, tmp_path):
    """(c) execute gate_rejected → gateway retorna violations; cliente repassa sem alteração."""
    _setup_env(monkeypatch)
    gateway_response = {
        "executed": False,
        "reason": "gate_rejected",
        "violations": ["leverage 20 > max_leverage 5"],
    }
    proposal_path = _write_proposal(tmp_path, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        mock.post("/execute").mock(return_value=httpx.Response(200, json=gateway_response))

        with httpx.Client() as client:
            rc = main(["execute", proposal_path], http_client=client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == gateway_response


def test_execute_proposal_invalida_nao_chama_gateway(monkeypatch, capsys, tmp_path):
    """(d) proposal sem stop_loss → invalid_proposal local; gateway NÃO é chamado."""
    _setup_env(monkeypatch)
    proposal_path = _write_proposal(tmp_path, _proposal_sem_stop())

    # assert_all_called=False: registramos a rota só para poder checar call_count;
    # o teste prova que ela NÃO foi chamada.
    with respx.mock(base_url=GATEWAY_URL, assert_all_called=False) as mock:
        execute_route = mock.post("/execute").mock(
            return_value=httpx.Response(200, json={"executed": True})
        )

        with httpx.Client() as client:
            rc = main(["execute", proposal_path], http_client=client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["executed"] is False
    assert out["reason"] == "invalid_proposal"
    assert execute_route.call_count == 0


def test_auth_header_enviado_no_brief_e_execute(monkeypatch, capsys, tmp_path):
    """(e) cliente envia 'Authorization: Bearer <GATEWAY_TOKEN>' em /brief e /execute."""
    _setup_env(monkeypatch)
    proposal_path = _write_proposal(tmp_path, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        brief_route = mock.post("/brief").mock(
            return_value=httpx.Response(200, json=_brief_payload())
        )
        execute_route = mock.post("/execute").mock(
            return_value=httpx.Response(200, json={"executed": True, "orders": [], "automations": [], "errors": []})
        )

        with httpx.Client() as client:
            main(["brief"], http_client=client)
            capsys.readouterr()  # descarta stdout do brief
            main(["execute", proposal_path], http_client=client)

    expected_auth = f"Bearer {GATEWAY_TOKEN}"
    assert brief_route.calls[0].request.headers["Authorization"] == expected_auth
    assert execute_route.calls[0].request.headers["Authorization"] == expected_auth


def test_execute_gateway_502_vira_gateway_error(monkeypatch, capsys, tmp_path):
    """(f) /execute responde 502 → cliente imprime {"executed":false,"reason":"gateway_error"}."""
    _setup_env(monkeypatch)
    proposal_path = _write_proposal(tmp_path, _proposal_payload())

    with respx.mock(base_url=GATEWAY_URL) as mock:
        mock.post("/execute").mock(return_value=httpx.Response(502, text="bad gateway"))

        with httpx.Client() as client:
            rc = main(["execute", proposal_path], http_client=client)

    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["executed"] is False
    assert out["reason"] == "gateway_error"
    assert "detail" in out
