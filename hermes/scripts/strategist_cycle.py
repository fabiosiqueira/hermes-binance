# CLI do ciclo do estrategista Hermes (duas metades: brief + execute).
#
# Orquestra os 4 módulos do Cluster 3 (schemas, risk_engine, betrader_client,
# observability) seguindo o contrato documentado em hermes/AGENTS.md ("Ciclo do
# estrategista") e na spec de design. NÃO contém lógica de risco ou de I/O própria —
# só compõe os módulos na ordem exata do spec.
#
# Consumidor do stdout é o agente LLM (HAWK): a saída é SEMPRE JSON ou um path
# absoluto, NUNCA traceback cru. Todo catch de I/O externo chama record_error(type)
# e segue o contrato JSON.
#
# Imports são flat (from schemas import ...) porque os módulos do Cluster 3 também
# importam assim; o diretório do script entra no sys.path quando rodado como
# `python scripts/strategist_cycle.py ...`.
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

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

# Workspace dos artefatos do ciclo (brief.json, proposal.json) — relativo ao cwd
# do agente (que roda a partir de hermes/), conforme AGENTS.md.
_WORKSPACE = Path("workspace")
_BRIEF_PATH = _WORKSPACE / "brief.json"

# Dogmas vivem ao lado do data dir, relativo ao próprio script (não ao cwd).
_DOGMAS_PATH = Path(__file__).parent.parent / "dogmas.yaml"


def _build_redis():
    """Constrói o cliente Redis da fronteira de I/O a partir do env.

    REDIS_HOST/REDIS_PORT conforme AGENTS.md. decode_responses=True para que o
    FinancialState leia strings (não bytes) na desserialização.
    """
    import redis

    host = os.environ.get("REDIS_HOST", "localhost")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def _ref_price(brief: Brief) -> Optional[float]:
    """Último close do brief para dimensionar ordens MARKET (contrato do Cluster 3)."""
    if brief.market.candles:
        return brief.market.candles[-1].close
    return None


def _cmd_brief(
    *,
    redis_client: object,
    observability: Observability,
) -> int:
    """Metade 1: monta o Brief e escreve workspace/brief.json.

    Lê env (SYMBOL/TIMEFRAME/EXECUTION_MODE), garante monitor, carrega o estado
    financeiro do Redis e busca o brief no betrader. Única saída no stdout é o path
    absoluto do brief.json.
    """
    symbol = os.environ.get("SYMBOL", "BTCUSDT")
    timeframe = os.environ.get("TIMEFRAME", "15m")
    mode = ExecutionMode(os.environ.get("EXECUTION_MODE", "DRY_RUN"))

    client = BetraderClient.from_env(on_error=observability.record_error)
    try:
        client.ensure_monitor(symbol, timeframe)
        state = FinancialState.load(redis_client)
        brief = client.fetch_brief(symbol, timeframe, mode, state.to_risk_state())
    finally:
        client.close()

    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    _BRIEF_PATH.write_text(brief.model_dump_json(indent=2), encoding="utf-8")
    print(str(_BRIEF_PATH.resolve()))
    return 0


def _emit(payload: dict) -> int:
    """Imprime o resumo JSON do ciclo no stdout e retorna exit code 0."""
    print(json.dumps(payload))
    return 0


def _cmd_execute(
    proposal_path: str,
    *,
    redis_client: object,
    observability: Observability,
) -> int:
    """Metade 2: gate + execução da proposta, na ordem exata do spec.

    Contrato de stdout: SEMPRE JSON {"executed": ...}; nunca traceback. Estado
    financeiro persistido ANTES de imprimir o resultado (integridade bot.md).
    """
    # (a) kill switch via env, checado no início — sem throw.
    if check_emergency_stop():
        return _emit({"executed": False, "reason": "emergency_stop"})

    # (b) carrega a proposta; ValidationError vira JSON (o agente lê e corrige).
    try:
        raw = Path(proposal_path).read_text(encoding="utf-8")
        proposal = StrategyProposal.model_validate_json(raw)
    except ValidationError as exc:
        return _emit(
            {"executed": False, "reason": "invalid_proposal", "detail": exc.errors()}
        )
    except OSError as exc:
        observability.record_error("proposal_read_error")
        return _emit(
            {"executed": False, "reason": "invalid_proposal", "detail": str(exc)}
        )

    # (c) recarrega o brief do workspace + dogmas.
    try:
        brief = Brief.model_validate_json(_BRIEF_PATH.read_text(encoding="utf-8"))
        dogmas = load_dogmas(_DOGMAS_PATH)
    except (OSError, ValidationError) as exc:
        observability.record_error("brief_reload_error")
        return _emit(
            {"executed": False, "reason": "invalid_proposal", "detail": str(exc)}
        )

    state = FinancialState.load(redis_client)
    maybe_trigger_drawdown_wake(
        state,
        dogmas.max_daily_drawdown_pct,
        on_error=observability.record_error,
    )

    # (d) gate determinístico.
    result = validate(proposal, dogmas, brief)
    if not result.ok:
        observability.record_decision(
            proposal.model_dump(), gate_ok=False, reason=result.reason, redis=redis_client
        )
        return _emit(
            {
                "executed": False,
                "reason": "gate_rejected",
                "violations": result.violations,
            }
        )

    # (e) aprovado → execução. teardown → entries → install_automations.
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

    # (f) observability: decisão + ciclo, e PERSISTE o estado ANTES de imprimir.
    observability.record_decision(
        proposal.model_dump(), gate_ok=True, reason=None, redis=redis_client
    )
    observability.record_cycle()
    state.persist(redis_client)

    # (g) resumo do ciclo.
    return _emit(
        {
            "executed": True,
            "orders": orders,
            "automations": automations,
            "errors": errors,
        }
    )


def main(
    argv: Optional[list[str]] = None,
    *,
    redis_client: object = None,
    observability: Optional[Observability] = None,
) -> int:
    """Ponto de entrada do CLI. argv injetável para testes; idem redis/observability
    (fronteiras de I/O), default = clientes reais a partir do env.
    """
    parser = argparse.ArgumentParser(prog="strategist_cycle")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("brief", help="monta o Brief e escreve workspace/brief.json")
    p_exec = sub.add_parser("execute", help="gate + execução de uma proposta")
    p_exec.add_argument("proposal", help="path do proposal.json (StrategyProposal)")

    args = parser.parse_args(argv)

    redis_client = redis_client if redis_client is not None else _build_redis()
    observability = observability if observability is not None else Observability()

    if args.command == "brief":
        return _cmd_brief(redis_client=redis_client, observability=observability)
    return _cmd_execute(
        args.proposal, redis_client=redis_client, observability=observability
    )


if __name__ == "__main__":
    sys.exit(main())
