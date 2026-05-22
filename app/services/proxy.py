from __future__ import annotations

from typing import Iterable

import httpx
from starlette.requests import Request
from starlette.responses import Response


HOP_BY_HOP_REQUEST_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

HOP_BY_HOP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class UpstreamProxy:
    def __init__(self, timeout_seconds: float) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)

    async def close(self) -> None:
        await self._client.aclose()

    async def is_healthy(self, backend_url: str, health_path: str, timeout_seconds: float) -> bool:
        health_url = f"{backend_url.rstrip('/')}{health_path}"
        try:
            response = await self._client.get(health_url, timeout=timeout_seconds)
            return response.status_code < 500
        except (httpx.HTTPError, httpx.TimeoutException):
            return False

    async def forward(self, request: Request, backend_url: str, upstream_path: str | None = None) -> Response:
        query = request.url.query
        raw_path = upstream_path if upstream_path is not None else request.url.path
        target_url = f"{backend_url.rstrip('/')}/{raw_path.lstrip('/')}"
        if query:
            target_url = f"{target_url}?{query}"

        headers = self._filter_headers(request.headers.items(), HOP_BY_HOP_REQUEST_HEADERS)
        body = await request.body()

        upstream = await self._client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )

        response_headers = self._filter_headers(upstream.headers.items(), HOP_BY_HOP_RESPONSE_HEADERS)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=dict(response_headers),
            media_type=upstream.headers.get("content-type"),
        )

    def _filter_headers(
        self,
        headers: Iterable[tuple[str, str]],
        blocked: set[str],
    ) -> list[tuple[str, str]]:
        return [(k, v) for k, v in headers if k.lower() not in blocked]
