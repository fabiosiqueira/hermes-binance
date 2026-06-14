# Unit tests do commit escopado do HAWK (commit_domain).
#
# Fronteira de SEGURANÇA: o HAWK só pode commitar o próprio domínio (skills autorais,
# SOUL.md, AGENTS.md, memories/, cron/jobs.json). NUNCA scripts/, dogmas.yaml,
# config.yaml, Dockerfile, *compose* nem .env*. A allowlist é construída a partir de
# roots conhecidos; `is_denied` é o backstop absoluto (defesa em profundidade).
#
# Caminho coberto: parsing do .bundled_manifest; backstop de negação (incl. path
# traversal); resolução de skills autorais (leaf ∉ manifest); coleta final = allowlist
# ∩ existentes − negados. I/O de git é mockado por um runner injetado (DI).
from types import SimpleNamespace

import pytest

from commit_domain import (
    build_push_url,
    collect_committable_files,
    commit_and_push,
    is_denied,
    main,
    parse_bundled_manifest,
)


# --- parse_bundled_manifest (pura) ---


def test_parse_bundled_manifest_extrai_nomes_antes_do_hash() -> None:
    text = "apple-notes:5e448abf\ngithub-issues:429ec06c\narxiv:06b66666\n"
    assert parse_bundled_manifest(text) == frozenset(
        {"apple-notes", "github-issues", "arxiv"}
    )


def test_parse_bundled_manifest_ignora_linhas_vazias_e_sem_dois_pontos() -> None:
    text = "apple-notes:5e448abf\n\nlinha-suja-sem-hash\ngithub-issues:429ec06c\n"
    assert parse_bundled_manifest(text) == frozenset({"apple-notes", "github-issues"})


def test_parse_bundled_manifest_vazio_retorna_frozenset_vazio() -> None:
    assert parse_bundled_manifest("") == frozenset()


# --- is_denied: backstop absoluto (security-critical) ---


@pytest.mark.parametrize(
    "relpath",
    [
        "scripts/risk_gateway.py",
        "scripts/risk_engine.py",
        "scripts/strategist_cycle.py",
        "scripts/commit_domain.py",
        "dogmas.yaml",
        "config.yaml",
        "Dockerfile",
        "docker-compose.yaml",
        "hermes-compose.local.yml",
        ".env",
        ".env.agent",
        ".gitignore",
        "../config.yaml",
        "skills/../dogmas.yaml",
        "/opt/data/dogmas.yaml",
    ],
)
def test_is_denied_bloqueia_codigo_constituicao_infra_e_traversal(relpath) -> None:
    assert is_denied(relpath) is True


@pytest.mark.parametrize(
    "relpath",
    [
        "SOUL.md",
        "AGENTS.md",
        "cron/jobs.json",
        "memories/MEMORY.md",
        "memories/USER.md",
        "skills/hawk-strategist-cycle/SKILL.md",
        "skills/hawk-strategist-cycle/references/diagnose-brief-502.md",
    ],
)
def test_is_denied_libera_dominio_do_hawk(relpath) -> None:
    assert is_denied(relpath) is False


# --- collect_committable_files: allowlist ∩ existentes − negados ---


def _seed_data_dir(root) -> None:
    """Cria uma árvore /opt/data realista: domínio do HAWK + arquivos proibidos."""
    (root / "SOUL.md").write_text("persona")
    (root / "AGENTS.md").write_text("contexto")
    (root / "config.yaml").write_text("model: x")  # proibido
    (root / "dogmas.yaml").write_text("max_leverage: 3")  # proibido
    (root / "docker-compose.yaml").write_text("services: {}")  # proibido
    (root / ".env").write_text("SECRET=1")  # proibido
    (root / "memories").mkdir()
    (root / "memories" / "MEMORY.md").write_text("mem")
    (root / "cron").mkdir()
    (root / "cron" / "jobs.json").write_text("[]")
    (root / "scripts").mkdir()
    (root / "scripts" / "risk_gateway.py").write_text("# gate")  # proibido
    skills = root / "skills"
    (skills / "hawk-strategist-cycle" / "references").mkdir(parents=True)
    (skills / "hawk-strategist-cycle" / "SKILL.md").write_text("autoral")
    (skills / "hawk-strategist-cycle" / "references" / "diagnose-brief-502.md").write_text("ref")
    (skills / "apple" / "apple-notes").mkdir(parents=True)
    (skills / "apple" / "apple-notes" / "SKILL.md").write_text("bundled")
    (skills / "github" / "github-issues").mkdir(parents=True)
    (skills / "github" / "github-issues" / "SKILL.md").write_text("bundled-retarget")


BUNDLED = frozenset({"apple-notes", "github-issues"})


def test_collect_inclui_dominio_do_hawk_e_skill_autoral(tmp_path) -> None:
    _seed_data_dir(tmp_path)
    got = set(collect_committable_files(tmp_path, BUNDLED))
    assert got == {
        "SOUL.md",
        "AGENTS.md",
        "cron/jobs.json",
        "memories/MEMORY.md",
        "skills/hawk-strategist-cycle/SKILL.md",
        "skills/hawk-strategist-cycle/references/diagnose-brief-502.md",
    }


def test_collect_nunca_inclui_proibidos(tmp_path) -> None:
    _seed_data_dir(tmp_path)
    got = set(collect_committable_files(tmp_path, BUNDLED))
    for proibido in (
        "config.yaml",
        "dogmas.yaml",
        "docker-compose.yaml",
        ".env",
        "scripts/risk_gateway.py",
    ):
        assert proibido not in got


def test_collect_exclui_skills_bundled(tmp_path) -> None:
    _seed_data_dir(tmp_path)
    got = set(collect_committable_files(tmp_path, BUNDLED))
    assert not any(p.startswith("skills/apple/") for p in got)
    assert not any(p.startswith("skills/github/") for p in got)


def test_collect_so_retorna_arquivos_existentes(tmp_path) -> None:
    # Só SOUL.md existe; nada mais do domínio foi semeado.
    (tmp_path / "SOUL.md").write_text("persona")
    got = collect_committable_files(tmp_path, frozenset())
    assert got == ["SOUL.md"]


# --- build_push_url (pura) ---


def test_build_push_url_embute_token_no_formato_x_access_token() -> None:
    url = build_push_url("fabiosiqueira/hermes-binance", "ghp_tok")
    assert url == "https://x-access-token:ghp_tok@github.com/fabiosiqueira/hermes-binance.git"


# --- commit_and_push: orquestração de git (runner injetado) ---


class _FakeRunner:
    """Runner injetável: grava os comandos e devolve returncode configurável.

    `diff_quiet_rc` é o rc de `git diff --cached --quiet` (1 = há mudanças staged).
    `fail_on` faz qualquer cmd que contenha esse verbo retornar rc 1 (simula erro git).
    """

    def __init__(self, diff_quiet_rc: int = 1, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self._diff_quiet_rc = diff_quiet_rc
        self._fail_on = fail_on

    def __call__(self, cmd: list[str]):
        self.calls.append(cmd)
        if self._fail_on is not None and self._fail_on in cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        rc = self._diff_quiet_rc if "--quiet" in cmd else 0
        return SimpleNamespace(returncode=rc, stdout="", stderr="")


def _seeded_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _seed_data_dir(data)
    return data, tmp_path / "clone"


def _git_verb(cmd: list[str]) -> str:
    """Subcomando git de um cmd, pulando `-C <dir>` (ex.: ['git','-C',d,'add'] → 'add')."""
    i = cmd.index("git") + 1
    if cmd[i] == "-C":
        i += 2
    return cmd[i]


def test_commit_and_push_copia_dominio_e_emite_git_na_ordem(tmp_path) -> None:
    data, clone = _seeded_data(tmp_path)
    runner = _FakeRunner(diff_quiet_rc=1)

    res = commit_and_push(
        data,
        clone,
        repo="fabiosiqueira/hermes-binance",
        token="ghp_tok",
        branch="main",
        message="hawk: update skill",
        bundled_names=BUNDLED,
        runner=runner,
    )

    assert res["committed"] is True
    # Domínio do HAWK foi copiado para clone/hermes/<rel>.
    assert (clone / "hermes" / "SOUL.md").is_file()
    assert (clone / "hermes" / "skills" / "hawk-strategist-cycle" / "SKILL.md").is_file()
    # Ordem dos comandos git: clone → add → commit → push.
    verbs = [_git_verb(c) for c in runner.calls]
    assert verbs[0] == "clone"
    assert "commit" in verbs and "push" in verbs
    assert verbs.index("commit") < verbs.index("push")


def test_commit_and_push_nunca_copia_proibido(tmp_path) -> None:
    data, clone = _seeded_data(tmp_path)
    runner = _FakeRunner(diff_quiet_rc=1)

    commit_and_push(
        data,
        clone,
        repo="fabiosiqueira/hermes-binance",
        token="ghp_tok",
        branch="main",
        message="m",
        bundled_names=BUNDLED,
        runner=runner,
    )

    assert not (clone / "hermes" / "scripts" / "risk_gateway.py").exists()
    assert not (clone / "hermes" / "dogmas.yaml").exists()
    assert not (clone / "hermes" / "config.yaml").exists()
    assert not (clone / "hermes" / ".env").exists()


def test_commit_and_push_sem_mudancas_nao_commita(tmp_path) -> None:
    data, clone = _seeded_data(tmp_path)
    runner = _FakeRunner(diff_quiet_rc=0)  # nada staged difere do repo

    res = commit_and_push(
        data,
        clone,
        repo="fabiosiqueira/hermes-binance",
        token="ghp_tok",
        branch="main",
        message="m",
        bundled_names=BUNDLED,
        runner=runner,
    )

    assert res["committed"] is False
    assert res["reason"] == "no_changes"
    verbs = [_git_verb(c) for c in runner.calls]
    assert "push" not in verbs


def test_commit_and_push_clone_falha_retorna_git_error_sem_commit(tmp_path) -> None:
    data, clone = _seeded_data(tmp_path)
    runner = _FakeRunner(fail_on="clone")

    res = commit_and_push(
        data,
        clone,
        repo="fabiosiqueira/hermes-binance",
        token="ghp_tok",
        branch="main",
        message="m",
        bundled_names=BUNDLED,
        runner=runner,
    )

    assert res["committed"] is False
    assert res["reason"] == "git_error"
    assert res["step"] == "clone"
    verbs = [_git_verb(c) for c in runner.calls]
    assert "commit" not in verbs and "push" not in verbs


def test_commit_and_push_push_falha_nao_reporta_sucesso(tmp_path) -> None:
    data, clone = _seeded_data(tmp_path)
    runner = _FakeRunner(diff_quiet_rc=1, fail_on="push")

    res = commit_and_push(
        data,
        clone,
        repo="fabiosiqueira/hermes-binance",
        token="ghp_tok",
        branch="main",
        message="m",
        bundled_names=BUNDLED,
        runner=runner,
    )

    assert res["committed"] is False
    assert res["reason"] == "git_error"
    assert res["step"] == "push"


def test_commit_and_push_nada_committavel_nao_clona(tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()  # vazio: nenhum arquivo do domínio
    clone = tmp_path / "clone"
    runner = _FakeRunner()

    res = commit_and_push(
        data,
        clone,
        repo="fabiosiqueira/hermes-binance",
        token="ghp_tok",
        branch="main",
        message="m",
        bundled_names=frozenset(),
        runner=runner,
    )

    assert res["committed"] is False
    assert res["reason"] == "nothing_to_commit"
    assert runner.calls == []  # não clonou nada


# --- main(): wiring de env (token, manifest, fail-safe) ---


def test_main_sem_token_retorna_2_e_nao_chama_git(monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    runner = _FakeRunner()
    rc = main(["commit_domain.py"], runner=runner)
    assert rc == 2
    assert runner.calls == []


def test_main_com_token_committa_dominio(monkeypatch, tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _seed_data_dir(data)
    (data / "skills" / ".bundled_manifest").write_text("apple-notes:h\ngithub-issues:h\n")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_prod")
    monkeypatch.setenv("HERMES_DATA_DIR", str(data))
    runner = _FakeRunner(diff_quiet_rc=1)

    rc = main(["commit_domain.py", "hawk: sync"], runner=runner)

    assert rc == 0
    verbs = [_git_verb(c) for c in runner.calls]
    assert verbs[0] == "clone"
    assert "push" in verbs


def test_main_gh_token_precede_github_token(monkeypatch, tmp_path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "SOUL.md").write_text("persona")
    monkeypatch.setenv("GH_TOKEN", "ghp_local")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_prod")
    monkeypatch.setenv("HERMES_DATA_DIR", str(data))
    runner = _FakeRunner(diff_quiet_rc=1)

    main(["commit_domain.py"], runner=runner)

    clone_cmd = runner.calls[0]
    assert any("x-access-token:ghp_local@" in tok for tok in clone_cmd)
    assert all("ghp_prod" not in tok for tok in clone_cmd)
