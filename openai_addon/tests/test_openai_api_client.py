"""
Unit tests for openai_api_client.py
Run: python3 -m pytest tests/ -v
"""

import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "package", "bin"))

from openai_api_client import OpenAIClient, OpenAIAPIError


def _make_response(status_code, body, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.headers = headers or {}
    return resp


class TestOpenAIClientGet(unittest.TestCase):

    def setUp(self):
        self.client = OpenAIClient(api_key="sk-test", org_id="org-123")

    @patch("openai_api_client.requests.Session.get")
    def test_successful_get(self, mock_get):
        body = {"data": [{"id": "evt_1"}], "has_more": False}
        mock_get.return_value = _make_response(200, body)
        self.assertEqual(self.client.get("/organization/audit_logs"), body)

    @patch("openai_api_client.requests.Session.get")
    def test_auth_header_sent(self, mock_get):
        mock_get.return_value = _make_response(200, {})
        self.client.get("/test")
        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["OpenAI-Organization"], "org-123")

    @patch("openai_api_client.requests.Session.get")
    def test_no_org_header_when_absent(self, mock_get):
        client = OpenAIClient(api_key="sk-test")
        mock_get.return_value = _make_response(200, {})
        client.get("/test")
        _, kwargs = mock_get.call_args
        self.assertNotIn("OpenAI-Organization", kwargs.get("headers", {}))

    @patch("openai_api_client.time.sleep", return_value=None)
    @patch("openai_api_client.requests.Session.get")
    def test_rate_limit_retry_then_success(self, mock_get, mock_sleep):
        body = {"data": []}
        mock_get.side_effect = [
            _make_response(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}),
            _make_response(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}),
            _make_response(200, body),
        ]
        self.assertEqual(self.client.get("/test"), body)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("openai_api_client.time.sleep", return_value=None)
    @patch("openai_api_client.requests.Session.get")
    def test_rate_limit_exhausts_retries(self, mock_get, mock_sleep):
        mock_get.return_value = _make_response(
            429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}
        )
        with self.assertRaises(OpenAIAPIError) as ctx:
            self.client.get("/test", max_retries=3)
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("openai_api_client.requests.Session.get")
    def test_non_retriable_error_raises(self, mock_get):
        mock_get.return_value = _make_response(401, {"error": {"message": "invalid api key"}})
        with self.assertRaises(OpenAIAPIError) as ctx:
            self.client.get("/test")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("invalid api key", str(ctx.exception))

    @patch("openai_api_client.requests.Session.get")
    def test_404_raises_immediately(self, mock_get):
        mock_get.return_value = _make_response(404, {"error": {"message": "not found"}})
        with self.assertRaises(OpenAIAPIError) as ctx:
            self.client.get("/missing")
        self.assertEqual(ctx.exception.status_code, 404)


class TestPaginateCursor(unittest.TestCase):

    def setUp(self):
        self.client = OpenAIClient(api_key="sk-test")

    @patch("openai_api_client.requests.Session.get")
    def test_single_page(self, mock_get):
        page = {"data": [{"id": "evt_1"}, {"id": "evt_2"}], "has_more": False}
        mock_get.return_value = _make_response(200, page)
        pages = list(self.client.paginate_cursor("/organization/audit_logs"))
        self.assertEqual(len(pages), 1)

    @patch("openai_api_client.requests.Session.get")
    def test_multi_page_cursor_advancement(self, mock_get):
        page1 = {"data": [{"id": "evt_3"}, {"id": "evt_2"}], "has_more": True}
        page2 = {"data": [{"id": "evt_1"}], "has_more": False}
        mock_get.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        pages = list(self.client.paginate_cursor("/organization/audit_logs"))
        self.assertEqual(len(pages), 2)
        second_call_params = mock_get.call_args_list[1][1].get("params", {})
        self.assertEqual(second_call_params.get("after"), "evt_2")

    @patch("openai_api_client.requests.Session.get")
    def test_empty_page_stops(self, mock_get):
        page = {"data": [], "has_more": True}
        mock_get.return_value = _make_response(200, page)
        pages = list(self.client.paginate_cursor("/test"))
        self.assertEqual(len(pages), 1)


class TestProxyConfig(unittest.TestCase):

    def test_proxy_configured_when_provided(self):
        proxy = {"http": "http://proxy.example.com:8080", "https": "http://proxy.example.com:8080"}
        client = OpenAIClient(api_key="sk-test", proxy=proxy)
        self.assertEqual(client.session.proxies, proxy)

    def test_proxy_not_configured_when_none(self):
        client = OpenAIClient(api_key="sk-test", proxy=None)
        self.assertEqual(client.session.proxies, {})


if __name__ == "__main__":
    unittest.main()
