# Shim HTTP que recebe o webhook do betrader-hydra, verifica a assinatura HMAC e
# repassa ao webhook nativo do engine Hermes re-assinando para o esquema Generic.
#
# Arquitetura (Opção B): betrader → shim (verifica X-Beholder-Signature: sha256=<hex>)
# → POST 127.0.0.1:8646/webhooks/strategist-event (header X-Webhook-Signature: <hex puro>,
# esquema Generic do engine) → a rota acorda o HAWK. Sem Redis stream, sem subprocess.
#
# Fronteira de I/O: httpx (forward ao engine). `on_error(type)` é o ponto de DI onde a
# observability pluga (este módulo NÃO importa observability). Segredos: o secret JAMAIS
# é logado nem aparece em repr/str; o corpo cru também não é logado.
#
# Porta pública 8645 (/hook/betrader). Engine na 8646 (loopback-only). Secret único
# BETRADER_WEBHOOK_SECRET (env). Assinatura = HMAC-SHA256(secret, raw_body) hex.
# betrader manda sha256=<hex> em X-Beholder-Signature; engine Generic espera hex puro
# (sem prefixo) em X-Webhook-Signature.
import hashlib
import hmac
import json
import os
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import httpx

_HOOK_PATH = "/hook/betrader"
_BEHOLDER_SIGNATURE_HEADER = "X-Beholder-Signature"
_BEHOLDER_PREFIX = "sha256="
_FORWARD_SIGNATURE_HEADER = "X-Webhook-Signature"
_DEFAULT_ENGINE_WEBHOOK_URL = "http://127.0.0.1:8646/webhooks/strategist-event"


def verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool:
    """Verifica a assinatura de entrada do betrader.

    header deve estar no formato `sha256=<hex>` (X-Beholder-Signature). Calcula
    expected = HMAC-SHA256(secret, raw_body) hex e compara com hmac.compare_digest
    contra o hex após o prefixo. Ausente ou divergente → False.
    """
    if header is None or not header.startswith(_BEHOLDER_PREFIX):
        return False
    provided = header[len(_BEHOLDER_PREFIX) :]
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


def build_forward_signature(raw_body: bytes, secret: str) -> str:
    """Assinatura de saída para o engine (esquema Generic): hex PURO, sem prefixo."""
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def start_shim(port: int = 8645, *, on_error: Callable[[str], None] | None = None) -> None:
    """Inicia o shim na porta `port` (thread daemon), único path POST /hook/betrader.

    Lê o secret de BETRADER_WEBHOOK_SECRET e a URL de forward de ENGINE_WEBHOOK_URL
    (default http://127.0.0.1:8646/webhooks/strategist-event). `on_error(type)` é
    notificado em falha de I/O do forward ao engine.
    """
    secret = os.environ["BETRADER_WEBHOOK_SECRET"]
    engine_url = os.environ.get("ENGINE_WEBHOOK_URL", _DEFAULT_ENGINE_WEBHOOK_URL)

    def _notify(type: str) -> None:  # noqa: A002
        if on_error is not None:
            on_error(type)

    class _ShimHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict) -> None:  # type: ignore[type-arg]
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._send_json(405, {"ok": False, "error": "method not allowed"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != _HOOK_PATH:
                self._send_json(404, {"ok": False, "error": "not found"})
                return

            length = int(self.headers.get("Content-Length", 0) or 0)
            raw_body = self.rfile.read(length) if length > 0 else b""

            # Verificação da assinatura de entrada (corpo RAW exato).
            header = self.headers.get(_BEHOLDER_SIGNATURE_HEADER)
            if not verify_signature(raw_body, header, secret):
                self._send_json(401, {"ok": False, "error": "invalid signature"})
                return

            # JSON inválido → 400 (não faz forward).
            try:
                json.loads(raw_body)
            except (ValueError, UnicodeDecodeError):
                self._send_json(400, {"ok": False, "error": "invalid json"})
                return

            # Forward ao engine com mesmo corpo raw + assinatura re-emitida (hex puro).
            forward_sig = build_forward_signature(raw_body, secret)
            try:
                resp = httpx.post(
                    engine_url,
                    content=raw_body,
                    headers={
                        "Content-Type": "application/json",
                        _FORWARD_SIGNATURE_HEADER: forward_sig,
                    },
                )
            except httpx.HTTPError:
                _notify("engine_forward_error")
                self._send_json(502, {"ok": False, "error": "forward failed"})
                return

            if 200 <= resp.status_code < 300:
                self._send_json(200, {"ok": True})
                return
            _notify("engine_forward_error")
            self._send_json(502, {"ok": False, "error": "forward failed"})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # silencia logs do BaseHTTPRequestHandler (nunca loga corpo/assinatura)

    server = HTTPServer(("0.0.0.0", port), _ShimHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()


if __name__ == "__main__":
    start_shim()
    # Mantém o processo vivo (a thread do servidor é daemon).
    import time

    while True:
        time.sleep(3600)
