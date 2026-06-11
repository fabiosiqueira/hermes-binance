# Changelog

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
