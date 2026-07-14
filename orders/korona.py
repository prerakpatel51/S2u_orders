import logging
import time
from urllib.parse import urljoin

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import ApiRequestLog

logger = logging.getLogger(__name__)


class KoronaError(RuntimeError):
    pass


class KoronaClient:
    def __init__(self):
        if not all(
            [settings.KORONA_BASE_URL, settings.KORONA_ACCOUNT_ID, settings.KORONA_USER, settings.KORONA_PASSWORD]
        ):
            raise KoronaError("KORONA credentials are not configured")
        self.account_id = settings.KORONA_ACCOUNT_ID
        self.base_url = settings.KORONA_BASE_URL.rstrip("/") + "/"
        self.session = requests.Session()
        self.session.auth = (settings.KORONA_USER, settings.KORONA_PASSWORD)
        self.session.headers.update({"Accept": "application/json", "User-Agent": "store-orders/1.0"})
        retry = Retry(
            total=settings.KORONA_HTTP_RETRIES,
            connect=settings.KORONA_HTTP_RETRIES,
            read=settings.KORONA_HTTP_RETRIES,
            status=settings.KORONA_HTTP_RETRIES,
            backoff_factor=settings.KORONA_HTTP_BACKOFF_SECONDS,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def account_path(self, suffix):
        return f"accounts/{self.account_id}/{suffix.lstrip('/')}"

    def request(self, method, path, **kwargs):
        url = urljoin(self.base_url, path.lstrip("/"))
        logged_path = ("/" + path.lstrip("/")).replace(self.account_id, "{account}")
        started = time.monotonic()
        response = None
        try:
            response = self.session.request(
                method,
                url,
                timeout=(settings.KORONA_CONNECT_TIMEOUT_SECONDS, settings.KORONA_READ_TIMEOUT_SECONDS),
                **kwargs,
            )
            latency = round((time.monotonic() - started) * 1000)
            ApiRequestLog.objects.create(
                method=method.upper(),
                url_path=logged_path,
                status_code=response.status_code,
                latency_ms=latency,
            )
            if response.status_code == 204:
                return None
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            latency = round((time.monotonic() - started) * 1000)
            if response is None:
                ApiRequestLog.objects.create(
                    method=method.upper(), url_path=logged_path, latency_ms=latency
                )
            logger.exception("KORONA request failed: %s %s", method, path)
            raise KoronaError(str(exc)) from exc

    def paginated(self, suffix, params=None, page_size=100):
        params = {**(params or {}), "size": page_size, "page": 1}
        max_revision = None
        while True:
            payload = self.request("GET", self.account_path(suffix), params=params)
            if not payload:
                break
            max_revision = payload.get("maxRevision", max_revision)
            yield payload.get("results", []), max_revision
            next_url = (payload.get("links") or {}).get("next")
            if not next_url:
                break
            params["page"] += 1

    def product_stocks(self, product_id):
        rows = []
        for page, _ in self.paginated(f"products/{product_id}/stocks"):
            rows.extend(page)
        return rows

    def organizational_unit_product_stocks(self, organizational_unit_id, revision=None, page_size=None):
        params = {}
        if revision is not None:
            params["revision"] = revision
        return self.paginated(
            f"organizationalUnits/{organizational_unit_id}/productStocks",
            params=params,
            page_size=page_size or settings.KORONA_STOCK_PAGE_SIZE,
        )
