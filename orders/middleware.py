import logging
import time

from django.db import DatabaseError

from .models import ApiRequestLog

logger = logging.getLogger("orders.requests")

METRICS_EXCLUDED_PATHS = {
    "/live",
    "/ready",
    "/metrics",
    "/api/health/",
    "/api/health/runtime/",
}


def normalized_route(request):
    """Return the URL pattern, avoiding IDs and other high-cardinality labels."""
    match = getattr(request, "resolver_match", None)
    return (match.route if match and match.route else request.path)[:1000]


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        try:
            response = self.get_response(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            latency_ms = round((time.monotonic() - started) * 1000)
            if request.path not in METRICS_EXCLUDED_PATHS and not request.path.startswith("/static/"):
                try:
                    ApiRequestLog.objects.create(
                        service="django",
                        method=request.method,
                        url_path=normalized_route(request),
                        status_code=status_code,
                        latency_ms=latency_ms,
                    )
                except DatabaseError:
                    logger.warning("Could not persist request metric", exc_info=True)
        logger.info(
            "%s %s %s %sms user=%s",
            request.method,
            request.path,
            response.status_code,
            latency_ms,
            request.user.pk if getattr(request, "user", None) and request.user.is_authenticated else "anonymous",
        )
        return response
