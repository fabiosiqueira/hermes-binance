# Plan — Capacidade GitHub Issues do HAWK (conversacional)

## Metadata

- **Generated:** 2026-06-11
- **Worktree:** required

## Context

Projeto `binance-project` (overlay/customization do Hermes Agent "HAWK", estrategista
Binance Futures). Raiz git: `~/dev/projetos/hermes/binance-project`. Infra em Docker
Compose (stages `local`→`vps` no `Dockerfile`); scripts do estrategista em Python 3.11
(`hermes/scripts/`), testados com pytest + respx + fakeredis (`tests/`,
`pythonpath = ["hermes/scripts"]`). Persona/contexto do agente em `hermes/SOUL.md` /
`hermes/AGENTS.md`. Skills do agente vivem em `hermes/skills/` (dir ainda inexistente).
Spec de design aprovado: `docs/superpowers/specs/2026-06-11-hawk-github-issues-design.md`.

## Baseline (current state)

```bash
# Nenhuma capacidade gh/issues existe ainda. Tudo abaixo retorna vazio/erro:
grep -c 'gh_auth\|GH_VERSION' Dockerfile          # → 0
ls hermes/scripts/gh_auth.py                       # → No such file
ls hermes/skills/                                  # → No such file
grep -c 'GITHUB_TOKEN' .env.example                # → 0
python -m pytest tests/unit/test_gh_auth.py -q     # → file not found
```

## Objective

Portar o padrão de GitHub Issues do `defi-project` para o HAWK em escopo
**conversacional**: instalar `gh` na imagem, escrever a auth no boot a partir de
`GITHUB_TOKEN`/`GH_TOKEN`, wirear o `gateway` para rodar a auth antes do `gateway run`,
portar a skill `github-issues` retargetada para `fabiosiqueira/hermes-binance`, e
documentar a capacidade no `AGENTS.md`. Sem PRs, sem automação no ciclo.

## Definition of Done (global)

Single verifiable command:

```bash
python -m pytest tests/unit/test_gh_auth.py -q \
  && grep -q 'GH_VERSION=2.93.0' Dockerfile \
  && grep -q 'GH_CONFIG_DIR=/opt/hermes/.gh' Dockerfile \
  && grep -q 'gh_auth.py' docker-compose.yaml \
  && grep -q 'gh_auth.py' hermes-compose.local.yml \
  && grep -q '^GITHUB_TOKEN=' .env.example \
  && test -f hermes/skills/github/github-issues/SKILL.md \
  && grep -rq 'hermes-binance' hermes/skills/github/github-issues \
  && ! grep -rq 'defi-agent' hermes/skills/github/github-issues \
  && grep -q 'hermes-binance' hermes/AGENTS.md \
  && grep -q 'gh label list' hermes/AGENTS.md
```

**Expected output:** pytest termina com `passed` (sem `failed`/`error`) e o comando
composto retorna exit 0 (todos os greps casam, nenhum `defi-agent` na skill).

## Policy (invariant)

- **Mudança cirúrgica e mínima.** Toque apenas os arquivos declarados em cada task.
  Zero refactor, renomeação ou reorganização fora do escopo da task.
- **Match existing style exactly.** Antes de editar, espelhe naming/estrutura/idioma
  do código vizinho (scripts Python: docstring `#` no topo, funções `_`-prefixadas,
  `os.environ.get`, DI na fronteira de I/O; testes: docstring pt-BR, `monkeypatch`/`tmp_path`).
- **Segredos nunca em arquivo versionado.** `.env.example` só mostra a variável sem valor.
  `GH_TOKEN`/`GITHUB_TOKEN` vêm do env em runtime.
- **`GH_CONFIG_DIR` fica FORA do volume `/opt/data`** (em `/opt/hermes/.gh`): o token não
  pode vazar no working tree do agente nem persistir no volume nomeado.
- **`betrader-hydra` e `dogmas.yaml`/`risk_engine.py` são intocáveis.** Nenhuma task aqui
  encosta neles.
- **Não modificar infra fora do declarado.** As edições no `Dockerfile` e composes estão
  pré-aprovadas pelo spec (seção "Componentes"); qualquer outra mudança de infra → parar.

## Dependency justification

Nenhuma dependência declarada. As 5 tasks tocam conjuntos de arquivos disjuntos
(`Dockerfile` / `gh_auth.py`+test / 2 composes+`.env.example` / `skills/**` / `AGENTS.md`)
e não consomem artefato uma da outra em tempo de edição. A Task 1.3 referencia
`scripts/gh_auth.py` por **string de path** no `command` do compose, não por handoff de
artefato — o YAML é editável e verificável (grep/yaml-lint) independentemente de a Task 1.2
ter rodado. Logo todas são Phase 0 paralelas.

## Clusters

### Cluster 1 — Capacidade GitHub Issues do HAWK

**Inter-cluster dependency:** none

#### Task 1.1: Instalar gh CLI no Dockerfile (stage local) [sonnet]

**Files:**
- Modify: `Dockerfile`

**Diagnosis:** O `Dockerfile` tem stages `local` (engine + deps) e `vps` (`FROM local`,
data dir baked). O `gh` precisa entrar no stage `local` para que `vps` herde. A referência
1:1 está no `defi-project/Dockerfile` (linhas 24-35): tarball do release oficial pinado em
`GH_VERSION=2.93.0`, `install -m 0755`, `ENV GH_CONFIG_DIR=/opt/hermes/.gh`, e
`mkdir -p /opt/hermes/.gh && chown hermes:hermes ... && chmod 700`. Diferença: o binance NÃO
faz bootstrap de repo git no boot, então só o binário + dir de config; nada de clone.

**Verification:** `grep -q 'GH_VERSION=2.93.0' Dockerfile && grep -q 'ENV GH_CONFIG_DIR=/opt/hermes/.gh' Dockerfile && grep -q 'chmod 700 /opt/hermes/.gh' Dockerfile`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (cwd: /Users/fabiosiqueira/dev/projetos/hermes/binance-project).
Tarefa: instalar o GitHub CLI (`gh`) no `Dockerfile`, no stage `local`.

CONTEXTO
- O `Dockerfile` tem dois stages: `local` (FROM ghcr.io/.../hermes-engine, instala deps)
  e `vps` (FROM local, COPY hermes/ → /opt/data, chown, VOLUME). vps herda tudo de local.
- O `gh` DEVE entrar no stage `local`, depois do bloco que instala `redis-tools` (apt-get)
  e ANTES da linha `# ---------- Stage vps: data dir baked ...` / `FROM local AS vps`.
- Referência 1:1 (copie o padrão exato, ajustando só os comentários para o contexto binance):
  o arquivo /Users/fabiosiqueira/dev/projetos/hermes/defi-project/Dockerfile contém, entre as
  linhas ~24-35, o bloco de instalação do gh. Leia-o e replique:
    ARG GH_VERSION=2.93.0
    RUN curl -fsSL --retry 3 -o /tmp/gh.tar.gz \
            "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
        && tar -C /tmp -xzf /tmp/gh.tar.gz \
        && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
        && rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64"
    ENV GH_CONFIG_DIR=/opt/hermes/.gh
    RUN mkdir -p /opt/hermes/.gh && chown hermes:hermes /opt/hermes/.gh && chmod 700 /opt/hermes/.gh
- Adicione um comentário curto (estilo dos comentários existentes no Dockerfile binance, em
  pt-BR) explicando: gh para o HAWK abrir issues; auth (hosts.yml) escrita no boot por
  scripts/gh_auth.py a partir de GITHUB_TOKEN/GH_TOKEN; GH_CONFIG_DIR fora do volume /opt/data
  p/ não vazar token no working tree e ser imune ao masking do env do agente.
- O `curl` já está disponível na imagem (o engine o traz). NÃO adicione apt-get para curl.

CONSTRAINTS
- NÃO toque em nenhum outro arquivo. NÃO altere os stages existentes, o COPY, o chown do vps,
  nem as deps Python/redis-tools já presentes.
- NÃO instale gh via apt nem via `gh extension`. Só o tarball pinado, como na referência.
- Mudança mínima: só o bloco novo + seu comentário.

OUTPUT
- Reporte: o trecho inserido e em que ponto do arquivo (entre quais linhas/blocos).

VERIFICAÇÃO
- Rode: grep -q 'GH_VERSION=2.93.0' Dockerfile && grep -q 'ENV GH_CONFIG_DIR=/opt/hermes/.gh' Dockerfile && grep -q 'chmod 700 /opt/hermes/.gh' Dockerfile
- Retorne quando esse comando sair com exit 0.
```

#### Task 1.2: gh_auth.py + testes pytest [sonnet] +reviewer

**Files:**
- Create: `hermes/scripts/gh_auth.py`
- Create: `tests/unit/test_gh_auth.py`

**Diagnosis:** Script baked que, no boot do `gateway`, materializa
`$GH_CONFIG_DIR/hosts.yml` a partir de `GH_TOKEN` (override local) ou `GITHUB_TOKEN`
(Coolify). Auth por arquivo (não `gh auth login --with-token`) porque o token vira string
vazia no subprocesso do agente (masking) e `--with-token` exige escopo `read:org` que o
token não tem. Token ausente → no-op exit 0 (degrada para "not logged in", nunca derruba o
gateway em crash-loop). Estilo segue `hermes/scripts/strategist_cycle.py`: docstring `#` no
topo, funções `_`-prefixadas, `os.environ.get`, `main()` com fronteira de I/O injetável; teste
segue `tests/unit/test_schemas.py`/`test_webhook_shim.py` (docstring pt-BR, `monkeypatch`,
`tmp_path`). É o único código de produção do plano e toca manuseio de token → +reviewer.

**Verification:** `python -m pytest tests/unit/test_gh_auth.py -q`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (cwd: /Users/fabiosiqueira/dev/projetos/hermes/binance-project).
Tarefa: criar `hermes/scripts/gh_auth.py` (código de produção) + `tests/unit/test_gh_auth.py`.

OBJETIVO DO SCRIPT
Escrever a auth do gh CLI por ARQUIVO no boot do container `gateway`, idempotente e
fail-safe. Comportamento exato:
- Lê o token de `GH_TOKEN` (override local) OU, se ausente, `GITHUB_TOKEN` (prod/Coolify).
- Diretório de config: `os.environ.get("GH_CONFIG_DIR", "/opt/hermes/.gh")`.
- Usuário: `os.environ.get("GH_AUTH_USER", "fabiosiqueira")`.
- Se NÃO houver token (nem GH_TOKEN nem GITHUB_TOKEN, após strip): NO-OP, retorna exit 0,
  SEM criar arquivo e SEM exceção. (Degrada para "not logged in"; nunca derruba o gateway.)
- Se houver token: garante que GH_CONFIG_DIR existe (mkdir parents, ok se já existe), escreve
  `<GH_CONFIG_DIR>/hosts.yml` com EXATAMENTE este conteúdo (chave 4-espaços, ordem das chaves):
      github.com:
          oauth_token: <TOKEN>
          user: <GH_AUTH_USER ou fabiosiqueira>
          git_protocol: https
  Depois aplica chmod 600 no hosts.yml. Idempotente (reescrever é ok). Retorna exit 0.
- NÃO usar `gh auth login --with-token` (token vira string vazia no subprocesso do agente por
  masking; --with-token exige read:org que o token não tem). Auth por arquivo é obrigatória.
- NÃO logar o valor do token em hipótese alguma. Pode imprimir uma linha tipo
  "[gh_auth] hosts.yml escrito (GH_CONFIG_DIR=...)" sem o token.

ESTILO (LEIA ANTES DE ESCREVER)
- Leia `hermes/scripts/strategist_cycle.py` e copie o estilo: bloco de comentário `#` no topo
  explicando o quê/porquê (em pt-BR), imports stdlib, funções helper `_`-prefixadas, tipos,
  `def main(argv=None, *, <fronteira injetável>) -> int:` retornando exit code, guard
  `if __name__ == "__main__": sys.exit(main())`.
- A fronteira de I/O aqui é o filesystem + env. Para testabilidade, parametrize o diretório de
  config e o ambiente de forma que o teste possa usar `tmp_path`/`monkeypatch` SEM mockar
  funções internas. Padrão sugerido: `main()` lê env via os.environ e chama um helper puro tipo
  `_write_hosts(config_dir: Path, token: str, user: str) -> Path`. Mock só na fronteira FS/env.
- Escreva o hosts.yml manualmente (string formatada), NÃO use a lib yaml (match com o defi, que
  escreve via printf; e evita reordenar chaves).

TESTES (tests/unit/test_gh_auth.py)
- Leia `tests/unit/test_schemas.py` e `tests/unit/test_webhook_shim.py` p/ o estilo (docstring
  pt-BR no topo, nomes `test_<comportamento>`, `monkeypatch.setenv`, `tmp_path`).
- Casos obrigatórios:
  1. token presente via GH_TOKEN → hosts.yml escrito em GH_CONFIG_DIR com conteúdo correto
     (contém `oauth_token: <token>`, `user: fabiosiqueira`, `git_protocol: https`,
     `github.com:`) e permissão 600 (`oct(path.stat().st_mode & 0o777) == '0o600'`); exit 0.
  2. token só via GITHUB_TOKEN (GH_TOKEN ausente) → mesmo resultado; GH_TOKEN tem precedência
     quando ambos presentes (teste extra: GH_TOKEN vence GITHUB_TOKEN).
  3. GH_AUTH_USER customizado → aparece no user do hosts.yml.
  4. token ausente (nenhuma das duas vars) → exit 0, hosts.yml NÃO existe, sem exceção.
  5. idempotência → rodar duas vezes deixa o arquivo válido (sem erro).
- pytest config já resolve imports: `pythonpath = ["hermes/scripts"]` no pyproject.toml, então
  `from gh_auth import main, _write_hosts` (ou os nomes que você definir) funciona.

CONSTRAINTS
- Crie SOMENTE esses dois arquivos. Não toque em Dockerfile, composes, AGENTS.md, outros scripts.
- Não adicione dependências novas ao pyproject (use só stdlib: os, sys, pathlib, etc.).
- Sem `console.log`/prints de debug residuais; sem TODO sem ticket; sem catch silencioso.

OUTPUT
- Reporte: API pública do módulo (funções e assinaturas), decisões não-óbvias, e o resultado
  do pytest (contagem passed).

VERIFICAÇÃO
- Rode: python -m pytest tests/unit/test_gh_auth.py -q
- Retorne quando sair com exit 0 (todos passed, zero failed/error).
```

#### Task 1.3: Wiring nos composes + .env.example [sonnet]

**Files:**
- Modify: `docker-compose.yaml`
- Modify: `hermes-compose.local.yml`
- Modify: `.env.example`

**Diagnosis:** O serviço `gateway` roda `command: ["gateway","run"]`, que bypassa o s6 do
defi — então o cont-init que escreve a auth não dispara aqui. Solução: wrappear o command para
rodar `gh_auth.py` antes do `gateway run`. **Gotcha:** o serviço `gateway` (em ambos os
composes) NÃO tem `working_dir: /opt/data` (só o `risk-gateway` tem) — um `python
scripts/gh_auth.py` relativo resolveria errado. Usar **path absoluto** `/opt/data/scripts/gh_auth.py`
evita mexer no `working_dir` do `gateway` (mudar o cwd do processo `gateway run` é risco
desnecessário). Env: prod recebe `GITHUB_TOKEN: ${GITHUB_TOKEN}` no bloco `environment` do
gateway; local recebe `GH_TOKEN: ${GH_TOKEN:-}` (opcional, default vazio — evita o clobber de
`${VAR}` não-definida). `.env.example` ganha `GITHUB_TOKEN=` sem valor.

**Verification:** `grep -q 'gh_auth.py' docker-compose.yaml && grep -q 'gh_auth.py' hermes-compose.local.yml && grep -q 'GITHUB_TOKEN: ${GITHUB_TOKEN}' docker-compose.yaml && grep -q 'GH_TOKEN: ${GH_TOKEN:-}' hermes-compose.local.yml && grep -q '^GITHUB_TOKEN=' .env.example`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (cwd: /Users/fabiosiqueira/dev/projetos/hermes/binance-project).
Tarefa: wirear a auth do gh no serviço `gateway` de DOIS composes + adicionar a var no .env.example.

CONTEXTO
- `docker-compose.yaml` (PROD/Coolify): o serviço `gateway` tem `command: ["gateway", "run"]`
  e um bloco `environment:` com secrets (MINIMAX_API_KEY, OPENROUTER_API_KEY, TELEGRAM_*,
  EXECUTION_MODE, GATEWAY_URL, etc.). NÃO tem `working_dir`. (O serviço `risk-gateway` no mesmo
  arquivo tem working_dir: /opt/data — NÃO copie isso pro gateway.)
- `hermes-compose.local.yml` (DEV): o serviço `gateway` tem `command: ["gateway", "run"]`,
  um bloco `environment:` (HERMES_DATA_DIR, REDIS_*, WEBHOOK_*, GATEWAY_URL) e
  `env_file: hermes/.env.agent`. Também sem working_dir.
- `.env.example`: template de variáveis (sem valores reais).

MUDANÇAS (exatas)
1. Em AMBOS os composes, troque o command do serviço `gateway` de:
       command: ["gateway", "run"]
   para:
       command: ["sh", "-c", "python /opt/data/scripts/gh_auth.py; exec gateway run"]
   GOTCHA CRÍTICO: use o path ABSOLUTO `/opt/data/scripts/gh_auth.py`. O serviço `gateway` não
   tem working_dir, então um path relativo `scripts/gh_auth.py` falharia. NÃO adicione
   `working_dir` ao gateway (mudaria o cwd do `gateway run`). O `;` (não `&&`) garante que,
   mesmo se a auth falhar, o gateway sobe (gh_auth.py já é fail-safe e sai 0, mas o `;` é defesa
   extra). `exec` substitui o shell pelo processo do gateway (PID 1 correto).
2. Em `docker-compose.yaml` (PROD): adicione ao bloco `environment:` do `gateway` a linha:
       GITHUB_TOKEN: ${GITHUB_TOKEN}
   (junto das outras vars de secret; mantenha o alinhamento/indentação do bloco). Atualize
   também o comentário-cabeçalho do arquivo que lista as env vars do gateway (procure o bloco de
   comentário "#   gateway: MINIMAX_API_KEY, ...") adicionando GITHUB_TOKEN à lista.
3. Em `hermes-compose.local.yml` (DEV): adicione ao bloco `environment:` do `gateway` a linha:
       GH_TOKEN: ${GH_TOKEN:-}
   O `:-` (default vazio) é obrigatório: sem ele, `docker compose` reclama de var não-definida
   no host de dev. (Local usa GH_TOKEN como override; prod usa GITHUB_TOKEN.)
4. Em `.env.example`: adicione uma seção curta (estilo das seções existentes, com comentário
   pt-BR), no fim do arquivo:
       # --- GitHub (HAWK abre issues no repo fabiosiqueira/hermes-binance) ---
       # Token com scope `repo`. Prod: GITHUB_TOKEN (Coolify). Local: GH_TOKEN (override).
       GITHUB_TOKEN=

CONSTRAINTS
- Toque SOMENTE esses 3 arquivos. Não altere o serviço `risk-gateway`, `webhook-shim`,
  `redis`, `dashboard`, redes, volumes, healthchecks, ports ou labels Traefik.
- Não adicione working_dir a serviço nenhum. Não toque no command do risk-gateway.
- Mantenha indentação YAML exata dos blocos vizinhos.

OUTPUT
- Reporte: o diff conceitual de cada arquivo (command novo, vars adicionadas).

VERIFICAÇÃO
- Rode: grep -q 'gh_auth.py' docker-compose.yaml && grep -q 'gh_auth.py' hermes-compose.local.yml && grep -q 'GITHUB_TOKEN: ${GITHUB_TOKEN}' docker-compose.yaml && grep -q 'GH_TOKEN: ${GH_TOKEN:-}' hermes-compose.local.yml && grep -q '^GITHUB_TOKEN=' .env.example
- (Opcional, se docker disponível) `docker compose -f docker-compose.yaml config -q` e
  `GH_TOKEN= docker compose -f hermes-compose.local.yml config -q` não devem dar erro de parse.
- Retorne quando o grep composto sair com exit 0.
```

#### Task 1.4: Portar skill github-issues retargetada [sonnet]

**Files:**
- Create: `hermes/skills/github/github-issues/SKILL.md`
- Create: `hermes/skills/github/github-issues/templates/bug-report.md`
- Create: `hermes/skills/github/github-issues/templates/feature-request.md`

**Diagnosis:** Portar a skill genérica do defi (`defi-project/hermes/skills/github/github-issues/`),
SÓ `SKILL.md` + `templates/` (decisão do operador: dropar `references/`, que é defi-specific).
**Diferença crítica do defi:** o `/opt/data` do HAWK NÃO é um checkout git com remote GitHub
(o defi faz bootstrap via git; o binance não). Logo o bloco `Setup` original deriva
`OWNER/REPO` de `git remote get-url origin` → **falha no container do HAWK**. A skill precisa
fixar o repo alvo em `fabiosiqueira/hermes-binance` (sem derivar de remote), e o fallback de
token deve ler `GITHUB_TOKEN`/`GH_TOKEN` do env (não `~/.hermes/.env`). Restante (gh-first,
curl fallback, create/list/view/comment/close/relabel) porta quase 1:1; SEM seção de PRs/branch.

**Verification:** `test -f hermes/skills/github/github-issues/SKILL.md && test -f hermes/skills/github/github-issues/templates/bug-report.md && test -f hermes/skills/github/github-issues/templates/feature-request.md && grep -rq 'fabiosiqueira/hermes-binance' hermes/skills/github/github-issues && ! grep -rq 'defi-agent' hermes/skills/github/github-issues && ! grep -rq 'git remote get-url' hermes/skills/github/github-issues`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (cwd: /Users/fabiosiqueira/dev/projetos/hermes/binance-project).
Tarefa: portar a skill `github-issues` do defi-project para o HAWK, retargetada e enxuta.

FONTE (LEIA)
- /Users/fabiosiqueira/dev/projetos/hermes/defi-project/hermes/skills/github/github-issues/SKILL.md
- /Users/fabiosiqueira/dev/projetos/hermes/defi-project/hermes/skills/github/github-issues/templates/bug-report.md
- /Users/fabiosiqueira/dev/projetos/hermes/defi-project/hermes/skills/github/github-issues/templates/feature-request.md

DESTINO (CRIE)
- hermes/skills/github/github-issues/SKILL.md
- hermes/skills/github/github-issues/templates/bug-report.md
- hermes/skills/github/github-issues/templates/feature-request.md
NÃO porte o diretório `references/` (defi-specific — fora de escopo por decisão do operador).

ADAPTAÇÕES OBRIGATÓRIAS no SKILL.md (o resto porta ~1:1)
1. REPO ALVO FIXO: o `/opt/data` do HAWK NÃO é um checkout git com remote GitHub, então o
   bloco `Setup` original que faz `git remote get-url origin` + sed p/ derivar OWNER/REPO
   FALHA aqui. Substitua-o por valores FIXOS:
       OWNER=fabiosiqueira
       REPO=hermes-binance
   e nos comandos `gh`, use SEMPRE `--repo fabiosiqueira/hermes-binance` (ex.:
   `gh issue list --repo fabiosiqueira/hermes-binance`, `gh issue create --repo ... `,
   `gh issue view N --repo ...`, etc.). Os exemplos curl já usam $OWNER/$REPO — mantenha,
   agora apontando para os valores fixos.
2. Detecção de auth: mantenha o padrão "gh-first, curl fallback". No fallback, leia o token de
   `GITHUB_TOKEN` ou `GH_TOKEN` do AMBIENTE (não de `~/.hermes/.env` nem `~/.git-credentials` —
   esses são defi-specific). Simplifique o bloco para algo como:
       if command -v gh &>/dev/null && gh auth status &>/dev/null; then AUTH="gh"; else
         AUTH="curl"; GITHUB_TOKEN="${GITHUB_TOKEN:-$GH_TOKEN}"; fi
       OWNER=fabiosiqueira; REPO=hermes-binance
3. GOTCHA dos labels: adicione uma nota explícita (curta) de que `gh issue create --label X`
   ignora silenciosamente labels inexistentes → rodar `gh label list --repo
   fabiosiqueira/hermes-binance` ANTES de criar com `--label`.
4. ESCOPO: mantenha as seções Viewing / Creating / Managing (labels, assign, comment,
   close/reopen) / Triage / Bulk + Quick Reference. REMOVA a seção "Linking Issues to PRs"
   e qualquer menção a criar branch/PR (`gh issue develop`, `git checkout -b`): o HAWK não
   edita código deste repo (sem PRs — YAGNI).
5. Frontmatter: mantenha o bloco YAML (name: github-issues, description, version, etc.).
   Pode atualizar a description p/ refletir o repo alvo, mas mantenha `name: github-issues`.
6. Não mencione `defi-agent`, `defi-project` nem `~/.hermes/.env` em lugar nenhum dos arquivos.

TEMPLATES
- bug-report.md e feature-request.md são genéricos no defi → copie verbatim (são neutros,
  sem referência a defi). Confira que não citam defi-agent.

CONSTRAINTS
- Crie SOMENTE esses 3 arquivos (mais os diretórios que precisarem existir). Não toque em
  nada fora de hermes/skills/github/github-issues/.
- Match de estilo: mantenha o formato markdown/heading da skill original; só edite o conteúdo
  indicado.

OUTPUT
- Reporte: o que mudou vs. a fonte (Setup retargetado, seção de PRs removida, gotcha de labels
  adicionado), e confirmação de que não há string `defi-agent`/`git remote get-url`.

VERIFICAÇÃO
- Rode: test -f hermes/skills/github/github-issues/SKILL.md && test -f hermes/skills/github/github-issues/templates/bug-report.md && test -f hermes/skills/github/github-issues/templates/feature-request.md && grep -rq 'fabiosiqueira/hermes-binance' hermes/skills/github/github-issues && ! grep -rq 'defi-agent' hermes/skills/github/github-issues && ! grep -rq 'git remote get-url' hermes/skills/github/github-issues
- Retorne quando sair com exit 0.
```

#### Task 1.5: Seção GitHub Issues no AGENTS.md [sonnet]

**Files:**
- Modify: `hermes/AGENTS.md`

**Diagnosis:** O `AGENTS.md` (170 linhas) é o contexto operacional que o HAWK carrega. Falta
uma seção curta documentando a nova capacidade de issues. Inserir após a seção "## Serviços que
eu uso" (que descreve Redis e betrader-hydra) — é o lugar natural pra "## GitHub Issues
(backlog de estratégia)", antes do "## Mapa rápido". Conteúdo dirigido pelo spec (§5): repo
alvo `fabiosiqueira/hermes-binance`; gotcha `gh label list` antes de `--label`; o que é
apropriado emitir (débito de estratégia, conflito com dogma, padrão de proposta rejeitada
recorrente); limite (gere backlog, não edita código deste repo). Curto — não inflar o arquivo.

**Verification:** `grep -q 'fabiosiqueira/hermes-binance' hermes/AGENTS.md && grep -q 'gh label list' hermes/AGENTS.md && grep -qi 'github issues' hermes/AGENTS.md`

**Prompt for subagent (Agent tool):**
```
Você está no repo binance-project (cwd: /Users/fabiosiqueira/dev/projetos/hermes/binance-project).
Tarefa: adicionar uma seção curta sobre GitHub Issues ao `hermes/AGENTS.md`.

CONTEXTO
- Leia `hermes/AGENTS.md` para casar o tom (pt-BR direto, 1ª pessoa "eu"/HAWK, headings `##`,
  bullets curtos). O arquivo descreve onde estão as coisas do agente e como ele opera.
- Estrutura atual relevante: "## Serviços que eu uso" (Redis, betrader-hydra) → depois
  "## Mapa rápido". Insira a nova seção ENTRE essas duas (depois do bloco de betrader-hydra,
  antes de "## Mapa rápido").

SEÇÃO A ADICIONAR (ajuste a redação ao estilo do arquivo, mantenha CURTA — ~12-18 linhas):
Título: "## GitHub Issues (backlog de estratégia)"
Conteúdo (cobrir, em prosa/bullets enxutos):
- Tenho `gh` CLI autenticado no boot (auth por arquivo a partir de GITHUB_TOKEN/GH_TOKEN; eu
  nunca leio nem escrevo o token). Uso a skill `github-issues` para a sintaxe dos comandos.
- Repo alvo: `fabiosiqueira/hermes-binance`. Sempre passo `--repo fabiosiqueira/hermes-binance`.
- GOTCHA: `gh issue create --label X` ignora silenciosamente labels que não existem. SEMPRE
  rodar `gh label list --repo fabiosiqueira/hermes-binance` ANTES de criar com `--label`.
- Quando abrir issue (apropriado): débito de estratégia que percebo recorrente, conflito entre
  o que eu proporia e um dogma, padrão de proposta minha rejeitada pelo gate que se repete.
  Só sob demanda numa conversa — NÃO no ciclo automático nem no cron.
- Limite: eu gerencio o BACKLOG (abro/comento/fecho/relabelo issues minhas). NÃO edito o código
  deste repo nem abro PRs — isso é trabalho humano/do coding agent.

CONSTRAINTS
- Toque SOMENTE `hermes/AGENTS.md`. Não altere outras seções, tabelas ou exemplos existentes.
- Não duplique conteúdo da skill (a skill tem a sintaxe; aqui é só o contexto operacional).
- Mantenha curto: é contexto carregado em runtime, não documentação exaustiva.

OUTPUT
- Reporte: o título da seção e onde foi inserida (entre quais seções).

VERIFICAÇÃO
- Rode: grep -q 'fabiosiqueira/hermes-binance' hermes/AGENTS.md && grep -q 'gh label list' hermes/AGENTS.md && grep -qi 'github issues' hermes/AGENTS.md
- Retorne quando sair com exit 0.
```

## Launch order (DAG resolved)

### Phase 0 — parallel

- Cluster 1 / Task 1.1 — Dockerfile (gh CLI)
- Cluster 1 / Task 1.2 — gh_auth.py + testes (+reviewer)
- Cluster 1 / Task 1.3 — composes + .env.example
- Cluster 1 / Task 1.4 — skill github-issues
- Cluster 1 / Task 1.5 — AGENTS.md

**Fan-out Phase 0: 5 parallel tasks**

### Phase 1 — after Phase 0 completes

- (nenhuma — não há dependências)

## Notas operacionais (pós-plano, não-código)

- **Pré-requisito de deploy:** criar/obter `GITHUB_TOKEN` com scope `repo` e setar na env tab
  do Coolify antes do deploy. Sem token, a capacidade degrada para "not logged in" (não quebra).
- **Passo terminal (spec §"Passo terminal"):** após implementar, **parar o app no Coolify (prod)**
  — stop do gateway/serviços, estado/volume preservados — para cortar consumo de quota de LLM.
  Reversível. Esse stop é ação manual do operador, fora do escopo de execução do plano.
```
