from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from redis.asyncio import Redis
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.logging_config import configure_logging
from app.services.event_resolver import EventResolver
from app.services.proxy import UpstreamProxy
from app.services.router import BackendRouter, NoHealthyBackendsError

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("atlantis-proxy")

app = FastAPI(title="atlantis-proxy", version="0.1.0")


def _prefixed_path(path: str) -> str:
    if not settings.proxy_path_prefix:
        return path
    return f"{settings.proxy_path_prefix}{path}"


def _path_without_prefix(path: str) -> str:
    if not settings.proxy_path_prefix:
        return path
    prefix = settings.proxy_path_prefix
    if path == prefix:
        return "/"
    if path.startswith(prefix + "/"):
        return path[len(prefix) :]
    return path


HEALTHZ_PATH = _prefixed_path("/healthz")


@app.on_event("startup")
async def on_startup() -> None:
    redis = Redis.from_url(settings.redis_url)
    proxy = UpstreamProxy(timeout_seconds=settings.upstream_timeout_seconds)

    async def health_check(backend_url: str) -> bool:
        return await proxy.is_healthy(
            backend_url=backend_url,
            health_path=settings.backend_health_path,
            timeout_seconds=settings.backend_health_timeout_seconds,
        )

    router = BackendRouter(
        redis=redis,
        backends_key=settings.redis_backends_key,
        sticky_prefix=settings.redis_sticky_prefix,
        round_robin_key=settings.redis_round_robin_key,
        health_check=health_check,
    )

    app.state.redis = redis
    app.state.proxy = proxy
    app.state.router = router
    app.state.resolver = EventResolver()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await app.state.proxy.close()
    await app.state.redis.aclose()


@app.get(HEALTHZ_PATH)
async def healthz() -> JSONResponse:
    try:
        await app.state.redis.ping()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "redis": "down", "error": str(exc)},
        )

    return JSONResponse(content={"status": "ok", "redis": "up"})


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def catch_all(request: Request, path: str) -> Response:
    event_type = "non_sticky"
    sticky_key = None
    normalized_path = _path_without_prefix(request.url.path)

    if request.method.upper() == "POST" and normalized_path == "/events":
        payload = await _json_payload_or_error(request)
        resolution = app.state.resolver.resolve(dict(request.headers), payload)
        event_type = resolution.event_type
        sticky_key = resolution.sticky_key

    try:
        if sticky_key:
            decision = await app.state.router.sticky_backend(sticky_key)
        else:
            decision = await app.state.router.random_backend()
    except NoHealthyBackendsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info(
        '%s %s %s',
        decision.backend_url,
        event_type == "non_sticky" and "-" or event_type,
        sticky_key or "-",
    )

    return await app.state.proxy.forward(
        request=request,
        backend_url=decision.backend_url,
        upstream_path=normalized_path,
    )


async def _json_payload_or_error(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="JSON payload must be an object")

    return parsed
