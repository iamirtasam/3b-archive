"""API Lambda for s3-app-streaming.

Routes (HTTP API, payload format v2):
    GET  /api/health      - public liveness check
    POST /api/login       - verify credentials, create session, set cookies
    POST /api/logout      - destroy session, clear cookies
    GET  /api/videos      - session required; returns the video catalog
    GET  /api/stream-auth - session required; re-issues CloudFront signed cookies

After a successful login the response carries:
    svg_session            - this API's session token (HttpOnly)
    CloudFront-Policy      - custom policy for https://DOMAIN/videos/*
    CloudFront-Signature   - RSA-SHA1 signature of that policy
    CloudFront-Key-Pair-Id - the CloudFront public key id

Because the website, the API, and the videos are all served from one
CloudFront domain, these cookies flow to every request automatically -
which is what makes HLS playback work without per-URL signatures.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "svg_")
DOMAIN = os.environ["DOMAIN"]
KEY_PAIR_ID = os.environ["KEY_PAIR_ID"]
SSM_PARAM = os.environ["SSM_PARAM"]

USERS_TABLE = f"{TABLE_PREFIX}users"
SESSIONS_TABLE = f"{TABLE_PREFIX}sessions"
VIDEOS_TABLE = f"{TABLE_PREFIX}videos"

SESSION_COOKIE = "svg_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600      # 30 days
STREAM_TTL_SECONDS = 12 * 3600            # signed cookies live 12 hours
PBKDF2_ITERATIONS = 200_000

DUMMY_HASH = "0" * 64  # compared against when the user does not exist (timing)

_tables = {}
_ssm = None
_private_key_cache: str | None = None


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    try:
        if path == "/api/health" and method == "GET":
            return _json(200, {"ok": True})
        if path == "/api/login" and method == "POST":
            return _login(event)
        if path == "/api/logout" and method == "POST":
            return _logout(event)
        if path == "/api/videos" and method == "GET":
            return _videos(event)
        if path == "/api/stream-auth" and method == "GET":
            return _stream_auth(event)
        return _json(404, {"error": "not found"})
    except Exception as err:  # noqa: BLE001 - convert anything to a clean 500
        print(f"ERROR {path}: {err!r}")
        return _json(500, {"error": "internal error"})


# ---------------------------------------------------------------- login flow

def _login(event: dict) -> dict:
    body = _parse_body(event)
    username = str(body.get("username", "")).strip().lower()
    password = str(body.get("password", ""))
    if not username or not password:
        return _json(400, {"error": "username and password are required"})

    users = _table(USERS_TABLE)
    item = users.get_item(Key={"username": username}).get("Item")
    if item is None:
        _hash_password(password, "00" * 16)  # equalize timing
        return _json(401, {"error": "invalid credentials"})

    derived = _hash_password(password, item["salt"])
    if not hmac.compare_digest(derived, item["password_hash"]):
        return _json(401, {"error": "invalid credentials"})

    token = secrets.token_urlsafe(32)
    now = int(time.time())
    _table(SESSIONS_TABLE).put_item(Item={
        "token_hash": _token_hash(token),
        "username": username,
        "expires": now + SESSION_TTL_SECONDS,
    })

    cookies = [_session_cookie(token, SESSION_TTL_SECONDS)]
    return _json(200, {"ok": True, "username": username}, cookies=cookies)


def _logout(event: dict) -> dict:
    session = _session_from(event)
    if session:
        _table(SESSIONS_TABLE).delete_item(Key={"token_hash": _token_hash(session)})
    clear = [
        _cookie(SESSION_COOKIE, "", max_age=0),
        _cookie("CloudFront-Policy", "", max_age=0),
        _cookie("CloudFront-Signature", "", max_age=0),
        _cookie("CloudFront-Key-Pair-Id", "", max_age=0),
    ]
    return _json(200, {"ok": True}, cookies=clear)


def _videos(event: dict) -> dict:
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    items = []
    table = _table(VIDEOS_TABLE)
    response = table.scan()
    items.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:  # paginate manually past 1 MB pages
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))

    videos = []
    for item in items:
        slug = item["slug"]
        videos.append({
            "slug": slug,
            "title": item.get("title", slug),
            "folder": item.get("folder", "Videos"),
            "duration_seconds": int(item.get("duration_seconds", 0)),
            "width": int(item.get("width", 0)) if item.get("width") else None,
            "height": int(item.get("height", 0)) if item.get("height") else None,
            "playlist": f"/videos/{slug}/index.m3u8",
            "thumbnail": f"/videos/{slug}/thumbnail.jpg",
        })
    videos.sort(key=lambda v: (v["folder"].lower(), v["title"].lower()))
    return _json(200, {"username": username, "videos": videos})


def _stream_auth(event: dict) -> dict:
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})
    return _json(200, {"ok": True, "username": username},
                 cookies=_stream_cookies())


# ------------------------------------------------------------------- helpers

def _require_session(event: dict) -> str | None:
    token = _session_from(event)
    if not token:
        return None
    item = _table(SESSIONS_TABLE).get_item(
        Key={"token_hash": _token_hash(token)}
    ).get("Item")
    if item is None or int(item["expires"]) < int(time.time()):
        return None
    return item["username"]


def _session_from(event: dict) -> str | None:
    prefix = f"{SESSION_COOKIE}="
    for cookie in event.get("cookies", []):
        if cookie.startswith(prefix):
            return cookie[len(prefix):]
    return None


def _session_cookie(token: str, max_age: int) -> str:
    return _cookie(SESSION_COOKIE, token, max_age=max_age)


def _cookie(name: str, value: str, max_age: int) -> str:
    return (
        f"{name}={value}; Domain={DOMAIN}; Path=/; Secure; HttpOnly; "
        f"SameSite=Lax; Max-Age={max_age}"
    )


def _stream_cookies() -> list[str]:
    expires = int(time.time()) + STREAM_TTL_SECONDS
    policy = json.dumps({
        "Statement": [{
            "Resource": f"https://{DOMAIN}/videos/*",
            "Condition": {"DateLessThan": {"AWS:EpochTime": expires}},
        }]
    }, separators=(",", ":"))
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(_private_key().encode(), password=None)
    signature = key.sign(policy.encode(), padding.PKCS1v15(), hashes.SHA1())
    values = {
        "CloudFront-Policy": _cf_b64(policy.encode()),
        "CloudFront-Signature": _cf_b64(signature),
        "CloudFront-Key-Pair-Id": KEY_PAIR_ID,
    }
    return [_cookie(name, value, max_age=STREAM_TTL_SECONDS)
            for name, value in values.items()]


def _cf_b64(data: bytes) -> str:
    """Base64 in CloudFront's custom alphabet ('/'->'~', '='->'_')."""
    return (
        base64.b64encode(data)
        .replace(b"+", b"-")
        .replace(b"=", b"_")
        .replace(b"/", b"~")
        .decode("ascii")
    )


def _private_key() -> str:
    global _private_key_cache
    if _private_key_cache is None:
        resp = _ssm_client().get_parameter(
            Name=SSM_PARAM, WithDecryption=True
        )
        _private_key_cache = resp["Parameter"]["Value"]
    return _private_key_cache


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
    ).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _parse_body(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _json(status: int, body: dict, cookies: list[str] | None = None) -> dict:
    resp = {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
    if cookies:
        resp["cookies"] = cookies
    return resp


def _dynamo():
    return boto3.resource("dynamodb")


def _table(name: str):
    if name not in _tables:
        _tables[name] = _dynamo().Table(name)
    return _tables[name]


def _ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm")
    return _ssm
