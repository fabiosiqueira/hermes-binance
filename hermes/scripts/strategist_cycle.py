# CLI do ciclo do estrategista Hermes (thin-client HTTP do Risk Gateway).
#
# Este módulo é um CLIENTE BURRO: não valida dogmas, não chama betrader, não toca
# Redis. Todo enforcement de risco fica no gateway (risk_gateway.py). O agente LLM
# (HAWK) só fala com o gateway via HTTP.
#
# Handoff 100% Redis (sem filesystem): `brief` dispara o gateway (que escreve o brief
# no Redis) e roda o mulham_analyzer (que grava os sinais no Redis); imprime a CHAVE
# Redis do brief. `execute` lê a proposal do Redis (prefixo redis:KEY) e imprime JSON
# {"executed": ...}. NUNCA traceback cru.
#
# Env vars necessários do lado do cliente:
#   GATEWAY_URL    ex.: http://risk-gateway:8647
#   GATEWAY_TOKEN  token de autenticação do gateway
#   REDIS_HOST/REDIS_PORT  Redis do agente (binance-redis) — leitura da proposal
#   (brief apenas) SYMBOL, TIMEFRAME, EXECUTION_MODE
import argparse
import json
import os
import sys
from typing import Optional

import httpx
from pydantic import ValidationError

from schemas import ExecutionMode, StrategyProposal

# Chave Redis do brief espelhado pelo gateway (binance-redis), consumida redis-first.
_BRIEF_KEY_PREFIX = "binance:strategist:brief:"

_AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "

# Timeout do client HTTP do gateway. O brief é betrader-bound e leva 15–44s; o
# default do httpx (5s) estoura sempre e mascara o brief como gateway_error apesar
# de o gateway já ter gravado o Redis (#6). 90s cobre a cauda com folga; ajustável
# via GATEWAY_HTTP_TIMEOUT_SECONDS.
_DEFAULT_HTTP_TIMEOUT_SECONDS = 90.0


def _gateway_headers(token: str) -> dict:
    return {_AUTH_HEADER: f"{_BEARER_PREFIX}{token}"}


def _build_http_client() -> httpx.Client:
    """Client HTTP do gateway com timeout generoso (não o 5s default do httpx).

    Lê GATEWAY_HTTP_TIMEOUT_SECONDS do env (default _DEFAULT_HTTP_TIMEOUT_SECONDS).
    """
    timeout = float(
        os.environ.get("GATEWAY_HTTP_TIMEOUT_SECONDS", _DEFAULT_HTTP_TIMEOUT_SECONDS)
    )
    return httpx.Client(timeout=timeout)


def _emit(payload: dict) -> int:
    """Imprime o resumo JSON do ciclo no stdout e retorna exit code 0."""
    print(json.dumps(payload))
    return 0


def _emit_error(reason: str, detail: str = "") -> int:
    payload: dict = {"executed": False, "reason": reason}
    if detail:
        payload["detail"] = detail
    return _emit(payload)


def _build_redis():
    """Cliente Redis do agente (binance-redis) a partir de REDIS_HOST/REDIS_PORT."""
    import redis

    host = os.environ.get("REDIS_HOST", "redis")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def _load_gateway_config() -> tuple[str, str] | None:
    """Lê GATEWAY_URL e GATEWAY_TOKEN do env. Retorna None se ausentes."""
    url = os.environ.get("GATEWAY_URL", "").strip()
    token = os.environ.get("GATEWAY_TOKEN", "").strip()
    if not url or not token:
        return None
    return url, token


def _cmd_brief(*, http_client: httpx.Client) -> int:
    """Metade 1: solicita Brief ao gateway (handoff 100% Redis).

    Envia {symbol, timeframe, mode} ao POST /brief; o gateway escreve o brief no Redis
    do agente (binance:strategist:brief:<symbol>) e no seu cache autoritativo privado.
    Em seguida roda o mulham_analyzer (redis-first) e imprime a CHAVE Redis do brief.
    Não há escrita em arquivo.
    """
    config = _load_gateway_config()
    if config is None:
        print(
            json.dumps({"executed": False, "reason": "missing_gateway_config"}),
            file=sys.stderr,
        )
        return 1

    gateway_url, token = config
    symbol = os.environ.get("SYMBOL", "BTCUSDT")
    timeframe = os.environ.get("TIMEFRAME", "15m")
    mode = ExecutionMode(os.environ.get("EXECUTION_MODE", "DRY_RUN"))

    try:
        resp = http_client.post(
            f"{gateway_url}/brief",
            json={"symbol": symbol, "timeframe": timeframe, "mode": mode},
            headers=_gateway_headers(token),
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(
            json.dumps({"executed": False, "reason": "gateway_error", "detail": str(exc)}),
            file=sys.stderr,
        )
        return 1

    # Pré-análise determinística do Mulham, redis-first: lê o brief do Redis (espelhado
    # pelo gateway acima) e grava os sinais em binance:strategist:mulham:<symbol>. O LLM
    # é instruído (SOUL/AGENTS + prompts do cron/webhook) a consumir isso como fato ANTES
    # de qualquer raciocínio caro — tira a "leitura de gráfico" dos turnos pagos e evita
    # re-analisar estados de mercado quase idênticos a cada heartbeat/evento.
    try:
        import subprocess
        subprocess.run(
            ["python", "scripts/mulham_analyzer.py", "--symbol", symbol],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass  # analyzer é best-effort; o agente ainda lê o brief cru do Redis se falhar

    print(f"{_BRIEF_KEY_PREFIX}{symbol}")
    return 0


def _cmd_execute(
    proposal_ref: str, *, http_client: httpx.Client, redis_client: object = None
) -> int:
    """Metade 2: envia proposta ao gateway e repassa o resultado (redis-first).

    A proposta é lida do Redis do agente via referência "redis:<key>" (handoff oficial,
    sem filesystem). Valida schema localmente (só pydantic, sem dogmas) e POST /execute,
    repassando o corpo exato.
    """
    config = _load_gateway_config()
    if config is None:
        return _emit_error("missing_gateway_config")

    gateway_url, token = config

    if not proposal_ref.startswith("redis:"):
        return _emit(
            {
                "executed": False,
                "reason": "invalid_proposal",
                "detail": "handoff é redis-first: use redis:<key>",
            }
        )

    # Carrega o conteúdo da proposta do Redis (REDIS_HOST/REDIS_PORT do env).
    try:
        key = proposal_ref[len("redis:") :]
        r = redis_client if redis_client is not None else _build_redis()
        raw = r.get(key)
        if raw is None:
            return _emit({"executed": False, "reason": "invalid_proposal", "detail": f"redis key not found: {key}"})
        # raw já é str por causa de decode_responses

        StrategyProposal.model_validate_json(raw)
    except ValidationError as exc:
        return _emit({"executed": False, "reason": "invalid_proposal", "detail": exc.errors()})
    except Exception as exc:  # redis etc.
        return _emit({"executed": False, "reason": "invalid_proposal", "detail": str(exc)})

    # Envia ao gateway (corpo = raw exato)
    try:
        resp = http_client.post(
            f"{gateway_url}/execute",
            content=raw.encode() if isinstance(raw, str) else raw,
            headers={
                **_gateway_headers(token),
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _emit_error("gateway_error", f"HTTP {exc.response.status_code}")
    except httpx.HTTPError as exc:
        return _emit_error("gateway_error", str(exc))

    return _emit(resp.json())


def main(
    argv: Optional[list[str]] = None,
    *,
    http_client: Optional[httpx.Client] = None,
    redis_client: object = None,
) -> int:
    """Ponto de entrada do CLI. argv injetável para testes; http_client é a fronteira
    de I/O do gateway e redis_client a do Redis do agente (defaults = clientes reais).
    """
    parser = argparse.ArgumentParser(prog="strategist_cycle")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("brief", help="solicita Brief ao gateway (handoff via Redis; imprime a chave)")
    p_exec = sub.add_parser("execute", help="envia proposta (redis:KEY) ao gateway e repassa resultado")
    p_exec.add_argument("proposal", help="referência redis:KEY da StrategyProposal")

    args = parser.parse_args(argv)

    client = http_client if http_client is not None else _build_http_client()

    if args.command == "brief":
        return _cmd_brief(http_client=client)
    return _cmd_execute(args.proposal, http_client=client, redis_client=redis_client)


if __name__ == "__main__":
    sys.exit(main())
