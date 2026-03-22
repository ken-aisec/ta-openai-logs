"""
anthropic_api_client.py — Shared HTTP client for Anthropic API

Handles:
  - x-api-key authentication + anthropic-version header
  - Proxy configuration (requests-style dict from anthropic_utils.get_proxy_settings)
  - Automatic rate-limit backoff (HTTP 429, Retry-After header)
  - page_token-based pagination (has_more / next_page)
"""

import time
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"

# Maximum back-off wait in seconds for rate-limit responses
MAX_BACKOFF_SECONDS = 120
# Default timeout for HTTP requests (connect, read)
DEFAULT_TIMEOUT = (10, 60)

logger = logging.getLogger(__name__)


class AnthropicAPIError(Exception):
    """Raised for non-retriable Anthropic API errors."""
    def __init__(self, status_code, message):
        self.status_code = status_code
        super().__init__("Anthropic API error {}: {}".format(status_code, message))


class AnthropicClient:
    """
    Lightweight wrapper around requests for Anthropic API calls.

    Usage::

        client = AnthropicClient(api_key="sk-ant-admin-...", proxy_settings={...})
        for page in client.paginate_usage("/organizations/usage_report/messages", params={...}):
            for bucket in page.get("data", []):
                ...
    """

    def __init__(self, api_key, proxy=None):
        self.api_key = api_key
        self.session = self._build_session(proxy)

    # ------------------------------------------------------------------
    # Session / proxy setup
    # ------------------------------------------------------------------

    def _build_session(self, proxy):
        session = requests.Session()

        # Retry on transient network errors only (not 4xx/5xx — we handle those)
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # Explicitly enforce TLS certificate validation
        session.verify = True

        # proxy is a requests-style dict: {"http": uri, "https": uri} or None
        if proxy:
            session.proxies = proxy
            logger.debug("Proxy configured.")

        return session

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def get(self, path, params=None, max_retries=5):
        """
        Perform a GET request with rate-limit back-off.

        Returns the parsed JSON body on success.
        Raises AnthropicAPIError for non-retriable errors.
        """
        url = "{}{}".format(ANTHROPIC_BASE_URL, path)
        attempt = 0

        while attempt <= max_retries:
            attempt += 1
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=DEFAULT_TIMEOUT,
                    verify=True,
                )
            except requests.exceptions.RequestException as exc:
                if attempt > max_retries:
                    raise
                wait = min(2 ** attempt, MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Request error (attempt %d/%d): %s — retrying in %ds",
                    attempt, max_retries, exc, wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                retry_after = int(
                    resp.headers.get("Retry-After", min(2 ** attempt, MAX_BACKOFF_SECONDS))
                )
                wait = min(retry_after, MAX_BACKOFF_SECONDS)
                logger.warning(
                    "Rate limited (attempt %d/%d) — waiting %ds", attempt, max_retries, wait
                )
                time.sleep(wait)
                continue

            # Non-retriable error
            try:
                error_body = resp.json()
                if isinstance(error_body, dict):
                    err = error_body.get("error", {})
                    message = err.get("message", resp.text) if isinstance(err, dict) else resp.text
                else:
                    message = resp.text
            except ValueError:
                message = resp.text
            raise AnthropicAPIError(resp.status_code, message)

        raise AnthropicAPIError(429, "Max retries exceeded due to rate limiting")

    # ------------------------------------------------------------------
    # Pagination helpers
    # ------------------------------------------------------------------

    def paginate_usage(self, path, params=None):
        """
        Generator for page_token-based pagination (usage report endpoints).

        Yields each page dict. Stops when `has_more` is False or missing.
        The next page token is taken from the `next_page` field in each response,
        and passed as `page_token` in the next request.
        """
        params = dict(params or {})
        while True:
            page = self.get(path, params=params)
            yield page

            if not page.get("has_more"):
                break

            next_page = page.get("next_page")
            if not next_page:
                break

            # Remove any one-time params that shouldn't repeat
            params.pop("starting_at", None)
            params.pop("ending_at", None)
            params["page_token"] = next_page
