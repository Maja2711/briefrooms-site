"""Test private EURUSD GitHub artifact redirect auth isolation offline."""
import unittest
import urllib.request
from unittest.mock import patch

from saxo_private_github_artifacts import (
    StripCrossOriginAuthorization, read_github_api,
)


class PrivateArtifactRedirectTests(unittest.TestCase):
    def setUp(self):
        self.handler = StripCrossOriginAuthorization()
        self.request = urllib.request.Request(
            "https://api.github.com/repos/Maja2711/briefrooms-site/actions/artifacts/12/zip",
            headers={"Authorization": "Bearer test-only", "Accept": "application/vnd.github+json"},
        )

    def test_drop_bearer_on_signed_artifact_redirect(self):
        redirected = self.handler.redirect_request(
            self.request, None, 302, "Found", {},
            "https://example.blob.core.windows.net/asset?sig=synthetic",
        )
        self.assertEqual(redirected.full_url.split("?")[0], "https://example.blob.core.windows.net/asset")
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertIn("Accept", redirected.headers)
        self.assertEqual(self.request.get_header("Authorization"), "Bearer test-only")

    def test_keep_auth_for_same_github_hostname(self):
        redirected = self.handler.redirect_request(
            self.request, None, 302, "Found", {},
            "https://api.github.com/repos/Maja2711/briefrooms-site/actions/artifacts/12",
        )
        self.assertEqual(redirected.get_header("Authorization"), "Bearer test-only")

    def test_never_follow_non_https_signed_redirect(self):
        with self.assertRaises(ValueError):
            self.handler.redirect_request(
                self.request, None, 302, "Found", {}, "http://external.invalid/blob"
            )

    def test_read_rejects_non_api_github_destination(self):
        with self.assertRaises(ValueError):
            read_github_api("https://example.com/anything", "unused")

    def test_read_rejects_missing_token(self):
        with self.assertRaises(ValueError):
            read_github_api("https://api.github.com/repos/example/repo", "")


if __name__ == "__main__":
    unittest.main()
