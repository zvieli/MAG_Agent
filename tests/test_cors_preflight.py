import unittest
from unittest.mock import Mock, patch
import azure.functions as func
from function_app import (
    _get_cors_headers_for_req,
    _handle_options_request,
    _resolve_upload_filename,
    ping,
)


class TestCorsPreflight(unittest.TestCase):
    def test_cors_headers_default(self):
        headers = _get_cors_headers_for_req(None)
        self.assertEqual(
            headers["Access-Control-Allow-Headers"],
            "Authorization, Content-Type, Content-Disposition, x-functions-key, x-mag-file-name, *",
        )

    def test_upload_filename_uses_matching_redundant_metadata(self):
        req = Mock()
        req.params = {"filename": "sample_page_1.pdf"}
        req.headers = {
            "x-mag-file-name": "sample_page_1.pdf",
            "Content-Disposition": 'attachment; filename="sample_page_1.pdf"',
        }

        self.assertEqual(_resolve_upload_filename(req), "sample_page_1.pdf")

    def test_upload_filename_supports_encoded_unicode(self):
        req = Mock()
        req.params = {"filename": "%D7%AA%D7%99%D7%A7%20%D7%90.pdf"}
        req.headers = {}

        self.assertEqual(_resolve_upload_filename(req), "תיק א.pdf")

    def test_upload_filename_rejects_missing_or_conflicting_metadata(self):
        missing = Mock()
        missing.params = {}
        missing.headers = {}
        with self.assertRaisesRegex(ValueError, "Missing PDF filename"):
            _resolve_upload_filename(missing)

        conflicting = Mock()
        conflicting.params = {"filename": "first.pdf"}
        conflicting.headers = {"x-mag-file-name": "second.pdf"}
        with self.assertRaisesRegex(ValueError, "Conflicting PDF filename metadata"):
            _resolve_upload_filename(conflicting)

    def test_cors_headers_echoes_request_headers_includes_authorization(self):
        req = Mock()
        req.headers = {
            "Origin": "https://tikshuv.sharepoint.com",
            "Access-Control-Request-Headers": "x-mag-trace-id, pragma, cache-control, sec-fetch-dest",
        }
        headers = _get_cors_headers_for_req(req)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "https://tikshuv.sharepoint.com")
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])
        self.assertIn("x-mag-trace-id", headers["Access-Control-Allow-Headers"])

    def test_options_request_handler(self):
        req = Mock()
        req.headers = {
            "origin": "https://tikshuv.sharepoint.com",
            "access-control-request-headers": "authorization, content-type, x-functions-key",
        }
        res = _handle_options_request(req)
        self.assertEqual(res.status_code, 204)
        self.assertEqual(res.headers.get("Access-Control-Allow-Origin"), "https://tikshuv.sharepoint.com")
        self.assertIn("authorization", res.headers.get("Access-Control-Allow-Headers").lower())

    @patch("src.auth.decorators.validate_bearer_token", return_value=None)
    def test_endpoint_requires_token_auth_unauthorized(self, mock_validate):
        req = func.HttpRequest(
            method="GET",
            url="https://func.azurewebsites.net/api/ping",
            headers={},
            body=b"",
        )
        res = ping(req)
        self.assertEqual(res.status_code, 401)
        self.assertIn("Access-Control-Allow-Origin", res.headers)
        self.assertIn("Authorization", res.headers["Access-Control-Allow-Headers"])

    @patch("src.auth.decorators.validate_bearer_token", return_value={"sub": "user1"})
    def test_endpoint_requires_token_auth_authorized(self, mock_validate):
        req = func.HttpRequest(
            method="GET",
            url="https://func.azurewebsites.net/api/ping",
            headers={"Authorization": "Bearer valid_token"},
            body=b"",
        )
        res = ping(req)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_body().decode("utf-8"), "Healthy")


if __name__ == "__main__":
    unittest.main()
