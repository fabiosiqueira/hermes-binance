# CLI do ciclo do estrategista Hermes (thin-client HTTP do Risk Gateway).
#
# Este módulo é um CLIENTE BURRO: não valida dogmas, não chama betrader, não toca
# Redis. Todo enforcement de risco fica no gateway (risk_gateway.py). O agente LLM
# (HAWK) só fala com o gateway via HTTP.
#
# Contrato de stdout (inalterado): `brief` imprime o PATH absoluto do brief.json;
# `execute` imprime JSON {"executed": ...}. NUNCA traceback cru.
#
# Env vars necessários do lado do cliente:
#   GATEWAY_URL    ex.: http://risk-gateway:8647
#   GATEWAY_TOKEN  token de autenticação do gateway
#   (brief apenas) SYMBOL, TIMEFRAME, EXECUTION_MODE
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from pydantic import ValidationError

from schemas import ExecutionMode, StrategyProposal

# Workspace dos artefatos do ciclo (brief.json, proposal.json) — relativo ao cwd
# do agente (que roda a partir de hermes/), conforme AGENTS.md.
_WORKSPACE = Path("workspace")
_BRIEF_PATH = _WORKSPACE / "brief.json"
_PROPOSAL_PATH = _WORKSPACE / "proposal.json"

_AUTH_HEADER = "Authorization"
_BEARER_PREFIX = "Bearer "


def _gateway_headers(token: str) -> dict:
    return {_AUTH_HEADER: f"{_BEARER_PREFIX}{token}"}


def _emit(payload: dict) -> int:
    """Imprime o resumo JSON do ciclo no stdout e retorna exit code 0."""
    print(json.dumps(payload))
    return 0


def _emit_error(reason: str, detail: str = "") -> int:
    payload: dict = {"executed": False, "reason": reason}
    if detail:
        payload["detail"] = detail
    return _emit(payload)


def _load_gateway_config() -> tuple[str, str] | None:
    """Lê GATEWAY_URL e GATEWAY_TOKEN do env. Retorna None se ausentes."""
    url = os.environ.get("GATEWAY_URL", "").strip()
    token = os.environ.get("GATEWAY_TOKEN", "").strip()
    if not url or not token:
        return None
    return url, token


def _cmd_brief(*, http_client: httpx.Client) -> int:
    """Metade 1: solicita Brief ao gateway e escreve workspace/brief.json.

    Envia {symbol, timeframe, mode} ao POST /brief do gateway; escreve a resposta
    JSON em workspace/brief.json e imprime o path absoluto.
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

    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    _BRIEF_PATH.write_text(resp.text, encoding="utf-8")
    print(str(_BRIEF_PATH.resolve()))
    return 0


def _cmd_execute(proposal_path: str, *, http_client: httpx.Client) -> int:
    """Metade 2: envia proposta ao gateway e repassa o resultado.

    Lê workspace/proposal.json, valida schema localmente (captura inválidos antes
    de trafegar pela rede) e POST /execute ao gateway. Repassa o JSON da resposta
    fielmente, sem qualquer reinterpretação de risco.
    """
    config = _load_gateway_config()
    if config is None:
        return _emit_error("missing_gateway_config")

    gateway_url, token = config

    # Carrega e valida a proposta localmente (só schema, sem regras de risco).
    try:
        raw = Path(proposal_path).read_text(encoding="utf-8")
        StrategyProposal.model_validate_json(raw)
    except ValidationError as exc:
        return _emit({"executed": False, "reason": "invalid_proposal", "detail": exc.errors()})
    except OSError as exc:
        return _emit({"executed": False, "reason": "invalid_proposal", "detail": str(exc)})

    # Envia ao gateway (corpo = raw JSON exato lido do arquivo).
    try:
        resp = http_client.post(
            f"{gateway_url}/execute",
            content=raw.encode(),
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
) -> int:
    """Ponto de entrada do CLI. argv injetável para testes; http_client é a fronteira
    de I/O do gateway (default = httpx.Client real).
    """
    parser = argparse.ArgumentParser(prog="strategist_cycle")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("brief", help="solicita Brief ao gateway e escreve workspace/brief.json")
    p_exec = sub.add_parser("execute", help="envia proposta ao gateway e repassa resultado")
    p_exec.add_argument("proposal", help="path do proposal.json (StrategyProposal)")

    args = parser.parse_args(argv)

    client = http_client if http_client is not None else httpx.Client()

    if args.command == "brief":
        return _cmd_brief(http_client=client)
    return _cmd_execute(args.proposal, http_client=client)


if __name__ == "__main__":
    sys.exit(main())
