# STATE — hermes/binance-project

Atualizado: 2026-06-13 ~22:00 UTC (sessão /goal: fix #6 e #7 → /hermes-validate fechou ambos)

## Resultado: #6 e #7 FECHADAS pelo revisor HAWK
Goal cumprido: corrigir #6 e #7, validar runtime e fechar via revisor Hermes (HAWK). Ambas CLOSED.

## #6 Part B — gateway concorrente (binance v0.6.2, commit 56f4791)
`HTTPServer` → `ThreadingHTTPServer` (daemon_threads) + `_EXECUTE_LOCK` na seção crítica do `handle_execute` (load→gate→betrader→persist do financial_state). Briefs/health paralelos; executes mutuamente exclusivos. TDD 168 verde (2 novos de concorrência, Event-based). Probe runtime: `/health` 5–33ms com brief em voo (não serializa). HAWK fechou cross-checando o código real (risk_gateway.py L30/L59/L161/L398-399).

## #7 — sentinela dinâmica liq-proximity (cadeia binance + betrader)
A aceitação "armar liq-proximity sem hardcode" exigiu **9 fixes** em 2 repos, porque o caminho de install NUNCA tinha sido exercido e2e:

**binance-project (v0.6.2→v0.6.4):**
1. schema: regex aceita dot-path no LHS (`.current`); `parse_automation_condition` decompõe em {eval,operator,variable}+symbol+index_key. RHS segue literal (anti-injeção).
2. adapter `install_automations`: envia o contrato REAL do betrader (`conditions:[{eval,operator,variable}]`+`symbol`+`indexes`); filtra colunas de action (`_ACTION_COLUMNS`).
3. `Brief.memory_indexes`: passthrough de `/api/automations/indexes` (antes descartado) — é por aqui que o HAWK descobre o nome exato do índice (userId opaco).
4. AGENTS.md "Como armar sentinelas" reescrito (descoberta via memory_indexes; `.current`; LIQ_PROXIMITY_PCT).

**betrader-hydra (main, ac6247b→ccc94e4) — 5 bugs, cada um escondia o próximo:**
1. `LIQ_PROXIMITY_PCT_<userId>` derivado por tick de MARK_PRICE (LIQ_WATCH gate; flat→unset) + exposto no catálogo (getFuturesLiquidationIndexes).
2. rota `/api/automations/indexes` castava string→Symbol (symbol.base/quote undefined→catálogo "MEMORY['undefined:…']"); agora resolve o Symbol real (404 se ausente).
3. **conditions-leak**: saveAutomation não destructurava `conditions` (relation)→spread cru no prisma.automation.create→500. NENHUMA automation era criável (UI ou API).
4. **getAutomation fora da transação**: read-back do registro recém-criado usava prisma global→lê fora da tx→null→"reading id of null". Agora `(transaction ?? prisma)`.
5. **sync de símbolos quebrado (2 camadas)**: `new Exchange()` spot-only chamava futuresExchangeInfo→getExchangeInfo de undefined; e filtros beautified (Float) vs model String. Sem sync, Symbol table sem BTCUSDT→catálogo vazio.

**Validação e2e host-side (probe no container do gateway):** brief→HAWK descobre `LIQ_PROXIMITY_PCT_<uid>` no memory_indexes→arma sentinela com o índice DESCOBERTO→`executed:true,errors:[]`, persistido correto (conditions {eval,operator,variable}, isActive, WEBHOOK+url)→teardown limpo. Sync restaurado: 1022 símbolos, BTCUSDT presente, 31 índices no catálogo.

## Não-fechado / próximos
- #4 (F3 gradação HOM→PROD) segue aberta — fora do escopo desta sessão.
- betrader#6 (beholder serialization) débito externo GitLab — mitigado (timeout 90s + gateway sem serialização), não resolvido.
- Gotchas detalhados em auto-memory: issue6b-7-automations-contract.md.
