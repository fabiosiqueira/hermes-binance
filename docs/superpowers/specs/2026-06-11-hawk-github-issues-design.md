# Design — Capacidade de GitHub Issues para o HAWK (conversacional)

**Data:** 2026-06-11
**Status:** Aprovado (design)
**Repo alvo das issues:** `fabiosiqueira/hermes-binance`

## Contexto

O `defi-project` dá ao agente OWL a capacidade de abrir/gerir issues no próprio
repo (via `gh` baked na imagem + auth por arquivo no boot + skill dedicada). O
`binance-project` (HAWK) nunca recebeu essa capacidade: o spec M1 centrou o loop
em Brief→StrategyProposal→Gate→Execute via Redis + Risk Gateway, e `gh`/issues
nunca foi requisito. Resultado: débito de estratégia, conflitos com dogma e
padrões de proposta rejeitada vivem só em memória/Redis e se perdem.

Este design porta o padrão do defi para o HAWK, em **escopo conversacional**
(HAWK abre issue sob demanda numa conversa — sem automação no ciclo).

## Decisões travadas (brainstorming 2026-06-11)

| Decisão | Escolha | Rationale |
|---|---|---|
| Gatilho | Só conversacional | Sem integração no ciclo/cron; menor superfície. |
| Auth no boot | Script Python baked + wrapper no `command` | O `gateway` roda `command: ["gateway","run"]`, que bypassa o s6 — o cont-init do defi não dispararia. |
| Linguagem do auth | Python (`scripts/gh_auth.py`) | `scripts/` do binance é Python+pytest. Copiar o `.sh` do defi violaria match-de-estilo local. |
| Capacidade | Issues: create/list/view/comment + close/relabel próprias | Sem PRs (HAWK não edita código deste repo — YAGNI). Token scope `repo`. |
| Documentação de uso | Portar skill `github/github-issues` do defi | Alinhado ao OWL; instrui o agente sem inflar AGENTS.md. |

## Componentes

### 1. `Dockerfile` (stage `local`) — INFRA
- Instalar binário `gh` pinado (`ARG GH_VERSION=2.93.0`, mesma versão do defi),
  via tarball do release oficial, `install -m 0755 ... /usr/local/bin/gh`.
- `ENV GH_CONFIG_DIR=/opt/hermes/.gh`.
- `mkdir -p /opt/hermes/.gh && chown hermes:hermes ... && chmod 700 ...`.
- **Fora** do volume `/opt/data`: token não vaza no working tree do agente nem
  persiste no volume nomeado `hermes-data`.

### 2. `hermes/scripts/gh_auth.py` (baked) — código de produção
- Escreve `$GH_CONFIG_DIR/hosts.yml` a partir de `GH_TOKEN` (override local) ou
  `GITHUB_TOKEN` (Coolify).
- `chmod 600` no `hosts.yml`. Idempotente.
- **Token ausente → no-op, exit 0** (degrada para "not logged in"; nunca derruba
  o gateway num crash-loop).
- Auth por arquivo (não `gh auth login --with-token`): o token vira string vazia
  no subprocesso do agente (masking), e `--with-token` exige escopo `read:org`
  que o token não tem.
- Conteúdo do `hosts.yml`:
  ```yaml
  github.com:
      oauth_token: <TOKEN>
      user: <GH_AUTH_USER ou fabiosiqueira>
      git_protocol: https
  ```
- Testes (pytest, match do estilo de `scripts/`): token presente → `hosts.yml`
  escrito com perm 600 e conteúdo correto; token ausente → exit 0 sem arquivo e
  sem exceção. Mock só na fronteira de filesystem/env.

### 3. `docker-compose.yaml` + `hermes-compose.local.yml` — INFRA
- `gateway.command` → `["sh","-c","python scripts/gh_auth.py; exec gateway run"]`.
- Adicionar `GITHUB_TOKEN: ${GITHUB_TOKEN}` no env do `gateway` (prod, vem do
  Coolify).
- `hermes-compose.local.yml`: `GH_TOKEN: ${GH_TOKEN:-}` opcional no gateway (dev).
- `.env.example`: adicionar `GITHUB_TOKEN=` (sem valor).

### 4. `hermes/skills/github/github-issues/` — skill portada
- Copiar a skill genérica do defi (`hermes/skills/github/github-issues/`),
  ajustando o repo alvo para `fabiosiqueira/hermes-binance`.
- Superfície: create/list/view/comment + close/relabel. Sem PRs.

### 5. `hermes/AGENTS.md` — seção curta
- Repo alvo: `fabiosiqueira/hermes-binance`.
- Gotcha: rodar `gh label list --repo <repo>` **antes** de `gh issue create
  --label` (gh ignora silenciosamente labels inexistentes).
- O que é apropriado emitir: débito de estratégia, conflito com dogma, padrão de
  proposta rejeitada recorrente.
- Limite: HAWK gere backlog, não edita código deste repo.

## Gotchas travados
- `gh issue create --label X` ignora labels inexistentes em silêncio → `gh label
  list` antes.
- `GH_CONFIG_DIR` fora do volume `/opt/data`.
- Auth por arquivo obrigatória (masking do token no subprocesso do agente).

## Out of scope (YAGNI)
- PRs, automação no ciclo do estrategista, overlay `local-s6` completo, issues em
  repos de terceiros.

## Pré-requisito operacional (não-código)
- Criar/obter `GITHUB_TOKEN` com scope `repo` e setar na env tab do Coolify antes
  do deploy. Sem o token, a capacidade degrada para "not logged in" (não quebra).

## Passo terminal (pós-implementação)
- Após estas alterações, **parar o app no Coolify (prod)** — stop do gateway e
  serviços, estado/volume preservados — para cortar consumo de quota de LLM.
  Reversível (start depois).
