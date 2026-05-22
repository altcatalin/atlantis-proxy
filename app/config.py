from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    redis_url: str
    redis_backends_key: str
    redis_sticky_prefix: str
    redis_round_robin_key: str
    backend_health_path: str
    backend_health_timeout_seconds: float
    upstream_timeout_seconds: float
    log_level: str
    proxy_path_prefix: str



def get_settings() -> Settings:
    return Settings(
        redis_url=os.getenv("REDIS_URL", "redis://redis:6379/0"),
        redis_backends_key=os.getenv("REDIS_BACKENDS_KEY", "atlantis:backends"),
        redis_sticky_prefix=os.getenv("REDIS_STICKY_PREFIX", "atlantis:sticky"),
        redis_round_robin_key=os.getenv("REDIS_ROUND_ROBIN_KEY", "atlantis:rr:index"),
        backend_health_path=os.getenv("BACKEND_HEALTH_PATH", "/healthz"),
        backend_health_timeout_seconds=float(os.getenv("BACKEND_HEALTH_TIMEOUT_SECONDS", "2.0")),
        upstream_timeout_seconds=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "20.0")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        proxy_path_prefix=normalize_path_prefix(os.getenv("PROXY_PATH_PREFIX", "/")),
    )


def normalize_path_prefix(raw_prefix: str) -> str:
    prefix = raw_prefix.strip()
    if not prefix or prefix == "/":
        return ""
    prefix = "/" + prefix.strip("/")
    return prefix
