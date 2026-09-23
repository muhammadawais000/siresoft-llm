"""SearXNG-backed web search fallback tests. The HTTP boundary
(requests.get) is mocked -- this suite is about web_search()'s own
contract (disabled flag, result shaping, graceful failure on a down/
misbehaving SearXNG instance), not about SearXNG's own behavior.
"""

from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from rag.web_search import WebResult, web_search


@override_settings(WEB_SEARCH_ENABLED=True, WEB_SEARCH_MAX_RESULTS=5, SEARXNG_URL="http://searxng:8080")
class WebSearchTests(SimpleTestCase):
    @patch("rag.web_search.requests.get")
    def test_disabled_returns_empty_list_without_a_request(self, mock_get):
        with override_settings(WEB_SEARCH_ENABLED=False):
            self.assertEqual(web_search("anything"), [])
        mock_get.assert_not_called()

    @patch("rag.web_search.requests.get")
    def test_parses_searxng_json_results(self, mock_get):
        mock_get.return_value = MagicMock(
            json=lambda: {
                "results": [
                    {"title": "A", "url": "https://a.example", "content": "snippet a"},
                    {"title": "B", "url": "https://b.example", "content": "snippet b"},
                ]
            }
        )
        mock_get.return_value.raise_for_status = lambda: None

        results = web_search("some query")

        self.assertEqual(
            results,
            [
                WebResult(title="A", url="https://a.example", snippet="snippet a"),
                WebResult(title="B", url="https://b.example", snippet="snippet b"),
            ],
        )

    @patch("rag.web_search.requests.get")
    def test_results_without_a_url_are_dropped(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"results": [{"title": "No URL", "content": "x"}]})
        mock_get.return_value.raise_for_status = lambda: None

        self.assertEqual(web_search("q"), [])

    @patch("rag.web_search.requests.get")
    def test_max_results_truncates_the_result_list(self, mock_get):
        mock_get.return_value = MagicMock(
            json=lambda: {
                "results": [{"title": str(i), "url": f"https://x.example/{i}", "content": ""} for i in range(10)]
            }
        )
        mock_get.return_value.raise_for_status = lambda: None

        self.assertEqual(len(web_search("q", max_results=3)), 3)

    @patch("rag.web_search.requests.get", side_effect=requests.ConnectionError)
    def test_connection_failure_returns_empty_list(self, mock_get):
        self.assertEqual(web_search("q"), [])

    @patch("rag.web_search.requests.get")
    def test_invalid_json_response_returns_empty_list(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: (_ for _ in ()).throw(ValueError("not json")))
        mock_get.return_value.raise_for_status = lambda: None

        self.assertEqual(web_search("q"), [])

    @patch("rag.web_search.requests.get")
    def test_query_and_json_format_are_sent_to_searxng(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"results": []})
        mock_get.return_value.raise_for_status = lambda: None

        web_search("capital of France")

        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], "http://searxng:8080/search")
        self.assertEqual(kwargs["params"], {"q": "capital of France", "format": "json"})
