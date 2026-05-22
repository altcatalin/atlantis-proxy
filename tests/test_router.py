import pytest
from fakeredis.aioredis import FakeRedis

from app.services.router import BackendRouter


@pytest.mark.asyncio
async def test_sticky_mapping_is_reused() -> None:
    redis = FakeRedis()
    await redis.sadd("atlantis:backends", "http://a", "http://b")

    async def health_check(_backend: str) -> bool:
        return True

    router = BackendRouter(
        redis=redis,
        backends_key="atlantis:backends",
        sticky_prefix="atlantis:sticky",
        round_robin_key="atlantis:rr:index",
        health_check=health_check,
    )

    first = await router.sticky_backend("pr:acme:repo:10")
    second = await router.sticky_backend("pr:acme:repo:10")

    assert first.backend_url == second.backend_url
    assert second.is_sticky
    assert not second.failover


@pytest.mark.asyncio
async def test_round_robin_for_new_keys() -> None:
    redis = FakeRedis()
    await redis.sadd("atlantis:backends", "http://a", "http://b")

    async def health_check(_backend: str) -> bool:
        return True

    router = BackendRouter(
        redis=redis,
        backends_key="atlantis:backends",
        sticky_prefix="atlantis:sticky",
        round_robin_key="atlantis:rr:index",
        health_check=health_check,
    )

    first = await router.sticky_backend("pr:acme:repo:1")
    second = await router.sticky_backend("pr:acme:repo:2")

    assert first.backend_url != second.backend_url


@pytest.mark.asyncio
async def test_failover_reassigns_when_mapped_backend_unhealthy() -> None:
    redis = FakeRedis()
    await redis.sadd("atlantis:backends", "http://a", "http://b")
    await redis.set("atlantis:sticky:pr:acme:repo:10", "http://a")

    async def health_check(backend: str) -> bool:
        return backend == "http://b"

    router = BackendRouter(
        redis=redis,
        backends_key="atlantis:backends",
        sticky_prefix="atlantis:sticky",
        round_robin_key="atlantis:rr:index",
        health_check=health_check,
    )

    result = await router.sticky_backend("pr:acme:repo:10")

    assert result.backend_url == "http://b"
    assert result.failover


@pytest.mark.asyncio
async def test_random_backend_for_non_sticky_requests() -> None:
    redis = FakeRedis()
    await redis.sadd("atlantis:backends", "http://a")

    async def health_check(_backend: str) -> bool:
        return True

    router = BackendRouter(
        redis=redis,
        backends_key="atlantis:backends",
        sticky_prefix="atlantis:sticky",
        round_robin_key="atlantis:rr:index",
        health_check=health_check,
    )

    result = await router.random_backend()

    assert result.backend_url == "http://a"
    assert not result.is_sticky
