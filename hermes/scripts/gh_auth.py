# Auth do gh CLI por arquivo no boot do container gateway.
#
# Por que arquivo em vez de `gh auth login --with-token`:
#   O harness do agente mascara strings que parecem tokens nos subprocessos; o
#   pipe para stdin do `gh` recebe string vazia. Além disso, --with-token exige
#   o escopo read:org que o token de operação não tem. Escrever hosts.yml
#   diretamente (formato idêntico ao que o próprio gh gera) é a única abordagem
#   robusta e sem dependência de escopo extra.
#
# Comportamento:
#   - Lê token de GH_TOKEN (override local) ou GITHUB_TOKEN (prod/Coolify).
#   - Se nenhum token presente → NO-OP, exit 0. Degrada para "not logged in";
#     nunca derruba o gateway.
#   - Se token presente → garante GH_CONFIG_DIR, escreve hosts.yml com chmod
#     600 e retorna exit 0.
#   - NUNCA loga o valor do token.
import os
import sys
from pathlib import Path

_DEFAULT_CONFIG_DIR = "/opt/hermes/.gh"
_DEFAULT_USER = "fabiosiqueira"

# Template do hosts.yml. Indentação de 4 espaços é exigida pelo formato do gh.
_HOSTS_TEMPLATE = """\
github.com:
    oauth_token: {token}
    user: {user}
    git_protocol: https
"""


def _resolve_token() -> str:
    """Retorna o token com strip, preferindo GH_TOKEN sobre GITHUB_TOKEN.

    Retorna string vazia se nenhum estiver definido ou for vazio após strip.
    """
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token
    return os.environ.get("GITHUB_TOKEN", "").strip()


def _write_hosts(config_dir: Path, token: str, user: str) -> Path:
    """Escreve hosts.yml em config_dir com chmod 600. Idempotente.

    Retorna o Path do arquivo escrito.
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    hosts_path = config_dir / "hosts.yml"
    hosts_path.write_text(_HOSTS_TEMPLATE.format(token=token, user=user))
    hosts_path.chmod(0o600)
    return hosts_path


def main(argv=None) -> int:
    token = _resolve_token()
    if not token:
        return 0

    config_dir = Path(os.environ.get("GH_CONFIG_DIR", _DEFAULT_CONFIG_DIR))
    user = os.environ.get("GH_AUTH_USER", _DEFAULT_USER)

    hosts_path = _write_hosts(config_dir, token, user)
    print(f"[gh_auth] hosts.yml escrito (GH_CONFIG_DIR={config_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
