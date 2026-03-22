"""
Unit tests for anthropic_api_client.py
Run: python3 -m pytest tests/ -v
"""

import sys
import os
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "package", "bin"))

from anthropic_api_client import AnthropicClient, AnthropicAPIError


def _make_response(status_code, body, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.headers = headers or {}
    return resp


class TestAnthropicClientGet(unittest.TestCase):

    def setUp(self):
        self.client = AnthropicClient(api_key="sk-ant-admin-test")

    @patch("anthropic_api_client.requests.Session.get")
    def test_successful_get(self, mock_get):
        body = {"data": [{"start_time": "2026-03-22T00:00:00Z", "results": []}], "has_more": False}
        mock_get.return_value = _make_response(200, body)
        self.assertEqual(self.client.get("/organizations/usage_report/messages"), body)

    @patch("anthropic_api_client.requests.Session.get")
    def test_auth_headers_sent(self, mock_get):
        mock_get.return_value = _make_response(200, {})
        self.client.get("/test")
        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        self.assertEqual(headers["x-api-key"], "sk-ant-admin-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(headers["content-type"], "application/json")

    @patch("anthropic_api_client.time.sleep", return_value=None)
    @patch("anthropic_api_client.requests.Session.get")
    def test_rate_limit_retry_then_success(self, mock_get, mock_sleep):
        body = {"data": []}
        mock_get.side_effect = [
            _make_response(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}),
            _make_response(429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}),
            _make_response(200, body),
        ]
        self.assertEqual(self.client.get("/test"), body)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("anthropic_api_client.time.sleep", return_value=None)
    @patch("anthropic_api_client.requests.Session.get")
    def test_rate_limit_exhausts_retries(self, mock_get, mock_sleep):
        mock_get.return_value = _make_response(
            429, {"error": {"message": "rate limited"}}, headers={"Retry-After": "1"}
        )
        with self.assertRaises(AnthropicAPIError) as ctx:
            self.client.get("/test", max_retries=3)
        self.assertEqual(ctx.exception.status_code, 429)

    @patch("anthropic_api_client.requests.Session.get")
    def test_non_retriable_error_raises(self, mock_get):
        mock_get.return_value = _make_response(401, {"error": {"message": "invalid api key"}})
        with self.assertRaises(AnthropicAPIError) as ctx:
            self.client.get("/test")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("invalid api key", str(ctx.exception))

    @patch("anthropic_api_client.requests.Session.get")
    def test_403_raises_immediately(self, mock_get):
        mock_get.return_value = _make_response(403, {"error": {"message": "permission denied"}})
        with self.assertRaises(AnthropicAPIError) as ctx:
            self.client.get("/organizations/usage_report/messages")
        self.assertEqual(ctx.exception.status_code, 403)

    @patch("anthropic_api_client.requests.Session.get")
    def test_404_raises_immediately(self, mock_get):
        mock_get.return_value = _make_response(404, {"error": {"message": "not found"}})
        with self.assertRaises(AnthropicAPIError) as ctx:
            self.client.get("/missing")
        self.assertEqual(ctx.exception.status_code, 404)


class TestPaginateUsage(unittest.TestCase):

    def setUp(self):
        self.client = AnthropicClient(api_key="sk-ant-admin-test")

    @patch("anthropic_api_client.requests.Session.get")
    def test_single_page(self, mock_get):
        page = {"data": [{"start_time": "2026-03-22T00:00:00Z"}], "has_more": False}
        mock_get.return_value = _make_response(200, page)
        pages = list(self.client.paginate_usage("/organizations/usage_report/messages"))
        self.assertEqual(len(pages), 1)

    @patch("anthropic_api_client.requests.Session.get")
    def test_multi_page_token_advancement(self, mock_get):
        page1 = {"data": [{"start_time": "2026-03-22T01:00:00Z"}], "has_more": True, "next_page": "page_token_abc"}
        page2 = {"data": [{"start_time": "2026-03-22T00:00:00Z"}], "has_more": False}
        mock_get.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        pages = list(self.client.paginate_usage("/organizations/usage_report/messages"))
        self.assertEqual(len(pages), 2)
        second_call_params = mock_get.call_args_list[1][1].get("params", {})
        self.assertEqual(second_call_params.get("page_token"), "page_token_abc")

    @patch("anthropic_api_client.requests.Session.get")
    def test_stops_when_no_next_page(self, mock_get):
        page = {"data": [], "has_more": True}  # has_more but no next_page token
        mock_get.return_value = _make_response(200, page)
        pages = list(self.client.paginate_usage("/test"))
        self.assertEqual(len(pages), 1)

    @patch("anthropic_api_client.requests.Session.get")
    def test_date_params_not_repeated_on_pagination(self, mock_get):
        """starting_at/ending_at should be dropped from subsequent page requests."""
        page1 = {"data": [], "has_more": True, "next_page": "tok_xyz"}
        page2 = {"data": [], "has_more": False}
        mock_get.side_effect = [_make_response(200, page1), _make_response(200, page2)]
        params = {"starting_at": "2026-03-22T00:00:00Z", "ending_at": "2026-03-23T00:00:00Z"}
        list(self.client.paginate_usage("/test", params=params))
        second_call_params = mock_get.call_args_list[1][1].get("params", {})
        self.assertNotIn("starting_at", second_call_params)
        self.assertNotIn("ending_at", second_call_params)
        self.assertEqual(second_call_params.get("page_token"), "tok_xyz")


class TestProxyConfig(unittest.TestCase):

    def test_proxy_configured_when_provided(self):
        proxy = {"http": "http://proxy.example.com:8080", "https": "http://proxy.example.com:8080"}
        client = AnthropicClient(api_key="sk-ant-admin-test", proxy=proxy)
        self.assertEqual(client.session.proxies, proxy)

    def test_proxy_not_configured_when_none(self):
        client = AnthropicClient(api_key="sk-ant-admin-test", proxy=None)
        self.assertEqual(client.session.proxies, {})


if __name__ == "__main__":
    unittest.main()
