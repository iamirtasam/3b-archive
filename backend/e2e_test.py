"""End-to-end test of the deployed API: health, login, videos, stream-auth.

Usage:
    python backend/e2e_test.py <username> <password>

Verifies the full chain: login sets cookies, /api/videos returns the catalog,
and /api/stream-auth re-issues CloudFront cookies. Finally fetches a real
playlist through CloudFront using the stream-auth cookies to prove playback.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from lib.common import load_config  # noqa: E402

CONFIG = load_config(Path(__file__).resolve().parent.parent / "config.json")


def endpoint() -> str:
    state = Path(__file__).resolve().parent / "api_endpoint.json"
    return json.loads(state.read_text())["endpoint"]


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    username, password = sys.argv[1], sys.argv[2]
    base = endpoint()
    print(f"API: {base}")

    status, _, _ = _req("GET", f"{base}api/health")
    print(f"health      -> {status}")
    assert status == 200

    status, body, headers = _req(
        "POST", f"{base}api/login", {"username": username, "password": password}
    )
    print(f"login       -> {status}")
    assert status == 200, body

    status, body, _ = _req("POST", f"{base}api/login", {"username": username, "password": "wrong"})
    print(f"bad login   -> {status} (expect 401)")
    assert status == 401

    status, body, _ = _req("GET", f"{base}api/videos", cookies=headers["set-cookie"])
    videos = json.loads(body)
    print(f"videos      -> {status}, {len(videos['videos'])} item(s)")
    assert status == 200
    if videos["videos"]:
        print(f"  first: {videos['videos'][0]['title']} ({videos['videos'][0]['folder']})")

    status, body, stream_headers = _req(
        "GET", f"{base}api/stream-auth", cookies=headers["set-cookie"]
    )
    print(f"stream-auth -> {status}")
    assert status == 200

    cookies = headers["set-cookie-list"] + stream_headers["set-cookie-list"]
    if videos["videos"]:
        slug = videos["videos"][0]["slug"]
        url = f"https://{CONFIG['cloudfront']['domain']}/videos/{slug}/index.m3u8"
        status, body, _ = _req("GET", url, cookies=cookies)
        print(f"playlist    -> {status} via CloudFront with cookies")
        assert status == 200
        assert b"#EXTM3U" in body
        print("PLAYBACK CHAIN VERIFIED (signed cookies -> playlist 200)")

    print("\nAll e2e checks passed.")


def _req(method: str, url: str, payload: dict | None = None, cookies=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("content-type", "application/json")
    if cookies:
        pairs = [
            value.split(";")[0].strip()  # drop cookie attributes
            for value in (cookies if isinstance(cookies, list) else [cookies])
        ]
        request.add_header("cookie", "; ".join(pairs))
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return (resp.status, resp.read(),
                    _headers_with_all_cookies(resp.headers))
    except urllib.error.HTTPError as err:
        return (err.code, err.read(),
                _headers_with_all_cookies(err.headers))


def _headers_with_all_cookies(message) -> dict:
    """dict() collapses repeated Set-Cookie headers; keep every one of them."""
    headers = dict(message)
    headers["set-cookie-list"] = message.get_all("Set-Cookie") or []
    return headers


if __name__ == "__main__":
    main()
