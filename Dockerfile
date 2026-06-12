# binance-project — overlay sobre o engine Hermes publicado.
#
# Dois targets:
#   local → engine + deps (redis-cli, mcp[cli], deps do estrategista). Data dir
#           vem por bind mount (./hermes:/opt/data) no compose local.
#           Usado por hermes-compose.local.yml.
#   vps   → local + data dir BAKED na imagem (/opt/data). O volume nomeado
#           inicializa a partir da árvore da imagem (comportamento padrão do
#           Docker, que o engine espera — ver stage2-hook).
#           Usado por hermes-coolify.yml (Contabo).
#
# Diferença do forex-project: sem MCP de trading (MT5/mt5-mcp). O estrategista
# binance executa via betrader REST API diretamente (sem MCP intermediário).
#
# Segredos (.env, *.db, auth.json) são gitignored E excluídos no .dockerignore →
# nunca entram na imagem. O Coolify builda do repo (sem segredos); secrets vêm
# das env vars do Coolify em runtime.
ARG HERMES_ENGINE_TAG=latest
FROM ghcr.io/fabiosiqueira/hermes-engine:${HERMES_ENGINE_TAG} AS local

# redis client (python) — Hermes/plugins binance conectam no serviço redis do compose.
# mcp[cli] — o engine publicado NÃO traz as deps de MCP client; sem isso
# `hermes mcp` falha ("typer is required") e o runtime não conecta no MCP.
# pydantic/httpx/prometheus-client/pyyaml — deps dos scripts do estrategista binance.
RUN uv pip install --python /opt/hermes/.venv/bin/python \
    redis \
    "mcp[cli]" \
    pydantic \
    httpx \
    prometheus-client \
    pyyaml

# redis-cli (binário) — debug/health do serviço redis a partir do gateway.
RUN apt-get update \
    && apt-get install -y --no-install-recommends redis-tools \
    && rm -rf /var/lib/apt/lists/*

# ---------- GitHub CLI (HAWK usa gh p/ abrir issues/PRs) ----------
# Só o binário; a auth (hosts.yml) é escrita no boot por scripts/gh_auth.py
# a partir de GITHUB_TOKEN/GH_TOKEN. GH_CONFIG_DIR fica fora do volume /opt/data
# p/ não vazar token no working tree e ser imune ao masking do env do agente.
ARG GH_VERSION=2.93.0
RUN curl -fsSL --retry 3 -o /tmp/gh.tar.gz \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    && tar -C /tmp -xzf /tmp/gh.tar.gz \
    && install -m 0755 "/tmp/gh_${GH_VERSION}_linux_amd64/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_amd64"
ENV GH_CONFIG_DIR=/opt/hermes/.gh
RUN mkdir -p /opt/hermes/.gh && chown hermes:hermes /opt/hermes/.gh && chmod 700 /opt/hermes/.gh

# ---------- Stage vps: data dir baked (deploy Coolify/Contabo) ----------
# Sem bind mount na Contabo: o data dir committed (config.yaml com mcp_servers,
# skills/, SOUL.md, AGENTS.md, memories/) é copiado para /opt/data. O volume
# nomeado `hermes-data` semeia daqui no 1º boot; stage2-hook ajusta perms/UID.
FROM local AS vps
COPY hermes/ /opt/data/
# CRÍTICO: o COPY entra como root (dirs 700) → o usuário `hermes` (runtime) não lê
# /opt/data/hooks nem config.yaml → PermissionError em hooks.discover_and_load() →
# gateway em crash-loop. O stage2-hook do engine não cobre. chown aqui resolve.
RUN chown -R hermes:hermes /opt/data
VOLUME ["/opt/data"]
