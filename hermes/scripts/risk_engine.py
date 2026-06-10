# Gate de risco determinístico (in-process, M1).
#
# Aplica os Dogmas sobre a StrategyProposal escrita pelo Hermes. É a constituição
# que torna a liberdade do LLM segura: o LLM compõe, o gate veta. Em F2 este módulo
# vira serviço separado (Risk Gateway) sem mudar o contrato StrategyProposal.
#
# `validate` é FUNÇÃO PURA: sem I/O, sem env, sem mutação dos inputs. O kill switch
# (emergency_stop) é checado pelo caller no início do ciclo, não aqui.
import os
from dataclasses import dataclass, field

from schemas import Brief, Dogmas, EntryOrder, StrategyProposal


@dataclass
class GateResult:
    """Resultado do gate. `violations` acumula TODAS as razões de rejeição.

    Não é fail-fast: o reasoning completo de rejeição volta pro LLM aprender.
    `reason` = "; ".join(violations) quando rejeitada, senão None.
    """

    ok: bool
    reason: str | None = None
    violations: list[str] = field(default_factory=list)


def check_emergency_stop() -> bool:
    """Lê EMERGENCY_STOP do ambiente. "true"/"1" (case-insensitive) = ativado.

    Kill switch via env, checado pelo caller no início de cada execução. SEM raise.
    """
    return os.environ.get("EMERGENCY_STOP", "").strip().lower() in ("true", "1")


def _entry_ref(entry: EntryOrder, brief: Brief) -> float | None:
    """Preço de referência da entrada: limit_price se LIMIT, senão último close.

    Retorna None se MARKET sem candles disponíveis (não dá pra aferir distância).
    """
    if entry.order_type == "LIMIT":
        return entry.limit_price
    if brief.market.candles:
        return brief.market.candles[-1].close
    return None


def validate(proposal: StrategyProposal, dogmas: Dogmas, brief: Brief) -> GateResult:
    """Valida a proposta contra os Dogmas, acumulando TODAS as violações.

    Pura: sem I/O, sem env, sem mutação dos inputs. Automations/teardown (gestão de
    saída, redução de risco) nunca são bloqueados — só as entradas novas passam pelo gate.
    """
    violations: list[str] = []

    # Exposição agregada: posições abertas convertidas pra % do equity corrente.
    equity = brief.portfolio.equity
    exposicao_posicoes_pct = 0.0
    if equity > 0:
        for pos in brief.portfolio.positions:
            exposicao_posicoes_pct += pos.entry_price * pos.quantity / equity * 100

    # Drawdown diário estourado veta QUALQUER entrada nova (reduzir risco segue livre).
    drawdown_estourado = brief.risk_state.drawdown_pct >= dogmas.max_daily_drawdown_pct

    soma_sizing_entries = 0.0

    for idx, entry in enumerate(proposal.entries):
        # 1. symbol fora dos permitidos.
        if entry.symbol not in dogmas.allowed_symbols:
            violations.append(
                f"entry[{idx}]: symbol {entry.symbol} fora de allowed_symbols"
            )

        # 2. leverage acima do teto (== teto passa, > viola).
        if entry.leverage > dogmas.max_leverage:
            violations.append(
                f"entry[{idx}]: leverage {entry.leverage} > max_leverage "
                f"{dogmas.max_leverage}"
            )

        # 3. sizing individual acima do teto (== teto passa, > viola).
        if entry.sizing_pct > dogmas.max_position_pct_equity:
            violations.append(
                f"entry[{idx}]: sizing_pct {entry.sizing_pct} > "
                f"max_position_pct_equity {dogmas.max_position_pct_equity}"
            )
        soma_sizing_entries += entry.sizing_pct

        # 4. drawdown diário estourado bloqueia entrada nova.
        if drawdown_estourado:
            violations.append(
                f"entry[{idx}]: drawdown_pct {brief.risk_state.drawdown_pct} >= "
                f"max_daily_drawdown_pct {dogmas.max_daily_drawdown_pct}"
            )

        # 5. distância do stop e lado do stop.
        ref = _entry_ref(entry, brief)
        if ref is None or ref <= 0:
            violations.append(
                f"entry[{idx}]: preço de referência indisponível para aferir stop"
            )
        else:
            distancia_pct = abs(ref - entry.stop_loss) / ref * 100
            if distancia_pct < dogmas.min_stop_distance_pct:
                violations.append(
                    f"entry[{idx}]: distância do stop {distancia_pct:.4f}% < "
                    f"min_stop_distance_pct {dogmas.min_stop_distance_pct}%"
                )
            # Stop do lado errado: BUY com stop >= ref, SELL com stop <= ref.
            if entry.side == "BUY" and entry.stop_loss >= ref:
                violations.append(
                    f"entry[{idx}]: BUY com stop_loss {entry.stop_loss} >= "
                    f"entry_ref {ref} (lado errado)"
                )
            if entry.side == "SELL" and entry.stop_loss <= ref:
                violations.append(
                    f"entry[{idx}]: SELL com stop_loss {entry.stop_loss} <= "
                    f"entry_ref {ref} (lado errado)"
                )

    # 3 (agregado): exposição total (entries + posições abertas) acima do teto.
    if proposal.entries:
        exposicao_total = soma_sizing_entries + exposicao_posicoes_pct
        if exposicao_total > dogmas.max_position_pct_equity:
            violations.append(
                f"exposição agregada {exposicao_total:.4f}% > "
                f"max_position_pct_equity {dogmas.max_position_pct_equity}% "
                f"(entries {soma_sizing_entries:.4f}% + posições "
                f"{exposicao_posicoes_pct:.4f}%)"
            )

    if violations:
        return GateResult(ok=False, reason="; ".join(violations), violations=violations)
    return GateResult(ok=True)
