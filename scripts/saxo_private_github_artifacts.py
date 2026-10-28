"""Safe GitHub Actions artifact fetcher for private EURUSD runtime.

GitHub's artifact download API redirects to an independent signed storage URL.
urllib's stock redirect handler copies custom Authorization headers into
cross-host redirects, which causes 401 from the signed artifact store.
Only send the GitHub token to api.github.com, never to redirected storage.
"""
from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit


class StripCrossOriginAuthorization(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        original = urlsplit(req.full_url)
        target = urlsplit(redirected.full_url)
        if target.scheme != "https" or not target.hostname:
            raise ValueError("artifact_redirect_must_be_https")
        if (target.hostname, target.port) != (original.hostname, original.port):
            redirected.remove_header("Authorization")
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


def read_github_api(url: str, token: str, *, max_bytes: int = 50_000_000) -> bytes:
    target = urlsplit(url)
    if target.scheme != "https" or target.hostname != "api.github.com" or not token:
        raise ValueError("invalid_github_api_read")
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "BriefRoomsPrivateSaxoDaily/1",
    })
    opener = urllib.request.build_opener(StripCrossOriginAuthorization())
    with opener.open(req, timeout=20) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("github_artifact_limit_exceeded")
    return data
