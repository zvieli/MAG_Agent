import unittest
from unittest.mock import Mock
from function_app import _get_cors_headers_for_req, CORS_HEADERS, _handle_options_request


class TestCorsPreflight(unittest.TestCase):
    def test_cors_headers_wildcard_by_default(self):
        headers = _get_cors_headers_for_req(None)
        self.assertEqual(headers["Access-Control-Allow-Headers"], "*")

    def test_cors_headers_echoes_request_headers(self):
        req = Mock()
        req.headers = {
            "Origin": "https://tikshuv.sharepoint.com",
            "Access-Control-Request-Headers": "x-mag-trace-id, pragma, cache-control, sec-fetch-dest"
        }
        headers = _get_cors_headers_for_req(req)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://tikshuv.sharepoint.com")
        self.assertEqual(headers["Access-Control-Allow-Headers"], "x-mag-trace-id, pragma, cache-control, sec-fetch-dest")

    def test_options_request_handler(self):
        req = Mock()
        req.headers = {
            "origin": "https://tikshuv.sharepoint.com",
            "access-control-request-headers": "authorization, content-type, x-functions-key"
        }
        res = _handle_options_request(req)
        self.assertEqual(res.status_code, 204)
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "https://tikshuv.sharepoint.com")
        self.assertEqual(res.headers.get("Access-Control-Allow-Headers"), "authorization, content-type, x-functions-key")


if __name__ == "__main__":
    unittest.main()
