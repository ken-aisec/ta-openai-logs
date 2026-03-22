"""
openai_api_client.py — Shared HTTP client for OpenAI API

Handles:
  - Authorization headers (Bearer token)
  - Organization-ID header (optional)
  - Proxy configuration (requests-style dict from openai_utils.get_proxy_settings)
  - Automatic rate-limit backoff (HTTP 429, Retry-After header)
  - Cursor-based pagination (has_more / after)
"""

import json
import time
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

OPENAI_BASE_URL = "https://api.openai.com/v1"

# Maximum back-off wait in seconds for rate-limit responses
MAX_BACKOFF_SECONDS = 120
# Default timeout for HTTP requests (connect, read)
DEFAULT_TIMEOUT = (10, 60)

logger = logging.getLogger(__name__)


class OpenAIAPIError(Exception):
    """Raised for non-retriable OpenAI API errors."""
    def __init__(self, status_code, message):
        self.status_code = status_code
        super().__init__(f"OpenAI API error {status_code}: {message}")


class OpenAIClient:
    """
    Lightweight wrapper around requests for OpenAI API calls.

    Usage::

        client = OpenAIClient(api_key="sk-...", org_id="org-...", proxy_settings={...})
        for page in client.paginate("/organization/usage/completions", params={...}):
            for bucket in page.get("data", []):
                ...
    """

    def __init__(self, api_key, org_id=None, proxy=None):
        self.api_key = api_key
        self.org_id = org_id
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
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.org_id:
            headers["OpenAI-Organization"] = self.org_id
        return headers

    def get(self, path, params=None, max_retries=5):
        """
        Perform a GET request with rate-limit back-off.

        Returns the parsed JSON body on success.
        Raises OpenAIAPIError for non-retriable errors.
        """
        url = f"{OPENAI_BASE_URL}{path}"
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
                logger.warning("Request error (attempt %d/%d): %s — retrying in %ds",
                               attempt, max_retries, exc, wait)
                time.sleep(wait)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", min(2 ** attempt, MAX_BACKOFF_SECONDS)))
                wait = min(retry_after, MAX_BACKOFF_SECONDS)
                logger.warning("Rate limited (attempt %d/%d) — waiting %ds", attempt, max_retries, wait)
                time.sleep(wait)
                continue

            # Non-retriable error
            try:
                error_body = resp.json()
                if isinstance(error_body, dict):
                    message = error_body.get("error", {}).get("message", resp.text)
                else:
                    message = resp.text
            except ValueError:
                message = resp.text
            raise OpenAIAPIError(resp.status_code, message)

        raise OpenAIAPIError(429, "Max retries exceeded due to rate limiting")

    # ------------------------------------------------------------------
    # Pagination helpers
    # ------------------------------------------------------------------

    def paginate_cursor(self, path, params=None):
        """
        Generator for cursor-based pagination (audit logs).

        Yields each page dict. Stops when `has_more` is False or missing.
        The `after` cursor is taken from the last item's `id` field.
        """
        params = dict(params or {})
        while True:
            page = self.get(path, params=params)
            yield page

            if not page.get("has_more"):
                break

            items = page.get("data", [])
            if not items:
                break

            # Advance cursor to ID of last item on this page
            params["after"] = items[-1]["id"]

    def paginate_usage(self, path, params=None):
        """
        Generator for the usage endpoint.

        The usage endpoint returns a flat list; callers iterate over
        the returned buckets directly. This wrapper handles HTTP + backoff
        and returns the full response body.
        """
        return self.get(path, params=params)
