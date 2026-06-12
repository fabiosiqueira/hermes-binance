# Unit tests do módulo gh_auth (autenticação do gh CLI por arquivo).
#
# Mock APENAS na fronteira env (monkeypatch.setenv) e filesystem (tmp_path).
# Nenhuma função interna é mockada — DI real via env + Path injetado em
# _write_hosts. Os testes cobrem: token via GH_TOKEN, token via GITHUB_TOKEN,
# precedência de GH_TOKEN sobre GITHUB_TOKEN, usuário customizado via
# GH_AUTH_USER, ausência de token (NO-OP), e idempotência.
from pathlib import Path

import pytest

from gh_auth import _write_hosts, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, tmp_path, *, gh_token=None, github_token=None, user=None):
    """Configura env e roda main() com GH_CONFIG_DIR apontando para tmp_path."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_AUTH_USER", raising=False)
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path))

    if gh_token is not None:
        monkeypatch.setenv("GH_TOKEN", gh_token)
    if github_token is not None:
        monkeypatch.setenv("GITHUB_TOKEN", github_token)
    if user is not None:
        monkeypatch.setenv("GH_AUTH_USER", user)

    return main()


# ---------------------------------------------------------------------------
# Casos obrigatórios
# ---------------------------------------------------------------------------


def test_token_via_gh_token_escreve_hosts_yml(monkeypatch, tmp_path) -> None:
    """GH_TOKEN presente → hosts.yml escrito com conteúdo correto e chmod 600."""
    rc = _run_main(monkeypatch, tmp_path, gh_token="ghp_abc123")

    assert rc == 0
    hosts = tmp_path / "hosts.yml"
    assert hosts.exists()
    content = hosts.read_text()
    assert "github.com:" in content
    assert "oauth_token: ghp_abc123" in content
    assert "user: fabiosiqueira" in content
    assert "git_protocol: https" in content
    assert oct(hosts.stat().st_mode & 0o777) == "0o600"


def test_token_via_github_token_escreve_hosts_yml(monkeypatch, tmp_path) -> None:
    """GITHUB_TOKEN presente (GH_TOKEN ausente) → mesmo resultado."""
    rc = _run_main(monkeypatch, tmp_path, github_token="ghp_xyz789")

    assert rc == 0
    hosts = tmp_path / "hosts.yml"
    assert hosts.exists()
    content = hosts.read_text()
    assert "oauth_token: ghp_xyz789" in content
    assert oct(hosts.stat().st_mode & 0o777) == "0o600"


def test_gh_token_tem_precedencia_sobre_github_token(monkeypatch, tmp_path) -> None:
    """GH_TOKEN vence GITHUB_TOKEN quando ambos estão presentes."""
    rc = _run_main(monkeypatch, tmp_path, gh_token="ghp_local", github_token="ghp_prod")

    assert rc == 0
    hosts = tmp_path / "hosts.yml"
    content = hosts.read_text()
    assert "oauth_token: ghp_local" in content
    assert "ghp_prod" not in content


def test_gh_auth_user_customizado_aparece_no_hosts(monkeypatch, tmp_path) -> None:
    """GH_AUTH_USER customizado → campo user reflete o valor."""
    rc = _run_main(monkeypatch, tmp_path, gh_token="ghp_tok", user="outrouser")

    assert rc == 0
    hosts = tmp_path / "hosts.yml"
    assert "user: outrouser" in hosts.read_text()


def test_sem_token_noop_sem_hosts_yml(monkeypatch, tmp_path) -> None:
    """Sem GH_TOKEN nem GITHUB_TOKEN → exit 0, hosts.yml NÃO criado."""
    rc = _run_main(monkeypatch, tmp_path)

    assert rc == 0
    assert not (tmp_path / "hosts.yml").exists()


def test_idempotencia_duas_execucoes_arquivo_valido(monkeypatch, tmp_path) -> None:
    """Rodar duas vezes → arquivo permanece válido e sem erro."""
    _run_main(monkeypatch, tmp_path, gh_token="ghp_idem")
    rc = _run_main(monkeypatch, tmp_path, gh_token="ghp_idem")

    assert rc == 0
    hosts = tmp_path / "hosts.yml"
    content = hosts.read_text()
    assert "oauth_token: ghp_idem" in content
    assert oct(hosts.stat().st_mode & 0o777) == "0o600"


# ---------------------------------------------------------------------------
# _write_hosts: testes da função pura
# ---------------------------------------------------------------------------


def test_write_hosts_cria_diretorio_se_ausente(tmp_path) -> None:
    """_write_hosts cria GH_CONFIG_DIR (parents) se não existir."""
    config_dir = tmp_path / "nested" / "dir" / ".gh"
    _write_hosts(config_dir, "ghp_tok", "fabiosiqueira")
    assert (config_dir / "hosts.yml").exists()


def test_write_hosts_retorna_path_do_arquivo(tmp_path) -> None:
    result = _write_hosts(tmp_path, "ghp_tok", "fabiosiqueira")
    assert result == tmp_path / "hosts.yml"
