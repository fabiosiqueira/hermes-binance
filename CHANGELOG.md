# Changelog

## [v0.6.3] - 2026-06-12

### Fixes
- d9555f9 fix(binance-project): action de automation filtrada para colunas reais do betrader (#7 rodada 2)

## [v0.6.2] - 2026-06-12

### Fixes
- 56f4791 fix(binance-project): gateway threaded + lock de execute (#6 Part B) e contrato real de automations (#7)

### Other
- 8fcf47e chore(binance-project): STATE — rodada #6 Part A fechada (/done deploy prd v0.6.1)

## [v0.6.1] - 2026-06-12

### Fixes
- 520b8b0 fix(binance-project): brief thin-client usa timeout 90s (não 5s default do httpx)

### Other
- aaf8281 docs(binance-project): relatório de validação de funcionalidades do HAWK + STATE

## [v0.6.0] - 2026-06-12

### Features
- 9dbd1ab feat(binance): brief/proposal/mulham handoff 100% Redis (sem filesystem) + dual-write do brief
- 71975a8 feat(binance): redis-first brief/proposal handoff + deterministic Mulham analyzer (W+S, CCT, token efficiency for Binance access via /hermes-validate)

### Fixes
- 1f0d467 fix(binance-project): brief pede candles-only (indicador on-demand do betrader pendura)
- 5fa0cac fix(binance-project): brief busca candles+indicators via POST /api/market
- 9faaeda fix(binance-project): redis do agente com nome único (evita colisão DNS com coolify-redis)
- a2f5077 fix(binance-project): Brief tolera falha em endpoints betrader descartados (beholder/memory, automations/indexes)
- 07829d5 fix(binance-project): gateway command usa `hermes gateway run` (engine dropou alias `gateway`)

## [v0.5.0] - 2026-06-11

### Features
- 5aa5740 feat(binance-project): capacidade GitHub Issues do HAWK (conversacional)

### Other
- 771e1c7 docs(binance-project): plano fsa-tools da capacidade GitHub Issues do HAWK
- 8ed2b12 docs(binance-project): spec capacidade GitHub Issues do HAWK (conversacional)

## [v0.4.0] - 2026-06-11

### Features
- 8ce1cae feat(binance-project): ativa ingress público do webhook em prod

### Fixes
- fd4d9bf fix(binance-project): heartbeat 4h + deliver telegram + max_turns 25 (corta burn de tokens ~94%)
- 99e8e70 fix(binance-project): healthcheck nos 3 serviços Python (resolve running:unknown)
- 83ac18e fix(binance-project): ingress via labels Traefik explícitas (substitui magic var)
- 2f62634 fix(binance-project): wire webhook path no hermes-coolify.yml (débito #3)

### Other
- 5e7b788 docs(binance-project): alinha SOUL/AGENTS/cron com arquitetura F1/F2
- 2ef24ee chore(binance-project): registra prod-uuid do Coolify no done.md
- 915bf3a chore(binance-project): docker-compose.yml -> .yaml (default do Coolify)
- 73003de chore(binance-project): renomeia hermes-coolify.yml -> docker-compose.yml

## [v0.3.0] - 2026-06-10

### Features
- 94c0f10 feat(binance-project): f2 risk gateway extrai enforcement do agente

## [v0.2.0] - 2026-06-10

### Features
- 1b63698 feat(binance-project): f1 event-driven wake via betrader webhook
- a17db5c feat: M1 do estrategista Hermes-Binance (DRY_RUN) via betrader-hydra

### Other
- cdfb3be docs: spec do estrategista Hermes para Binance via betrader-hydra (M1)
