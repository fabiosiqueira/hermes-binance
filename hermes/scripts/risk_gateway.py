# Risk Gateway (F2): promove o enforcement in-process do estrategista a serviço HTTP
# determinístico que DETÉM o token do betrader.
#
# O HAWK nunca mais chama o betrader nem o gate direto: ele bate em POST /brief e
# POST /execute neste serviço (autenticado por GATEWAY_TOKEN). O gate (risk_engine),
# o assert_testnet e o emergency_stop rodam AQUI — o agente não tem rota de bypass.
#
# Arquitetura idêntica ao ciclo in-process (strategist_cycle), só que o brief não vive
# mais em workspace/brief.json: handle_brief cacheia o Brief no Redis
# (binance:strategist:brief:<symbol>, TTL BRIEF_CACHE_TTL_SECONDS) e handle_execute o
# relê de lá. Brief ausente/expirado → {"executed": False, "reason": "brief_missing"}.
#
# Fronteiras de I/O (DI real, mocks só aqui nos testes): httpx via BetraderClient,
# Redis via cliente injetável, observability injetável. `on_error(type)` é o ponto onde
# a observability pluga (este módulo reusa observability.record_error).
#
# Decisão de portas (espelha observability.py): /metrics e /health vêm da Observability
# (start_servers → /metrics:9468, /health:9469). O handler DESTE serviço serve só
# /brief + /execute na porta GATEWAY_PORT (default 8647), além de um /health local de
# liveness. Cada responsabilidade isolada, sem servidor WSGI completo.
#
# Auth: Authorization: Bearer <GATEWAY_TOKEN>, comparado com hmac.compare_digest.
# GATEWAY_TOKEN ausente → o servidor RECUSA subir (igual webhook_shim com o secret).
# Segredos: GATEWAY_TOKEN e BETRADER_TOKEN JAMAIS são logados nem aparecem em corpo.
import hmac
import json
import os
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from pydantic import ValidationError

from schemas import (
    Brief,
    ExecutionMode,
    StrategyProposal,
    load_dogmas,
)

from betrader_client import BetraderClient, BetraderError
from observability import FinancialState, Observability, maybe_trigger_drawdown_wake
from risk_engine import check_emergency_stop, validate

# Dogmas vivem ao lado do data dir, relativo ao próprio script (não ao cwd) — igual
# strategist_cycle, para que o gate carregue a MESMA constituição de risco.
_DOGMAS_PATH = Path(__file__).parent.parent / "dogmas.yaml"

# Namespace do cache de brief (binance:strategist:* como o resto da observability).
_KEY_BRIEF_PREFIX = "binance:strategist:brief:"

_DEFAULT_GATEWAY_PORT = 8647


def _brief_key(symbol: str) -> str:
    """Chave Redis do brief cacheado para um símbolo."""
    return f"{_KEY_BRIEF_PREFIX}{symbol}"


def _ref_price(brief: Brief):
    """Último close do brief para dimensionar ordens MARKET (contrato do Cluster 3)."""
    if brief.market.candles:
        return brief.market.candles[-1].close
    return None


def require_auth(header_value: str | None) -> bool:
    """Valida o header Authorization contra GATEWAY_TOKEN via hmac.compare_digest.

    Aceita exatamente "Bearer <GATEWAY_TOKEN>". Ausente/divergente → False. Comparação
    em tempo constante. O token JAMAIS é logado.
    """
    token = os.environ["GATEWAY_TOKEN"]
    expected = f"Bearer {token}"
    if header_value is None:
        return False
    return hmac.compare_digest(header_value, expected)


def handle_brief(
    *,
    symbol: str,
    timeframe: str,
    mode: ExecutionMode,
    redis_client: object,
    observability: Observability,
) -> dict:
    """Monta o Brief (replica _cmd_brief) e o cacheia no Redis com TTL.

    Ordem exata do ciclo: ensure_monitor → FinancialState.load → fetch_brief. O brief
    serializado é cacheado em binance:strategist:brief:<symbol> (TTL =
    BRIEF_CACHE_TTL_SECONDS, default 900s) para o handle_execute reler. Retorna o
    Brief.model_dump().
    """
    client = BetraderClient.from_env(on_error=observability.record_error)
    try:
        client.ensure_monitor(symbol, timeframe)
        state = FinancialState.load(redis_client)
        brief = client.fetch_brief(symbol, timeframe, mode, state.to_risk_state())
    finally:
        client.close()

    ttl = int(os.environ.get("BRIEF_CACHE_TTL_SECONDS", "900"))
    redis_client.set(_brief_key(symbol), brief.model_dump_json(), ex=ttl)  # type: ignore[attr-defined]
    return brief.model_dump(mode="json")


def handle_execute(
    *,
    proposal: StrategyProposal,
    symbol: str,
    redis_client: object,
    observability: Observability,
) -> dict:
    """Gate + execução da proposta (replica _cmd_execute) com o brief vindo do cache.

    Contrato de stdout idêntico ao _cmd_execute (mesmos reasons: emergency_stop,
    brief_missing, invalid_proposal, gate_rejected; sucesso: executed/orders/
    automations/errors). O brief vem do Redis (ausente/expirado → brief_missing). O
    estado financeiro é persistido ANTES de retornar (integridade bot.md).
    """
    # (a) kill switch via env, checado no início — sem throw.
    if check_emergency_stop():
        return {"executed": False, "reason": "emergency_stop"}

    # (b) brief do cache Redis; ausente/expirado → brief_missing.
    raw_brief = redis_client.get(_brief_key(symbol))  # type: ignore[attr-defined]
    if raw_brief is None:
        return {"executed": False, "reason": "brief_missing"}
    try:
        brief = Brief.model_validate_json(raw_brief)
        dogmas = load_dogmas(_DOGMAS_PATH)
    except (OSError, ValidationError) as exc:
        observability.record_error("brief_reload_error")
        return {"executed": False, "reason": "invalid_proposal", "detail": str(exc)}

    state = FinancialState.load(redis_client)
    maybe_trigger_drawdown_wake(
        state,
        dogmas.max_daily_drawdown_pct,
        on_error=observability.record_error,
    )

    # (c) gate determinístico.
    result = validate(proposal, dogmas, brief)
    if not result.ok:
        observability.record_decision(
            proposal.model_dump(), gate_ok=False, reason=result.reason, redis=redis_client
        )
        return {
            "executed": False,
            "reason": "gate_rejected",
            "violations": result.violations,
        }

    # (d) aprovado → execução. teardown → entries → install_automations.
    orders: list[dict] = []
    automations: list[str] = []
    errors: list[str] = []
    client = BetraderClient.from_env(on_error=observability.record_error)
    try:
        if brief.mode == ExecutionMode.DRY_RUN:
            client.assert_testnet()

        if proposal.teardown:
            try:
                client.teardown(proposal.teardown)
            except BetraderError as exc:
                observability.record_error(exc.type)
                errors.append(exc.type)

        ref_price = _ref_price(brief)
        for entry in proposal.entries:
            # Falha de uma entry NÃO aborta as automations, mas é coletada.
            try:
                order = client.place_entry_with_stop(
                    entry, brief.portfolio.equity, ref_price=ref_price
                )
                orders.append(order)
            except BetraderError as exc:
                observability.record_error(exc.type)
                errors.append(exc.type)

        if proposal.automations:
            try:
                automations = client.install_automations(proposal.automations)
            except BetraderError as exc:
                observability.record_error(exc.type)
                errors.append(exc.type)
    except BetraderError as exc:
        # assert_testnet falhou (ou outra falha fora do laço): aborta a escrita.
        observability.record_error(exc.type)
        errors.append(exc.type)
    finally:
        client.close()

    # (e) observability: decisão + ciclo, e PERSISTE o estado ANTES de retornar.
    observability.record_decision(
        proposal.model_dump(), gate_ok=True, reason=None, redis=redis_client
    )
    observability.record_cycle()
    state.persist(redis_client)

    # (f) resumo do ciclo.
    return {
        "executed": True,
        "orders": orders,
        "automations": automations,
        "errors": errors,
    }


def _execute_symbol(proposal: StrategyProposal) -> str:
    """Símbolo do execute: entries[0].symbol quando há entries, senão SYMBOL do env.

    Para teardown/automation-only (sem entries) o brief cacheado é o do símbolo do env.
    """
    if proposal.entries:
        return proposal.entries[0].symbol
    return os.environ.get("SYMBOL", "BTCUSDT")


def _build_redis():
    """Constrói o cliente Redis da fronteira de I/O a partir do env.

    REDIS_HOST/REDIS_PORT conforme AGENTS.md. decode_responses=True para que o
    FinancialState e o cache de brief leiam strings (não bytes).
    """
    import redis

    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def start_gateway(
    port: int = _DEFAULT_GATEWAY_PORT,
    *,
    redis_client: object = None,
    observability: Observability = None,  # type: ignore[assignment]
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Sobe o Risk Gateway: Observability (/metrics+/health) + handler /brief+/execute.

    Constrói Redis e Observability se não injetados (fronteiras de I/O). Restaura as
    métricas do estado persistido (resiliência bot.md) ANTES de servir, sobe os
    servidores da Observability e o handler do gateway (thread daemon). GATEWAY_TOKEN
    ausente → RECUSA subir (raise no start, igual webhook_shim com o secret).
    """
    token = os.environ["GATEWAY_TOKEN"]  # ausente → KeyError, servidor não sobe.
    del token  # nunca usado/logado aqui; require_auth o relê por request.

    redis_client = redis_client if redis_client is not None else _build_redis()
    observability = observability if observability is not None else Observability()

    # Resiliência: re-popula métricas do estado persistido pós-restart antes de servir.
    observability.restore_metrics(FinancialState.load(redis_client))
    observability.start_servers()

    timeframe = os.environ.get("TIMEFRAME", "15m")
    default_symbol = os.environ.get("SYMBOL", "BTCUSDT")
    mode = ExecutionMode(os.environ.get("EXECUTION_MODE", "DRY_RUN"))

    def _notify(type: str) -> None:  # noqa: A002
        if on_error is not None:
            on_error(type)

    class _GatewayHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict) -> None:  # type: ignore[type-arg]
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict | None:  # type: ignore[type-arg]
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                return json.loads(raw) if raw else {}
            except (ValueError, UnicodeDecodeError):
                return None

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send_json(200, {"status": "ok"})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in ("/brief", "/execute"):
                self._send_json(404, {"ok": False, "error": "not found"})
                return

            if not require_auth(self.headers.get("Authorization")):
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return

            payload = self._read_json()
            if payload is None:
                self._send_json(400, {"ok": False, "error": "invalid json"})
                return

            if self.path == "/brief":
                self._handle_brief_request(payload)
            else:
                self._handle_execute_request(payload)

        def _handle_brief_request(self, payload: dict) -> None:  # type: ignore[type-arg]
            symbol = payload.get("symbol", default_symbol)
            tf = payload.get("timeframe", timeframe)
            try:
                result = handle_brief(
                    symbol=symbol,
                    timeframe=tf,
                    mode=mode,
                    redis_client=redis_client,
                    observability=observability,
                )
            except BetraderError as exc:
                _notify(exc.type)
                self._send_json(502, {"ok": False, "error": "brief failed"})
                return
            self._send_json(200, result)

        def _handle_execute_request(self, payload: dict) -> None:  # type: ignore[type-arg]
            try:
                proposal = StrategyProposal.model_validate(payload)
            except ValidationError as exc:
                self._send_json(
                    200,
                    {
                        "executed": False,
                        "reason": "invalid_proposal",
                        "detail": exc.errors(),
                    },
                )
                return
            symbol = _execute_symbol(proposal)
            result = handle_execute(
                proposal=proposal,
                symbol=symbol,
                redis_client=redis_client,
                observability=observability,
            )
            self._send_json(200, result)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # silencia logs (nunca loga token/corpo)

    server = HTTPServer(("0.0.0.0", port), _GatewayHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()


if __name__ == "__main__":
    start_gateway(int(os.environ.get("GATEWAY_PORT", str(_DEFAULT_GATEWAY_PORT))))
    # Mantém o processo vivo (a thread do servidor é daemon).
    import time

    while True:
        time.sleep(3600)
