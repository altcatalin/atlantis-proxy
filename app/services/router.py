from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Awaitable, Callable

from redis.asyncio import Redis


class NoHealthyBackendsError(RuntimeError):
    pass


HealthCheck = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class RouteDecision:
    backend_url: str
    is_sticky: bool
    sticky_key: str | None
    failover: bool


class BackendRouter:
    def __init__(
        self,
        redis: Redis,
        backends_key: str,
        sticky_prefix: str,
        round_robin_key: str,
        health_check: HealthCheck,
    ) -> None:
        self._redis = redis
        self._backends_key = backends_key
        self._sticky_prefix = sticky_prefix
        self._round_robin_key = round_robin_key
        self._health_check = health_check

    async def sticky_backend(self, sticky_key: str) -> RouteDecision:
        healthy_backends = await self._healthy_backends()
        sticky_redis_key = self._sticky_redis_key(sticky_key)

        mapped_backend_raw = await self._redis.get(sticky_redis_key)
        mapped_backend = mapped_backend_raw.decode() if mapped_backend_raw else None

        if mapped_backend and mapped_backend in healthy_backends:
            return RouteDecision(
                backend_url=mapped_backend,
                is_sticky=True,
                sticky_key=sticky_key,
                failover=False,
            )

        selected_backend = await self._round_robin_select(healthy_backends)
        await self._redis.set(sticky_redis_key, selected_backend)

        return RouteDecision(
            backend_url=selected_backend,
            is_sticky=True,
            sticky_key=sticky_key,
            failover=bool(mapped_backend and mapped_backend != selected_backend),
        )

    async def random_backend(self) -> RouteDecision:
        healthy_backends = await self._healthy_backends()
        return RouteDecision(
            backend_url=random.choice(healthy_backends),
            is_sticky=False,
            sticky_key=None,
            failover=False,
        )

    async def _healthy_backends(self) -> list[str]:
        backends_raw = await self._redis.smembers(self._backends_key)
        backends = sorted(item.decode() for item in backends_raw)
        healthy = []
        for backend in backends:
            if await self._health_check(backend):
                healthy.append(backend)

        if not healthy:
            raise NoHealthyBackendsError("No healthy Atlantis backends available")

        return healthy

    async def _round_robin_select(self, backends: list[str]) -> str:
        idx = await self._redis.incr(self._round_robin_key)
        return backends[(idx - 1) % len(backends)]

    def _sticky_redis_key(self, sticky_key: str) -> str:
        return f"{self._sticky_prefix}:{sticky_key}"
