from fastapi.testclient import TestClient
from fakeredis.aioredis import FakeRedis
import pytest

from app.main import app
from app.services.event_resolver import EventResolver
from app.services.router import BackendRouter


@pytest.fixture
def client() -> TestClient:
    redis = FakeRedis()

    async def health_check(_backend: str) -> bool:
        return True

    async def setup() -> None:
        await redis.sadd("atlantis:backends", "http://backend")

    import asyncio

    asyncio.get_event_loop().run_until_complete(setup())

    router = BackendRouter(
        redis=redis,
        backends_key="atlantis:backends",
        sticky_prefix="atlantis:sticky",
        round_robin_key="atlantis:rr:index",
        health_check=health_check,
    )

    class DummyProxy:
        async def forward(self, request, backend_url, upstream_path=None):
            from starlette.responses import JSONResponse

            return JSONResponse(
                {
                    "backend": backend_url,
                    "path": request.url.path,
                    "upstream_path": upstream_path,
                    "method": request.method,
                }
            )

        async def close(self):
            return None

    app.state.redis = redis
    app.state.router = router
    app.state.resolver = EventResolver()
    app.state.proxy = DummyProxy()

    return TestClient(app)



def test_post_events_pr_created_is_sticky(client: TestClient) -> None:
    payload = {
        "action": "opened",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "pull_request": {"number": 9},
    }

    first = client.post("/events", json=payload, headers={"X-Gitea-Event": "pull_request"})
    second = client.post("/events", json=payload, headers={"X-Gitea-Event": "pull_request"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["backend"] == second.json()["backend"]


def test_post_events_pr_closed_is_sticky(client: TestClient) -> None:
    payload = {
        "action": "closed",
        "repository": {"name": "infra", "owner": {"username": "acme"}},
        "pull_request": {"number": 9, "merged": False},
    }

    first = client.post("/events", json=payload, headers={"X-Gitea-Event": "pull_request"})
    second = client.post("/events", json=payload, headers={"X-Gitea-Event": "pull_request"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["backend"] == second.json()["backend"]



def test_post_events_non_matching_is_non_sticky(client: TestClient) -> None:
    response = client.post("/events", json={"hello": "world"}, headers={"X-Gitea-Event": "push"})

    assert response.status_code == 200
    assert response.json()["backend"] == "http://backend"
