# STATE — validação betrader (issue #5)

Atualizado: 2026-06-12 17:05 UTC (sessão `/hermes-validate #5`)

## Objetivo
Acesso ao betrader 100% testado/corrigido + handoff do ciclo 100% Redis (sem filesystem). Issue #5.

## Resultado

### ✅ Acesso ao betrader (blocker original) — RESOLVIDO
`-2015` (keys Futures testnet) + cadeia de 4 bugs de candle corrigidos no betrader (commits remotos 5fa0cac/1f0d467: POST /api/market candles+indicators). Brief real: equity 7283.51, 200 candles OHLCV, indicators.

### ✅ Handoff 100% Redis (sem filesystem) — IMPLEMENTADO + DEPLOYADO + VALIDADO (commit 9dbd1ab)
Antes: gateway gravava brief só no risk-redis privado → agente (binance-redis) não via → caía no brief.json (filesystem). Decisão errada.
Fix:
- **risk_gateway.handle_brief dual-write**: cópia AUTORITATIVA no risk-redis (o gate relê, agente não forja) + espelho no binance-redis (`BRIEF_MIRROR_REDIS_HOST=binance-redis`, deployado) para o agente ler redis-first.
- **mulham_analyzer**: modo redis (`--symbol`), lê brief do Redis, grava sinais no Redis. Sem arquivo.
- **strategist_cycle**: `brief` dispara gateway+mulham e imprime a CHAVE Redis; `execute` redis-only (`redis:KEY`). Sem brief.json/proposal.json.
- compose prod+local: BRIEF_MIRROR (NÃO `redis`, que colide com coolify-redis).
- SOUL/AGENTS/cron/config: fluxo redis-only, removidas afirmações de filesystem.
- Testes: 154 verde (inclui correção de 8 falhas pré-existentes da sessão anterior — mocks /api/market GET→POST). Verifier independente: PASS.

### Validação runtime (prod, DRY_RUN)
- Deploy Coolify (rebuild baked) + **re-seed manual do volume do agente** (scripts/SOUL/AGENTS — gotcha learned #28: volume sombreia baked).
- `brief` → stdout = `binance:strategist:brief:BTCUSDT`; **brief AGORA presente no binance-redis** (TTL 899, equity 7283, 200 candles) — o agente alcança. mulham presente. **Nenhum arquivo escrito** (workspace vazio após run).
- Ciclo completo redis-only: proposal SET no binance-redis → `execute redis:` → `{"executed":true}`. Gate rodou; decision no risk-redis (gate_ok=true); financial_state persistido.

## Próximo passo
Revisor Hermes (`hermes -z`) confirma canal+Redis (binance:strategist:brief|mulham|proposal no binance-redis) e **fecha** #5. Eu nunca fecho.

## Não-fechado
Issue #5 aberta até sign-off do revisor. betrader#6 (serialização beholder) fora do caminho crítico (mitigado em a2f5077).
Drift menor: docker-compose.yaml prod usa serviço `binance-redis` (commit 9faaeda); local ainda `redis` — intencional (sem coolify-redis local).
