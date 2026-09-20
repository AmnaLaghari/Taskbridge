import redis
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return "down"
    return "ok"


def _check_redis() -> str:
    try:
        redis.Redis.from_url(settings.REDIS_URL, socket_timeout=1).ping()
    except redis.RedisError:
        return "down"
    return "ok"


def health(request: HttpRequest) -> JsonResponse:
    checks = {"database": _check_database(), "redis": _check_redis()}
    healthy = all(state == "ok" for state in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", **checks},
        status=200 if healthy else 503,
    )
