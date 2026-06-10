# Smoke test: prova que o harness de testes está funcional e todas as
# dependências do estrategista estão instaladas e importáveis.
import pydantic
import httpx
import prometheus_client
import redis
import yaml
import respx
import fakeredis


def test_smoke_imports() -> None:
    assert True
