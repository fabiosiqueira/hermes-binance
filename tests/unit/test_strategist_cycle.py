# Unit tests do thin-client do ciclo (strategist_cycle).
#
# Foco: construção do httpx.Client da fronteira de I/O do gateway. O brief é
# betrader-bound e leva 15–44s; o default do httpx (5s) estoura sempre e mascara
# o brief como gateway_error apesar de o gateway gravar o Redis (#6). O client
# precisa de um timeout generoso e configurável.
import httpx

from strategist_cycle import _DEFAULT_HTTP_TIMEOUT_SECONDS, _build_http_client


def test_build_http_client_usa_timeout_generoso_por_default(monkeypatch):
    """Sem env: timeout = _DEFAULT_HTTP_TIMEOUT_SECONDS (não o 5s default do httpx)."""
    monkeypatch.delenv("GATEWAY_HTTP_TIMEOUT_SECONDS", raising=False)

    client = _build_http_client()
    try:
        assert client.timeout.read == _DEFAULT_HTTP_TIMEOUT_SECONDS
        assert client.timeout.connect == _DEFAULT_HTTP_TIMEOUT_SECONDS
        # Sanidade: comprovadamente maior que o default do httpx (5s) e que a
        # cauda de latência do brief (~44s).
        assert _DEFAULT_HTTP_TIMEOUT_SECONDS >= 60.0
    finally:
        client.close()


def test_build_http_client_honra_env(monkeypatch):
    """GATEWAY_HTTP_TIMEOUT_SECONDS sobrepõe o default."""
    monkeypatch.setenv("GATEWAY_HTTP_TIMEOUT_SECONDS", "120")

    client = _build_http_client()
    try:
        assert client.timeout.read == 120.0
    finally:
        client.close()
