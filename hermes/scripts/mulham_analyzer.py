#!/usr/bin/env python3
"""
mulham_analyzer.py — Camada determinística dos ensinamentos de @MulhamTrading.

Roda sobre o Brief (principalmente market.candles) e produz sinais estruturados
que o LLM deve consumir ANTES de qualquer raciocínio caro.

Uso (redis-first, sem filesystem):
  python scripts/mulham_analyzer.py --symbol BTCUSDT
  (lê o brief de binance:strategist:brief:<symbol> e grava os sinais em
   binance:strategist:mulham:<symbol>, ambos no Redis do agente via REDIS_HOST/PORT)

O output é 100% determinístico (sem LLM). Inclui:
- bias atual (via structure + CCT)
- high_prob_ranges detectados (W seguido de S / displacement)
- candidates de 1-rectangle (sniper entries)
- signature para detecção de mudança material
- material_change flag (heurística simples)

O LLM (via SOUL/AGENTS e prompts do cron/webhook) é instruído a rodar isto primeiro
e tratar o resultado como fato. Isso evita que o modelo re-analise a mesma série de
candles em ciclos curtos ou eventos repetidos — economizando tokens pagos.

Não depende de estado externo além do brief fornecido. Usa apenas stdlib + schemas do projeto.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Any

from schemas import Brief, Candle


@dataclass
class HighProbRange:
    start_idx: int
    end_idx: int
    direction: str  # "up" | "down"
    weakness_type: str
    strength_type: str
    rr_potential: float | None = None   # rough, based on range height vs next opposing swing


@dataclass
class RectCandidate:
    """1-Rectangle (weakness rectangle) style entry candidate, per Mulham videos."""
    tf: str
    weakness_idx: int
    rect_high: float
    rect_low: float
    sl: float
    tp: float | None
    rr: float | None
    side: str  # BUY or SELL
    key_level_ref: str | None = None
    confidence: str = "medium"


@dataclass
class MulhamSignals:
    bias: str  # "bullish" | "bearish" | "neutral"
    bias_method: str
    high_prob_ranges: list[HighProbRange]
    rect_candidates: list[RectCandidate]
    cct_last: dict[str, Any] | None
    material_change: bool
    signature: str
    notes: list[str]


def _swing_points(candles: list[Candle], lookback: int = 5) -> list[tuple[int, str, float]]:
    """
    Very simple swing detection (higher high / lower low with confirmation).
    Returns list of (idx, 'high'|'low', price).
    Good enough for deterministic pre-filter; LLM can refine with full context.
    """
    swings: list[tuple[int, str, float]] = []
    n = len(candles)
    if n < 3:
        return swings

    for i in range(1, n - 1):
        c = candles[i]
        prev = candles[i - 1]
        nxt = candles[i + 1]

        # Local high
        if c.high > prev.high and c.high > nxt.high:
            swings.append((i, "high", c.high))
        # Local low
        if c.low < prev.low and c.low < nxt.low:
            swings.append((i, "low", c.low))

    return swings[-lookback * 2:]  # recent ones


def _is_strong_close(c: Candle, direction: str, threshold: float = 0.6) -> bool:
    """Rough displacement/strength: body occupies good portion of the range in the direction."""
    body = abs(c.close - c.open)
    rng = max(c.high - c.low, 1e-9)
    if direction == "up":
        return c.close > c.open and (body / rng) > threshold
    else:
        return c.close < c.open and (body / rng) > threshold


def detect_high_prob_ranges(candles: list[Candle]) -> list[HighProbRange]:
    """
    Mulham "weakness followed by strength" = high probability range.
    Weakness: failure to close beyond a swing (wick rejection or inside close).
    Strength: subsequent strong BOS-style close beyond the opposing swing.
    """
    if len(candles) < 6:
        return []

    swings = _swing_points(candles, lookback=8)
    ranges: list[HighProbRange] = []

    for j in range(len(swings) - 2):
        (i1, typ1, p1), (i2, typ2, p2), (i3, typ3, p3) = swings[j : j + 3]

        # Simple W then S pattern for bearish continuation example:
        # Recent high (typ1=high), then failure to hold above it (weakness), then strong break of a lower low.
        if typ1 == "high" and typ2 == "low":
            # Check weakness around i1-i2 area
            w_idx = i2
            w_candle = candles[w_idx]
            failed_to_hold = w_candle.close < candles[i1].high * 0.999  # failed above the high

            # Look for strength after: strong move below the low
            for k in range(w_idx + 1, min(w_idx + 4, len(candles))):
                ck = candles[k]
                if ck.low < p2 and _is_strong_close(ck, "down"):
                    r = HighProbRange(
                        start_idx=i1,
                        end_idx=k,
                        direction="down",
                        weakness_type="fail_hold_high",
                        strength_type="strong_break_low",
                        rr_potential=round((p2 - min(c.low for c in candles[w_idx:k+1])) / max(ck.high - ck.low, 1e-9), 1) if (ck.high - ck.low) > 0 else None,
                    )
                    ranges.append(r)
                    break

        # Symmetric for bullish
        if typ1 == "low" and typ2 == "high":
            w_idx = i2
            w_candle = candles[w_idx]
            failed_to_hold = w_candle.close > candles[i1].low * 1.001
            for k in range(w_idx + 1, min(w_idx + 4, len(candles))):
                ck = candles[k]
                if ck.high > p2 and _is_strong_close(ck, "up"):
                    r = HighProbRange(
                        start_idx=i1,
                        end_idx=k,
                        direction="up",
                        weakness_type="fail_hold_low",
                        strength_type="strong_break_high",
                        rr_potential=round((max(c.high for c in candles[w_idx:k+1]) - p2) / max(ck.high - ck.low, 1e-9), 1) if (ck.high - ck.low) > 0 else None,
                    )
                    ranges.append(r)
                    break

    return ranges[-3:]  # keep recent


def compute_cct_bias(candles: list[Candle]) -> dict[str, Any] | None:
    """One-candle / CCT style bias from the last meaningful candle."""
    if len(candles) < 2:
        return None

    prev = candles[-2]
    curr = candles[-1]

    # Continuation bullish: curr opens below prev high-ish and closes strong above prev high
    if curr.open < prev.high and curr.close > prev.high and _is_strong_close(curr, "up"):
        return {"type": "continuation", "direction": "bullish", "ref_candle": -2}

    if curr.open > prev.low and curr.close < prev.low and _is_strong_close(curr, "down"):
        return {"type": "continuation", "direction": "bearish", "ref_candle": -2}

    # Weakness / reversal signal
    if curr.open > prev.low and curr.close < prev.high * 0.995:  # failed to continue up
        return {"type": "weakness", "direction": "bearish", "ref_candle": -1}

    if curr.open < prev.high and curr.close > prev.low * 1.005:
        return {"type": "weakness", "direction": "bullish", "ref_candle": -1}

    return {"type": "neutral", "direction": "neutral", "ref_candle": -1}


def find_rect_candidates(candles: list[Candle], ranges: list[HighProbRange]) -> list[RectCandidate]:
    """Basic 1-rectangle / weakness rect candidates (Mulham sniper setup)."""
    candidates: list[RectCandidate] = []
    if len(candles) < 5:
        return candidates

    recent = candles[-6:]

    for i, c in enumerate(recent[:-1]):
        idx = len(candles) - len(recent) + i

        # Bearish weakness at a high: bearish candle takes a local high but closes back inside
        if c.close < c.open:  # bearish candle
            # simplistic: if this candle's high is higher than prev 2 and next candle didn't confirm above
            if idx > 2:
                prev_high = max(candles[idx-2].high, candles[idx-1].high)
                if c.high > prev_high and c.close < prev_high:
                    # potential rect from close to high
                    rect_low = c.close
                    rect_high = c.high
                    sl = rect_high * 1.001  # slightly above
                    # TP rough: use last low or range height * 3-4
                    tp = min(c.low for c in candles[max(0, idx-4):idx]) 
                    rr = None
                    if sl > rect_low:
                        risk = sl - rect_low
                        reward = (rect_low - tp) if tp < rect_low else risk * 3
                        rr = round(reward / max(risk, 1e-9), 1)

                    candidates.append(RectCandidate(
                        tf="15m",
                        weakness_idx=idx,
                        rect_high=rect_high,
                        rect_low=rect_low,
                        sl=sl,
                        tp=tp,
                        rr=rr,
                        side="SELL",
                        key_level_ref="prior_swing_high",
                        confidence="medium" if rr and rr > 2.5 else "low",
                    ))

        # Bullish weakness at a low (symmetric)
        if c.close > c.open:
            if idx > 2:
                prev_low = min(candles[idx-2].low, candles[idx-1].low)
                if c.low < prev_low and c.close > prev_low:
                    rect_high = c.close
                    rect_low = c.low
                    sl = rect_low * 0.999
                    tp = max(c.high for c in candles[max(0, idx-4):idx])
                    rr = None
                    if rect_high > sl:
                        risk = rect_high - sl
                        reward = (tp - rect_high) if tp > rect_high else risk * 3
                        rr = round(reward / max(risk, 1e-9), 1)

                    candidates.append(RectCandidate(
                        tf="15m",
                        weakness_idx=idx,
                        rect_high=rect_high,
                        rect_low=rect_low,
                        sl=sl,
                        tp=tp,
                        rr=rr,
                        side="BUY",
                        key_level_ref="prior_swing_low",
                        confidence="medium" if rr and rr > 2.5 else "low",
                    ))

    return candidates[:3]


def compute_signature(candles: list[Candle], bias: str, n_ranges: int) -> str:
    """Cheap signature for change detection."""
    if not candles:
        return "empty"
    last5 = [round(c.close, 2) for c in candles[-5:]]
    quantized = [int(x * 100) % 1000 for x in last5]  # rough
    return f"{bias}:{n_ranges}:{'-'.join(map(str, quantized))}"


def analyze(brief: Brief) -> MulhamSignals:
    candles = brief.market.candles or []
    notes: list[str] = []

    if not candles:
        notes.append("No candles in brief.market.candles — Mulham structure analysis limited (indicators only).")
        return MulhamSignals(
            bias="neutral",
            bias_method="no_data",
            high_prob_ranges=[],
            rect_candidates=[],
            cct_last=None,
            material_change=True,
            signature="no_candles",
            notes=notes,
        )

    ranges = detect_high_prob_ranges(candles)
    cct = compute_cct_bias(candles)
    rects = find_rect_candidates(candles, ranges)

    # Bias fusion (very simple deterministic)
    bias = "neutral"
    method = "structure+cct"
    if ranges:
        last_dir = ranges[-1].direction
        bias = "bullish" if last_dir == "up" else "bearish"
    if cct and cct.get("direction") in ("bullish", "bearish"):
        if bias == "neutral":
            bias = cct["direction"]
        elif bias != cct["direction"]:
            method = "conflicting"
            bias = "neutral"  # let LLM decide

    last_sig = compute_signature(candles, bias, len(ranges))
    # material_change is conservative: if we have fresh ranges or strong CCT, assume material
    material = bool(ranges) or (cct is not None and cct.get("type") != "neutral")

    if len(candles) < 10:
        notes.append("Short candle history — some W+S / rect detections may be unreliable.")

    return MulhamSignals(
        bias=bias,
        bias_method=method,
        high_prob_ranges=ranges,
        rect_candidates=rects,
        cct_last=cct,
        material_change=material,
        signature=last_sig,
        notes=notes,
    )


def _build_redis():
    """Cliente Redis do agente (binance-redis) a partir do env REDIS_HOST/REDIS_PORT."""
    import redis

    host = os.environ.get("REDIS_HOST", "redis")
    port = int(os.environ.get("REDIS_PORT", "6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)


def load_brief_from_redis(redis_client: Any, symbol: str) -> Brief | None:
    """Lê o brief espelhado pelo gateway em binance:strategist:brief:<symbol>.

    Retorna None quando a chave está ausente/expirada (handoff redis-first sem brief).
    """
    raw = redis_client.get(f"binance:strategist:brief:{symbol}")
    if raw is None:
        return None
    return Brief.model_validate_json(raw)


def main(argv: list[str] | None = None, *, redis_client: Any = None) -> int:
    """Analyzer redis-first: lê o brief do Redis (espelho do gateway) e grava os sinais
    Mulham no Redis. Sem filesystem — o handoff é 100% Redis (binance-redis).
    """
    parser = argparse.ArgumentParser(description="Deterministic Mulham Trading pre-analyzer for Hermes Briefs.")
    parser.add_argument("--symbol", required=True, help="Symbol cujo brief ler do Redis (ex.: BTCUSDT)")
    args = parser.parse_args(argv)

    r = redis_client if redis_client is not None else _build_redis()

    brief = load_brief_from_redis(r, args.symbol)
    if brief is None:
        print(
            json.dumps({"error": "brief_missing", "symbol": args.symbol}),
            file=sys.stderr,
        )
        return 1

    signals = analyze(brief)

    out = {
        "bias": signals.bias,
        "bias_method": signals.bias_method,
        "high_prob_ranges": [asdict(r_) for r_ in signals.high_prob_ranges],
        "rect_candidates": [asdict(r_) for r_ in signals.rect_candidates],
        "cct_last": signals.cct_last,
        "material_change": signals.material_change,
        "signature": signals.signature,
        "notes": signals.notes,
        "source_brief_ts": brief.timestamp,
        "symbol": brief.market.symbol,
        "timeframe": brief.market.timeframe,
    }

    # Persistência redis-first dos sinais (binance:strategist:mulham:<symbol>) + chaves
    # auxiliares de detecção rápida de mudança. O agente consome tudo via Redis.
    key = f"binance:strategist:mulham:{out['symbol']}"
    r.set(key, json.dumps(out, ensure_ascii=False), ex=3600 * 6)
    r.set(f"{key}:signature", out["signature"], ex=3600 * 6)
    r.set(f"{key}:material_change", str(out["material_change"]).lower(), ex=3600 * 6)

    print(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())