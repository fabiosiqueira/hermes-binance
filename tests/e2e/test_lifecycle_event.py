# E2E do ramo POR EVENTO do lifecycle — narrativa de como o estrategista Hermes
# é acordado por sinais externos (em contraste com o E2E agendado em
# test_lifecycle_dry_run.py, que é o ciclo cron brief→execute).
#
# Três atos:
#   1) "betrader acorda o Hermes": shim recebe POST assinado (X-Beholder-Signature),
#      verifica HMAC e repassa ao engine re-assinando (X-Webhook-Signature hex puro).
#   2) "estrategista arma a sentinela": install_automations injeta webhookUrl/secret do
#      env numa action WEBHOOK sem mutar o spec original.
#   3) "drawdown nos acorda": maybe_trigger_drawdown_wake dispara POST assinado quando o
#      DD cruza 80% do limite dos dogmas.
#
# Fronteiras mockadas: HTTP via respx (forward ao engine, betrader, webhook público),
# env via monkeypatch. DI real em todos os módulos internos (webhook_shim,
# betrader_client, observability). Secret de teste é valor fixo local; nunca versionado.
# Asserts: comportamento observável (status HTTP, calls respx, headers/body, retorno).
import hashlib
import hmac
import json
import socket

import httpx
import pytest
import respx

from betrader_client import BetraderClient
from observability import FinancialState, maybe_trigger_drawdown_wake
from schemas import AutomationSpec
from webhook_shim import build_forward_signature, start_shim, verify_signature

# Secret fixo local de teste (nunca um valor de produção).
WEBHOOK_SECRET = "whsec_event_test"
ENGINE_URL = "http://127.0.0.1:8646/webhooks/strategist-event"
WEBHOOK_PUBLIC_URL = "https://hermes.example.test/hook/betrader"

BASE_URL = "https://betrader.example.test"
TOKEN = "bht_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcd"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _beholder_sign(raw_body: bytes) -> str:
    """Assinatura de entrada do betrader: `sha256=<hex>` (X-Beholder-Signature)."""
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()


def _event_body() -> bytes:
    """Corpo JSON de um evento que o betrader manda para acordar o Hermes."""
    return json.dumps(
        {
            "source": "beholder",
            "type": "automation.triggered",
            "symbol": "BTCUSDT",
            "automation": "btc_trailing_stop",
        }
    ).encode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def shim(monkeypatch):
    """Sobe o shim numa porta livre; retorna a base_url e a lista de erros capturados."""
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("ENGINE_WEBHOOK_URL", ENGINE_URL)
    port = _free_port()
    errors: list[str] = []
    start_shim(port, on_error=errors.append)
    return f"http://127.0.0.1:{port}", errors, port


def _setup_webhook_env(monkeypatch) -> None:
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", WEBHOOK_PUBLIC_URL)
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", WEBHOOK_SECRET)


# ===========================================================================
# ATO 1: "betrader acorda o Hermes" — shim verifica e repassa ao engine
# ===========================================================================


@respx.mock
def test_betrader_acorda_hermes_forward_assinado(shim) -> None:
    """Evento assinado válido → shim repassa ao engine com X-Webhook-Signature hex puro,
    MESMO body raw → 200. Assinatura adulterada → 401 e o engine NÃO é chamado.
    """
    base, _errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    engine = respx.post(ENGINE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    raw = _event_body()

    # --- Assinatura válida → forward + 200 ---
    resp = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": _beholder_sign(raw), "Content-Type": "application/json"},
    )

    assert resp.status_code == 200, "evento válido: shim responde 200"
    assert resp.json() == {"ok": True}
    assert engine.called, "evento válido: engine foi acordado (forward chamado)"

    fwd = engine.calls.last.request
    assert fwd.content == raw, "forward repassa o MESMO body raw, byte a byte"
    forward_sig = fwd.headers["X-Webhook-Signature"]
    assert forward_sig == build_forward_signature(raw, WEBHOOK_SECRET), (
        "forward re-assinado com HMAC do mesmo secret"
    )
    assert not forward_sig.startswith("sha256="), "esquema Generic do engine: hex PURO"
    # Coerência com a verificação pura do shim.
    assert verify_signature(raw, _beholder_sign(raw), WEBHOOK_SECRET) is True

    # --- Assinatura adulterada → 401, engine NÃO é chamado de novo ---
    calls_antes = engine.call_count
    resp_bad = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": "sha256=deadbeef", "Content-Type": "application/json"},
    )

    assert resp_bad.status_code == 401, "assinatura adulterada: 401"
    assert resp_bad.json() == {"ok": False, "error": "invalid signature"}
    assert engine.call_count == calls_antes, "assinatura adulterada: engine NÃO acordado"
    assert verify_signature(raw, "sha256=deadbeef", WEBHOOK_SECRET) is False


# ===========================================================================
# ATO 2: "estrategista arma a sentinela" — install_automations injeta webhook
# ===========================================================================


@respx.mock
def test_estrategista_arma_sentinela_webhook(monkeypatch) -> None:
    """install_automations numa action WEBHOOK → POST /api/automations carrega
    webhookUrl/webhookSecret do env; a action original do spec NÃO é mutada.
    """
    _setup_webhook_env(monkeypatch)

    post_automations = respx.post(f"{BASE_URL}/api/automations").mock(
        return_value=httpx.Response(200, json={"id": "auto-wh-1"})
    )
    start_automation = respx.post(f"{BASE_URL}/api/automations/auto-wh-1/start").mock(
        return_value=httpx.Response(200, json={})
    )

    action_original = {"type": "WEBHOOK"}
    spec = AutomationSpec(
        name="btc_drawdown_sentinel",
        condition="MEMORY['BTCUSDT:RSI_period'] > 70",
        action=action_original,
    )

    client = BetraderClient(BASE_URL, TOKEN)
    ids = client.install_automations([spec])
    client.close()

    # --- Retorno e calls ---
    assert ids == ["auto-wh-1"], "sentinela instalada com id retornado pelo betrader"
    assert post_automations.called, "POST /api/automations chamado"
    assert start_automation.called, "POST .../start chamado (sentinela ativada)"

    # --- Payload: webhookUrl/webhookSecret injetados na action ---
    body = json.loads(post_automations.calls.last.request.content)
    action_enviada = body["newAutomation"]["actions"][0]
    assert action_enviada["type"] == "WEBHOOK"
    assert action_enviada["webhookUrl"] == WEBHOOK_PUBLIC_URL, "webhookUrl injetado do env"
    assert action_enviada["webhookSecret"] == WEBHOOK_SECRET, "webhookSecret injetado do env"

    # --- A action original (e o spec) NÃO foram mutados ---
    assert action_original == {"type": "WEBHOOK"}, "action original imutável"
    assert spec.action == {"type": "WEBHOOK"}, "spec.action não mutado"


# ===========================================================================
# ATO 3: "drawdown nos acorda" — maybe_trigger_drawdown_wake dispara POST assinado
# ===========================================================================


@respx.mock
def test_drawdown_nos_acorda_post_assinado(monkeypatch) -> None:
    """DD ≥ 80% do limite → maybe_trigger_drawdown_wake dispara POST assinado ao webhook
    público (X-Beholder-Signature do MESMO body). DD abaixo do threshold → não dispara.
    """
    _setup_webhook_env(monkeypatch)

    wake = respx.post(WEBHOOK_PUBLIC_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    # Limite diário dos dogmas: 5%. Threshold de wake = 80% * 5% = 4%.
    max_daily_drawdown_pct = 5.0

    # Estado com DD ≥ 80% do limite: peak 10000, equity 9550 → DD = 4.5% ( > 4% threshold).
    state = FinancialState(initial_equity=10000.0, cum_pnl=-450.0, peak_equity=10000.0)
    assert state.drawdown_pct == pytest.approx(4.5), "pré-condição: DD = 4.5% ( ≥ threshold 4%)"

    disparou = maybe_trigger_drawdown_wake(state, max_daily_drawdown_pct)

    assert disparou is True, "DD acima do threshold: wake disparado"
    assert wake.called, "POST ao webhook público chamado"

    req = wake.calls.last.request
    raw_body = req.content
    sig_header = req.headers["X-Beholder-Signature"]
    assert sig_header.startswith("sha256="), "assinatura no esquema betrader (sha256=<hex>)"
    expected_sig = hmac.new(WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    assert sig_header == f"sha256={expected_sig}", "POST assinado com HMAC do MESMO body"
    # Body carrega o motivo do wake.
    payload = json.loads(raw_body)
    assert payload["type"] == "drawdown.threshold"
    assert payload["drawdown_pct"] == pytest.approx(4.5)

    # --- DD abaixo do threshold → não acorda ---
    calls_antes = wake.call_count
    state_calmo = FinancialState(initial_equity=10000.0, cum_pnl=-100.0, peak_equity=10000.0)
    assert state_calmo.drawdown_pct == pytest.approx(1.0), "DD calmo = 1% ( < threshold 4%)"
    nao_disparou = maybe_trigger_drawdown_wake(state_calmo, max_daily_drawdown_pct)

    assert nao_disparou is False, "DD abaixo do threshold: sem wake"
    assert wake.call_count == calls_antes, "nenhum POST adicional ao webhook"
