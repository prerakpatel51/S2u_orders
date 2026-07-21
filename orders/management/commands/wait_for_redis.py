import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from redis import Redis
from redis.exceptions import RedisError


class Command(BaseCommand):
    help = "Wait for the Celery Redis broker, then fail within a bounded time."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=int, default=60)

    def handle(self, *args, **options):
        timeout = max(1, options["timeout"])
        deadline = time.monotonic() + timeout
        client = Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=min(5, timeout),
            socket_timeout=min(5, timeout),
        )
        while True:
            try:
                client.ping()
                self.stdout.write(self.style.SUCCESS("Redis broker is ready."))
                return
            except RedisError as exc:
                if time.monotonic() >= deadline:
                    raise CommandError(
                        f"Redis broker did not become ready within {timeout} seconds: {exc}"
                    ) from exc
                time.sleep(2)
