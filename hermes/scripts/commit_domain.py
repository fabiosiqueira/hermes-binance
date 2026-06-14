# Commit escopado do HAWK: deixa o agente versionar APENAS o próprio domínio
# (skills autorais, SOUL.md, AGENTS.md, memories/, cron/jobs.json) no repo
# fabiosiqueira/hermes-binance. NUNCA scripts/, dogmas.yaml, config.yaml,
# Dockerfile, *compose* nem .env* — código/constituição/infra são território do
# operador (ver dogmas.yaml e o AGENTS.md do agente).
#
# A fronteira de escopo é ESTE script, não a disciplina do HAWK: a allowlist é
# construída a partir de roots conhecidos e `is_denied` é o backstop absoluto
# (defesa em profundidade — se a allowlist deixar passar algo proibido, o backstop
# remove). Skills autorais = leaf dir NÃO presente no `.bundled_manifest` do engine.
#
# Auth do git: push via https://x-access-token:$GITHUB_TOKEN@github.com/... — mesmo
# token (`GITHUB_TOKEN`/`GH_TOKEN`) que o gh já usa; o valor JAMAIS é logado.
# Sem clone baked: clona on-demand num tmpdir (zero mudança de Dockerfile/compose).
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_DEFAULT_REPO = "fabiosiqueira/hermes-binance"
_DEFAULT_BRANCH = "main"
_GIT_USER_NAME = "HAWK (Hermes Agent)"
_GIT_USER_EMAIL = "hawk@hermes-binance.local"

# --- Allowlist (o que o HAWK PODE commitar) ---
_ALLOWED_FILES = ("SOUL.md", "AGENTS.md", "cron/jobs.json")
_ALLOWED_DIRS = ("memories",)
_SKILLS_DIR = "skills"
_BUNDLED_MANIFEST = "skills/.bundled_manifest"

# --- Denylist absoluta (backstop — NUNCA commitar, mesmo que a allowlist falhe) ---
_DENIED_FILES = frozenset(
    {
        "dogmas.yaml",
        "config.yaml",
        "Dockerfile",
        "docker-compose.yaml",
        "hermes-compose.local.yml",
        ".gitignore",
    }
)
_DENIED_PREFIXES = ("scripts/",)
_DENIED_BASENAME_PREFIXES = (".env",)


def parse_bundled_manifest(text: str) -> frozenset[str]:
    """Extrai os nomes de skills bundled (parte antes do `:`) do .bundled_manifest.

    Formato: uma linha `nome:hash` por skill. Linhas vazias ou sem `:` são ignoradas.
    """
    names = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        names.add(line.split(":", 1)[0])
    return frozenset(names)


def is_denied(relpath: str) -> bool:
    """Backstop absoluto: True se o path NUNCA pode ser commitado pelo HAWK.

    Bloqueia: traversal/absolutos, código (`scripts/`), constituição/config/infra
    (`dogmas.yaml`, `config.yaml`, `Dockerfile`, composes), `.gitignore` e segredos
    (`.env*`). Defesa em profundidade — independe da allowlist.
    """
    if relpath.startswith("/") or ".." in Path(relpath).parts:
        return True
    if relpath in _DENIED_FILES:
        return True
    if any(relpath.startswith(prefix) for prefix in _DENIED_PREFIXES):
        return True
    return any(Path(relpath).name.startswith(p) for p in _DENIED_BASENAME_PREFIXES)


def authored_skill_dirs(data_dir, bundled_names: frozenset[str]) -> list[str]:
    """Diretórios de skills AUTORAIS (relativos a data_dir): contêm SKILL.md e cujo
    leaf dir NÃO está no manifest de bundled do engine. Ex.: `skills/hawk-strategist-cycle`.
    """
    data_dir = Path(data_dir)
    skills_root = data_dir / _SKILLS_DIR
    if not skills_root.is_dir():
        return []
    dirs = []
    for skill_md in skills_root.rglob("SKILL.md"):
        if skill_md.parent.name in bundled_names:
            continue
        dirs.append(skill_md.parent.relative_to(data_dir).as_posix())
    return sorted(dirs)


def collect_committable_files(data_dir, bundled_names: frozenset[str]) -> list[str]:
    """Paths (relativos a data_dir) que o HAWK pode commitar: allowlist ∩ existentes
    − negados. Determinístico (ordenado), sem duplicatas.
    """
    data_dir = Path(data_dir)
    candidates: list[str] = []

    for rel in _ALLOWED_FILES:
        if (data_dir / rel).is_file():
            candidates.append(rel)

    for allowed_dir in _ALLOWED_DIRS:
        base = data_dir / allowed_dir
        if base.is_dir():
            candidates.extend(
                p.relative_to(data_dir).as_posix() for p in base.rglob("*") if p.is_file()
            )

    for skill_dir in authored_skill_dirs(data_dir, bundled_names):
        base = data_dir / skill_dir
        candidates.extend(
            p.relative_to(data_dir).as_posix() for p in base.rglob("*") if p.is_file()
        )

    return sorted({c for c in candidates if not is_denied(c)})


def build_push_url(repo: str, token: str) -> str:
    """URL HTTPS autenticada para push. O token só aparece aqui — JAMAIS logado."""
    return f"https://x-access-token:{token}@github.com/{repo}.git"


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    """Runner real: subprocess.run sem shell, captura saída, não levanta em rc≠0."""
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _failed(result: object) -> bool:
    """True se o comando git falhou (returncode ≠ 0)."""
    return getattr(result, "returncode", 0) != 0


def commit_and_push(
    data_dir,
    clone_dir,
    *,
    repo: str,
    token: str,
    branch: str,
    message: str,
    bundled_names: frozenset[str],
    runner: Callable[[list[str]], object] = _default_runner,
) -> dict:
    """Clona o repo, copia SÓ o domínio committável do HAWK para `clone_dir/hermes/`,
    e commita+push se houver mudança. Idempotente: sem diff staged → não commita.

    `data_dir` mapeia para `hermes/` no repo (o data dir do agente = /opt/data).
    Retorna `{committed, files, reason?}`.
    """
    files = collect_committable_files(data_dir, bundled_names)
    if not files:
        return {"committed": False, "files": [], "reason": "nothing_to_commit"}

    clone_dir = Path(clone_dir)
    push_url = build_push_url(repo, token)
    if _failed(runner(["git", "clone", "--depth", "1", "--branch", branch, push_url, str(clone_dir)])):
        return {"committed": False, "files": files, "reason": "git_error", "step": "clone"}

    base = ["git", "-C", str(clone_dir)]
    runner([*base, "config", "user.name", _GIT_USER_NAME])
    runner([*base, "config", "user.email", _GIT_USER_EMAIL])

    staged: list[str] = []
    for rel in files:
        dst = clone_dir / "hermes" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(data_dir) / rel, dst)
        staged.append(f"hermes/{rel}")

    runner([*base, "add", *staged])

    # `diff --cached --quiet`: rc 0 = nada staged difere → idempotente, não commita.
    if not _failed(runner([*base, "diff", "--cached", "--quiet"])):
        return {"committed": False, "files": files, "reason": "no_changes"}

    if _failed(runner([*base, "commit", "-m", message])):
        return {"committed": False, "files": files, "reason": "git_error", "step": "commit"}
    if _failed(runner([*base, "push", "origin", branch])):
        return {"committed": False, "files": files, "reason": "git_error", "step": "push"}
    return {"committed": True, "files": files, "staged": staged}


def main(argv=None, *, runner: Callable[[list[str]], object] = _default_runner) -> int:
    """Commita o domínio do HAWK. Mensagem = argv[1] (ou default).

    Token de GH_TOKEN (override local) ou GITHUB_TOKEN (prod/Coolify). Sem token →
    erro explícito (exit 2) — diferente do gh_auth (boot no-op): aqui é ação direta.
    """
    argv = sys.argv if argv is None else argv
    token = os.environ.get("GH_TOKEN", "").strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("[commit_domain] erro: GH_TOKEN/GITHUB_TOKEN ausente — nada commitado.", file=sys.stderr)
        return 2

    data_dir = os.environ.get("HERMES_DATA_DIR", "/opt/data")
    repo = os.environ.get("COMMIT_DOMAIN_REPO", _DEFAULT_REPO)
    branch = os.environ.get("COMMIT_DOMAIN_BRANCH", _DEFAULT_BRANCH)
    message = argv[1] if len(argv) > 1 else "hawk: sync own domain (skills/persona/memory)"

    manifest = Path(data_dir) / _BUNDLED_MANIFEST
    bundled = parse_bundled_manifest(manifest.read_text()) if manifest.is_file() else frozenset()

    with tempfile.TemporaryDirectory() as tmp:
        clone_dir = Path(tmp) / "repo"
        result = commit_and_push(
            data_dir, clone_dir, repo=repo, token=token, branch=branch,
            message=message, bundled_names=bundled, runner=runner,
        )

    if result["committed"]:
        print(f"[commit_domain] commit+push ok: {len(result['files'])} arquivo(s) → {repo}@{branch}")
    else:
        print(f"[commit_domain] nada commitado ({result['reason']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
