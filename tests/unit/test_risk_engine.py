# Unit tests do gate de risco determinístico (risk_engine).
# Um teste isolado por dogma (caso passa + caso viola, com boundary explícito),
# mais exposição agregada, gestão pura sob drawdown estourado e emergency_stop.
# DI real: objetos schemas concretos, sem mocks de schemas.
from schemas import (
    AutomationSpec,
    Brief,
    Candle,
    Dogmas,
    EntryOrder,
    ExecutionMode,
    MarketState,
    Portfolio,
    Position,
    RiskState,
    StrategyProposal,
)

from risk_engine import GateResult, check_emergency_stop, validate


# --- Fixtures: Dogmas/Brief mínimos válidos ---


def _dogmas() -> Dogmas:
    return Dogmas(
        max_leverage=5,
        max_position_pct_equity=10.0,
        max_daily_drawdown_pct=3.0,
        mandatory_stop_loss=True,
        min_stop_distance_pct=0.5,
        allowed_symbols=["BTCUSDT"],
    )


def _brief(
    *,
    drawdown_pct: float = 0.0,
    equity: float = 10000.0,
    positions: list[Position] | None = None,
    last_close: float = 60000.0,
) -> Brief:
    return Brief(
        timestamp="2026-06-09T12:00:00Z",
        mode=ExecutionMode.DRY_RUN,
        market=MarketState(
            symbol="BTCUSDT",
            timeframe="1h",
            candles=[
                Candle(
                    open_time=1717934400000,
                    open=59900.0,
                    high=60500.0,
                    low=59800.0,
                    close=last_close,
                    volume=123.45,
                ),
            ],
        ),
        portfolio=Portfolio(
            equity=equity,
            balance=equity,
            positions=positions or [],
            used_leverage=0.0,
        ),
        risk_state=RiskState(
            daily_pnl=0.0,
            drawdown_pct=drawdown_pct,
            equity_curve_ref="hermes:equity_curve:BTCUSDT",
        ),
    )


def _entry(
    *,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    sizing_pct: float = 5.0,
    order_type: str = "LIMIT",
    limit_price: float | None = 60000.0,
    stop_loss: float = 59000.0,
    leverage: int = 2,
) -> EntryOrder:
    return EntryOrder(
        symbol=symbol,
        side=side,
        sizing_pct=sizing_pct,
        order_type=order_type,
        limit_price=limit_price,
        stop_loss=stop_loss,
        leverage=leverage,
    )


# --- Baseline: proposta válida passa ---


def test_proposta_valida_passa() -> None:
    result = validate(StrategyProposal(reasoning="ok", entries=[_entry()]), _dogmas(), _brief())
    assert result.ok is True
    assert result.reason is None
    assert result.violations == []


# --- Dogma 1: symbol fora de allowed_symbols ---


def test_symbol_fora_de_allowed_symbols_rejeita() -> None:
    proposal = StrategyProposal(
        reasoning="x",
        entries=[_entry(symbol="ETHUSDT", limit_price=3000.0, stop_loss=2900.0)],
    )
    result = validate(proposal, _dogmas(), _brief())
    assert result.ok is False
    assert any("ETHUSDT" in v for v in result.violations)


# --- Dogma 2: leverage > max_leverage (boundary: == teto passa, > viola) ---


def test_leverage_no_teto_passa() -> None:
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(leverage=5)]), _dogmas(), _brief()
    )
    assert result.ok is True


def test_leverage_acima_do_teto_rejeita() -> None:
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(leverage=6)]), _dogmas(), _brief()
    )
    assert result.ok is False
    assert any("leverage" in v.lower() for v in result.violations)


# --- Dogma 3: sizing_pct > max_position_pct_equity (boundary: == passa, > viola) ---


def test_sizing_pct_no_teto_passa() -> None:
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(sizing_pct=10.0)]), _dogmas(), _brief()
    )
    assert result.ok is True


def test_sizing_pct_acima_do_teto_rejeita() -> None:
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(sizing_pct=10.5)]), _dogmas(), _brief()
    )
    assert result.ok is False
    assert any("sizing" in v.lower() or "exposi" in v.lower() for v in result.violations)


# --- Dogma 3 (agregado): entries + posições abertas não excede o teto ---


def test_exposicao_agregada_com_posicao_aberta_rejeita() -> None:
    # Posição aberta: 60000 * 0.01 = 600 USD = 6% de equity 10000.
    # Entry de 5% + 6% = 11% > 10% → rejeita por exposição agregada.
    posicao = Position(
        symbol="BTCUSDT",
        side="BUY",
        entry_price=60000.0,
        quantity=0.01,
        unrealized_pnl=0.0,
        leverage=2,
    )
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(sizing_pct=5.0)]),
        _dogmas(),
        _brief(positions=[posicao]),
    )
    assert result.ok is False
    assert any("exposi" in v.lower() or "agregad" in v.lower() for v in result.violations)


def test_exposicao_agregada_no_teto_passa() -> None:
    # Posição: 400 USD = 4% + entry 6% = 10% == teto → passa.
    posicao = Position(
        symbol="BTCUSDT",
        side="BUY",
        entry_price=40000.0,
        quantity=0.01,
        unrealized_pnl=0.0,
        leverage=2,
    )
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(sizing_pct=6.0)]),
        _dogmas(),
        _brief(positions=[posicao]),
    )
    assert result.ok is True


# --- Dogma 4: drawdown >= max_daily_drawdown_pct rejeita entrada nova ---


def test_drawdown_no_teto_rejeita() -> None:
    # boundary: drawdown >= teto viola.
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry()]),
        _dogmas(),
        _brief(drawdown_pct=3.0),
    )
    assert result.ok is False
    assert any("drawdown" in v.lower() for v in result.violations)


def test_drawdown_abaixo_do_teto_passa() -> None:
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry()]),
        _dogmas(),
        _brief(drawdown_pct=2.99),
    )
    assert result.ok is True


# --- Dogma 4: gestão pura (sem entries) passa mesmo com drawdown estourado ---


def test_gestao_pura_passa_com_drawdown_estourado() -> None:
    proposal = StrategyProposal(
        reasoning="reduzir risco",
        automations=[
            AutomationSpec(
                name="exit",
                condition="MEMORY['BTCUSDT:RSI_14'] > 70",
                action={"type": "ORDER", "side": "SELL", "reduceOnly": True},
            )
        ],
        teardown=["order-old-1"],
    )
    result = validate(proposal, _dogmas(), _brief(drawdown_pct=10.0))
    assert result.ok is True


# --- Dogma 6: proposta sem entries (gestão pura) é válida ---


def test_proposta_sem_entries_passa() -> None:
    proposal = StrategyProposal(reasoning="nada a fazer", teardown=["auto-1"])
    result = validate(proposal, _dogmas(), _brief())
    assert result.ok is True


# --- Dogma 5: distância do stop < min_stop_distance_pct (boundary: == passa, < viola) ---


def test_stop_distance_no_minimo_passa() -> None:
    # 60000 → 59700 = 0.5% == mínimo → passa.
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(stop_loss=59700.0)]),
        _dogmas(),
        _brief(),
    )
    assert result.ok is True


def test_stop_distance_abaixo_do_minimo_rejeita() -> None:
    # 60000 → 59800 = 0.333% < 0.5% → rejeita.
    result = validate(
        StrategyProposal(reasoning="x", entries=[_entry(stop_loss=59800.0)]),
        _dogmas(),
        _brief(),
    )
    assert result.ok is False
    assert any("stop" in v.lower() for v in result.violations)


def test_stop_distance_usa_last_close_para_market() -> None:
    # MARKET: entry_ref = último close (60000). SL 59800 = 0.333% < 0.5% → rejeita.
    result = validate(
        StrategyProposal(
            reasoning="x",
            entries=[_entry(order_type="MARKET", limit_price=None, stop_loss=59800.0)],
        ),
        _dogmas(),
        _brief(last_close=60000.0),
    )
    assert result.ok is False
    assert any("stop" in v.lower() for v in result.violations)


# --- Dogma 5: stop do lado errado ---
# Nota: EntryOrder valida o lado para LIMIT no schema; o gate cobre o caso MARKET,
# onde a coerência só é checável contra o entry_ref (último close).


def test_stop_lado_errado_buy_market_rejeita() -> None:
    # BUY MARKET: stop >= entry_ref (last_close) → lado errado.
    result = validate(
        StrategyProposal(
            reasoning="x",
            entries=[_entry(order_type="MARKET", limit_price=None, stop_loss=60500.0)],
        ),
        _dogmas(),
        _brief(last_close=60000.0),
    )
    assert result.ok is False


def test_stop_lado_errado_sell_market_rejeita() -> None:
    # SELL MARKET: stop <= entry_ref → lado errado.
    result = validate(
        StrategyProposal(
            reasoning="x",
            entries=[_entry(side="SELL", order_type="MARKET", limit_price=None, stop_loss=59500.0)],
        ),
        _dogmas(),
        _brief(last_close=60000.0),
    )
    assert result.ok is False


# --- Acumula TODAS as violações (não fail-fast) ---


def test_acumula_multiplas_violacoes() -> None:
    proposal = StrategyProposal(
        reasoning="x",
        entries=[
            _entry(
                symbol="ETHUSDT",
                sizing_pct=20.0,
                leverage=10,
                limit_price=3000.0,
                stop_loss=2900.0,
            )
        ],
    )
    result = validate(proposal, _dogmas(), _brief())
    assert result.ok is False
    # symbol + leverage + sizing → ao menos 3 violações acumuladas.
    assert len(result.violations) >= 3
    assert result.reason == "; ".join(result.violations)


# --- check_emergency_stop via env (sem raise) ---


def test_emergency_stop_ativado_true(monkeypatch) -> None:
    monkeypatch.setenv("EMERGENCY_STOP", "true")
    assert check_emergency_stop() is True


def test_emergency_stop_ativado_um(monkeypatch) -> None:
    monkeypatch.setenv("EMERGENCY_STOP", "1")
    assert check_emergency_stop() is True


def test_emergency_stop_case_insensitive(monkeypatch) -> None:
    monkeypatch.setenv("EMERGENCY_STOP", "TRUE")
    assert check_emergency_stop() is True


def test_emergency_stop_desativado_por_default(monkeypatch) -> None:
    monkeypatch.delenv("EMERGENCY_STOP", raising=False)
    assert check_emergency_stop() is False


def test_emergency_stop_valor_arbitrario_desativado(monkeypatch) -> None:
    monkeypatch.setenv("EMERGENCY_STOP", "no")
    assert check_emergency_stop() is False


# --- validate é função pura: não muta inputs ---


def test_validate_nao_muta_inputs() -> None:
    dogmas = _dogmas()
    brief = _brief()
    proposal = StrategyProposal(reasoning="x", entries=[_entry()])
    snapshot = proposal.model_dump()
    validate(proposal, dogmas, brief)
    assert proposal.model_dump() == snapshot


def test_gate_result_default_violations_vazio() -> None:
    assert GateResult(ok=True).violations == []
