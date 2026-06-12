# STATE — hermes/binance-project

Atualizado: 2026-06-12 ~22:05 UTC (sessão /goal: fix #6 Part B + #7 → /hermes-validate)

## Objetivo da sessão
Goal ativo: "invoque /issues e corriga as #6 e #7, depois invoque /hermes-validate para fechá-las".

## Feito (código verde, deployando)
- **#6 Part B** (binance `56f4791`, v0.6.2): `ThreadingHTTPServer` + `_EXECUTE_LOCK` (executes mutuamente exclusivos, briefs paralelos). 168 testes verdes (2 novos de concorrência, Event-based sem sleeps).
- **#7** (dois repos):
  - betrader-hydra main `ac6247b`: índice derivado `LIQ_PROXIMITY_PCT_<userId>` por tick de MARK_PRICE (gate LIQ_WATCH; flat → unset). 248 jest verdes. Commit `chore(format)` separado antes da feature (hook prettier reformatou hydra/indexes inteiros).
  - binance `56f4791`: **adapter consertado** — `install_automations` agora envia `{eval,operator,variable}+symbol+indexes` (formato antigo `{condition:str}` NUNCA foi instalável; Prisma exige os campos e o brain dispara via indexes). Regex aceita dot-path no LHS (`.current`); RHS segue literal. `parse_automation_condition` em schemas.py. AGENTS.md seção sentinelas reescrita (POSITION_LIQ_PRICE não era chave MEMORY real).
- #6 estava CLOSED no GH (fechada 21:08 antes do tracking comment) mas Part B não implementada → **reaberta** para o fluxo needs-review→revisor.
- Tracking: handoff comments + `needs-review` em #6 e #7 (criei o label). Versão v0.6.2 (CHANGELOG; pyproject segue stale por precedente). Memory: `issue6b-7-automations-contract.md`.

## Em andamento (retomar AQUI se a sessão cair)
1. **Deploys prd em voo:** betrader `vrjzb5txo5a9j1tb7po77itz` (commit ac6247b) + binance `sm7fi94roaa5ro6626r0kwdy` (main 607b134). Gate: `running:healthy` em ambos.
2. **Pós-deploy binance OBRIGATÓRIO: re-seed do volume `hermes-data`** (scripts/ + AGENTS.md do agente são sombreados pelo volume; risk-gateway roda baked). Ver [betrader-validation-issue5]: `docker run -u 0 -v VOL:/seed --entrypoint sh IMG -c 'cp -a ...'` preservando memories/sessions/state. Depois restart do gateway.
3. **`/hermes-validate`** para validar runtime e FECHAR #6 e #7 (revisor fecha; usar `/issues close` com veredito+evidência). Retest mínimo: brief concorrente (2 calls paralelas não serializam), execute com automation-only proposal → automation instalada de verdade no betrader (id retornado + visível em /api/automations), LIQ_PROXIMITY_PCT no catálogo exige posição aberta (sem posição: índice ausente = comportamento correto).

## Gotchas novos desta rodada
Ver memory `issue6b-7-automations-contract.md` (contrato Prisma das automations, validateConditions async-sem-await, {current,previous}, prettier/format no betrader, tsc main ~85 erros pré-existentes → gate é jest).

## Não-fechado
- #6/#7 precisam do veredito do revisor pós /hermes-validate.
- betrader#6 (beholder serialization) segue aberto no GitLab — componente de latência mitigado, não resolvido.
- #4/F3 (gradação HOM→PROD) intocado nesta sessão.
