import logging
import time

logger = logging.getLogger("orders.requests")


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        latency_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "%s %s %s %sms user=%s",
            request.method,
            request.path,
            response.status_code,
            latency_ms,
            request.user.pk if getattr(request, "user", None) and request.user.is_authenticated else "anonymous",
        )
        return response
