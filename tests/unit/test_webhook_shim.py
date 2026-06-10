# Unit tests do shim de webhook betrader → engine (webhook_shim).
#
# Mock APENAS na fronteira HTTP (respx) para o forward ao engine; a verificação de
# assinatura e o roteamento do handler são reais (DI real). O shim roda em servidor
# HTTP real numa porta livre; o cliente bate com httpx contra ele.
#
# Caminho coberto: assinatura válida → forward com X-Webhook-Signature hex puro e mesmo
# body → 200; assinatura inválida/ausente → 401 e forward NÃO chamado; JSON inválido →
# 400; método GET → 405; path errado → 404; falha do engine (500 / ConnectError) → 502.
# O secret JAMAIS aparece em log/forward; é um valor fixo local de teste.
import hashlib
import hmac
import json
import socket

import httpx
import pytest
import respx

from webhook_shim import (
    build_forward_signature,
    start_shim,
    verify_signature,
)

SECRET = "whsec_test"
ENGINE_URL = "http://127.0.0.1:8646/webhooks/strategist-event"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sign(raw_body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest()


@pytest.fixture
def shim(monkeypatch):
    """Sobe o shim numa porta livre e retorna a base_url para os POSTs."""
    monkeypatch.setenv("BETRADER_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("ENGINE_WEBHOOK_URL", ENGINE_URL)
    port = _free_port()
    errors: list[str] = []
    start_shim(port, on_error=errors.append)
    base = f"http://127.0.0.1:{port}"
    return base, errors, port


# --- Funções puras de assinatura ---


def test_verify_signature_aceita_hex_correto() -> None:
    raw = b'{"event":"x"}'
    assert verify_signature(raw, _sign(raw), SECRET) is True


def test_verify_signature_rejeita_hex_divergente() -> None:
    raw = b'{"event":"x"}'
    assert verify_signature(raw, "sha256=deadbeef", SECRET) is False


def test_verify_signature_rejeita_header_ausente() -> None:
    assert verify_signature(b"{}", None, SECRET) is False


def test_verify_signature_rejeita_sem_prefixo() -> None:
    raw = b"{}"
    hex_only = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert verify_signature(raw, hex_only, SECRET) is False


def test_build_forward_signature_e_hex_puro_sem_prefixo() -> None:
    raw = b'{"event":"x"}'
    sig = build_forward_signature(raw, SECRET)
    assert sig == hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    assert not sig.startswith("sha256=")


# --- Caminho feliz: assinatura válida → forward → 200 ---


@respx.mock
def test_assinatura_valida_faz_forward_com_hex_puro_e_mesmo_body(shim) -> None:
    base, _errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    route = respx.post(ENGINE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    raw = json.dumps({"event": "strategist", "n": 1}).encode()

    resp = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": _sign(raw), "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert route.called
    fwd = route.calls.last.request
    # Mesmo corpo raw repassado.
    assert fwd.content == raw
    # Header de saída é hex PURO, sem prefixo sha256=.
    sig = fwd.headers["X-Webhook-Signature"]
    assert sig == build_forward_signature(raw, SECRET)
    assert not sig.startswith("sha256=")
    assert fwd.headers["Content-Type"] == "application/json"


# --- Assinatura inválida / ausente → 401, sem forward ---


@respx.mock
def test_assinatura_invalida_retorna_401_sem_forward(shim) -> None:
    base, _errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    route = respx.post(ENGINE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    raw = b'{"event":"x"}'

    resp = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": "sha256=deadbeef"},
    )

    assert resp.status_code == 401
    assert resp.json() == {"ok": False, "error": "invalid signature"}
    assert not route.called


@respx.mock
def test_header_ausente_retorna_401_sem_forward(shim) -> None:
    base, _errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    route = respx.post(ENGINE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    raw = b'{"event":"x"}'

    resp = httpx.post(f"{base}/hook/betrader", content=raw)

    assert resp.status_code == 401
    assert not route.called


# --- JSON inválido → 400 ---


@respx.mock
def test_json_invalido_retorna_400_sem_forward(shim) -> None:
    base, _errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    route = respx.post(ENGINE_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    raw = b"not-json"

    resp = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": _sign(raw)},
    )

    assert resp.status_code == 400
    assert not route.called


# --- Método / path ---


def test_metodo_get_retorna_405(shim) -> None:
    base, _errors, _port = shim
    resp = httpx.get(f"{base}/hook/betrader")
    assert resp.status_code == 405


def test_path_errado_retorna_404(shim) -> None:
    base, _errors, _port = shim
    raw = b"{}"
    resp = httpx.post(
        f"{base}/outro",
        content=raw,
        headers={"X-Beholder-Signature": _sign(raw)},
    )
    assert resp.status_code == 404


# --- Falha do engine → 502 + on_error ---


@respx.mock
def test_engine_500_retorna_502_e_notifica_on_error(shim) -> None:
    base, errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    respx.post(ENGINE_URL).mock(return_value=httpx.Response(500, text="boom"))
    raw = b'{"event":"x"}'

    resp = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": _sign(raw)},
    )

    assert resp.status_code == 502
    assert resp.json() == {"ok": False, "error": "forward failed"}
    assert "engine_forward_error" in errors


@respx.mock
def test_engine_connect_error_retorna_502_e_notifica_on_error(shim) -> None:
    base, errors, port = shim
    respx.route(host="127.0.0.1", port=port).pass_through()
    respx.post(ENGINE_URL).mock(side_effect=httpx.ConnectError("conn refused"))
    raw = b'{"event":"x"}'

    resp = httpx.post(
        f"{base}/hook/betrader",
        content=raw,
        headers={"X-Beholder-Signature": _sign(raw)},
    )

    assert resp.status_code == 502
    assert "engine_forward_error" in errors
