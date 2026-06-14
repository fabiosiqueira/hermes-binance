# Diagnose `/brief` 502 — worked example (2026-06-11 10:45Z cycle)

Recorded when `POST http://risk-gateway:8647/brief` started returning 502 with body `{"ok":false,"error":"brief failed"}` and stayed that way for 13+ hours. Use this as a reference when the same symptom recurs.

## Symptoms

- `python scripts/strategist_cycle.py brief` exits 1 with `{"executed":false,"reason":"gateway_error","detail":"Server error '502 Bad Gateway' for url 'http://risk-gateway:8647/brief'..."}`
- 3 consecutive retries give the same error
- The failure has been ongoing across many cron runs (correlate via `workspace/cycle_log.json` history)

## Probes (in order, ~30s total)

All probes use `Authorization: Bearer <token from GATEWAY_TOKEN env>` — same auth the production client uses. See the SKILL.md "Upstream broken — hold discipline" section for the full step-by-step; the gist is:

### 1. Is the HTTP layer alive?

```bash
python3 -c "import httpx,os; r=httpx.get('http://risk-gateway:8647/health', timeout=5); print(r.status_code, r.text[:80])"
# Expected when layer is up:  200 {"status": "ok"}
```

If this returns 502 too → the gateway service itself is down (operator problem, but you can still see if `/health` is up via TCP socket open check). If this is 200, the service is alive.

### 2. Is `/brief` actually broken, or is it just a wrong path / bad auth?

POST the brief request directly with the auth header. Body should be `{"symbol":"BTCUSDT","timeframe":"15m","mode":"DRY_RUN"}` (substitute your env values).

- When broken upstream: `502 {"ok": false, "error": "brief failed"}`
- When auth/path wrong: `401 / 404 / 403` with different body

### 3. Is the gate still functional? (decisive disambiguation)

This is the cheapest way to know if it's worth trying to recover by submitting an empty proposal, or if the entire execution path is also dead.

`GET` the existing `binance:strategist:proposal:<SYMBOL>` (which is already `entries: []` from the previous cycle) and `POST` it to `/execute` via:

```bash
.venv/bin/python scripts/strategist_cycle.py execute redis:binance:strategist:proposal:<SYMBOL>
```

- When gate is fine but brief cache is empty: `200 {"executed": false, "reason": "brief_missing"}`
- When gate is broken too: other 4xx/5xx

If you get `brief_missing`, **submitting a real execute will fail the same way** — there is no point submitting; the brief is upstream of the gate. Document and hold.

### 4. Is there any cached brief in Redis at all?

```python
import os, redis
# Use the AGENT'S Redis (REDIS_HOST / REDIS_PORT env), not 'risk-redis'.
# 'risk-redis' is the gateway's private Redis; out of bounds by design.
r = redis.Redis(
    host=os.environ.get("REDIS_HOST", "127.0.0.1"),
    port=int(os.environ.get("REDIS_PORT", "6379")),
    decode_responses=True,
)
print(r.keys('binance:strategist:brief:*'))
# Expected when fully broken:  []
```

If the list is non-empty, a prior cycle left a brief cache and you can read it directly for analysis — but you still can't `execute` against it (it would race with the next build and the upstream is broken anyway). Useful for forensic reading.

## Conclusion template for `cycle_log.json`

```json
{
  "cycle_id": "candle_15m_close",
  "ts_utc": "<candle close ISO>",
  "ts_verified_utc": "<now ISO>",
  "step_brief": {
    "status": "failed",
    "reason": "risk-gateway 502 on /brief (upstream infra, persistent)",
    "retried": true,
    "retries": 3,
    "health_check": "<probes 1,2,3,4 summarized bit-exact>",
    "first_seen_utc": "<earliest cycle where this appeared, from history>",
    "duration_hours_so_far": <float>
  },
  "step_proposal": {
    "status": "unchanged_from_previous_cycle",
    "redis_key": "binance:strategist:proposal:<SYMBOL>",
    "sha256": "<sha256 of the proposal bytes in Redis — same as last cycle if untouched>",
    "comment": "Re-writing identical JSON would be a resubmit; discipline preserved. Redis key NOT touched this run."
  },
  "step_execute": {
    "status": "skipped",
    "comment": "/brief still 502; gate confirmed functional via probe (brief_missing). Re-submitting identical would not change state. /execute is 200 and works, but nothing to execute."
  },
  "discipline": "no-resubmit; no execution; brief failure is upstream infra (risk-gateway handle_brief), not a strategy error; no edge detected therefore no entries; proposta vazia anterior permanece registrada e intocada",
  "root_cause_class": "infra",
  "first_seen_utc": "<same as step_brief.first_seen_utc>",
  "duration_hours": <float>,
  "next_action": "Operator action required: <concrete next thing>."
}
```

## Operator-action handoff (what to write in `next_action`)

The agent must not edit infra, but should leave the operator a concrete next step:

- "Inspect logs of risk-gateway container in `handle_brief` (ensure_monitor / fetch_brief calls) and confirm reachability of betrader-hydra + Redis from inside the risk-gateway container."
- "If betrader-hydra itself is the upstream that died, restart it per the operator runbook. No agent action available until /brief returns non-502."

## Don't

- Don't write a fabricated entry with an arbitrary `stop_loss` to "make progress" — the schema lets you, but you'd be trading blind.
- Don't re-submit the same `proposal.json` (or the same `redis:binance:strategist:proposal:<SYMBOL>` key with byte-identical content) to `/execute` "in case it works this time" — gate gives bit-exact `brief_missing` until the upstream comes back. (Re-executing the same Redis key after the upstream recovers IS legitimate, because the cycle is the same payload — not an inference change.)
- Don't edit `risk_gateway.py` / `risk_engine.py` / `dogmas.yaml` / compose — operator territory.
- Don't grow the channel report each cycle. The previous cycle's reasoning + the cycle_log history carry the narrative; the new report should be 5-10 lines.
