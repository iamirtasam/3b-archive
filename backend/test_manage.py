"""E2E test for archive management routes (rename/move/folder-rename/delete).

Creates a throwaway catalog entry (no S3 objects behind it), exercises every
management route against it via the real HTTP API, then deletes it and the
test user. Real videos are never touched.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
API = "https://no5vvtdoxh.execute-api.ap-south-1.amazonaws.com"
REGION = CFG["aws"]["region"]
SLUG = "manage-test-entry"


def call(path: str, body: dict | None = None, cookie: str | None = None):
    req = urllib.request.Request(f"{API}{path}", method="POST" if body else "GET")
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("content-type", "application/json")
    if cookie:
        req.add_header("cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("set-cookie", "")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), ""


def main() -> None:
    ddb = boto3.client("dynamodb", region_name=REGION)
    # seed throwaway entry + test user
    ddb.put_item(TableName="svg_videos", Item={
        "slug": {"S": SLUG}, "title": {"S": "manage test"},
        "folder": {"S": "TestFolder"}, "duration_seconds": {"N": "1"},
        "width": {"N": "16"}, "height": {"N": "9"},
    })
    import hashlib, secrets
    salt = secrets.token_hex(16)
    ph = hashlib.pbkdf2_hmac("sha256", b"test-pass-123", bytes.fromhex(salt), 200_000).hex()
    ddb.put_item(TableName="svg_users", Item={
        "username": {"S": "managetest"}, "salt": {"S": salt},
        "password_hash": {"S": ph}, "iterations": {"N": "200000"},
    })
    try:
        st, data, setc = call("/api/login", {"username": "managetest", "password": "test-pass-123"})
        assert st == 200, f"login failed: {data}"
        cookie = setc.split(";")[0]

        st, data, _ = call("/api/videos/rename", {"slug": SLUG, "title": "renamed test"}, cookie)
        assert st == 200, data
        st, data, _ = call("/api/videos/move", {"slug": SLUG, "folder": "MovedFolder"}, cookie)
        assert st == 200, data
        st, data, _ = call("/api/folders/rename", {"from": "MovedFolder", "to": "FinalFolder"}, cookie)
        assert st == 200 and data["moved"] == 1, data
        st, data, _ = call("/api/videos", None, cookie)
        item = next(v for v in data["videos"] if v["slug"] == SLUG)
        assert item["title"] == "renamed test" and item["folder"] == "FinalFolder", item
        print("rename/move/folder-rename: OK")

        st, data, _ = call("/api/videos/delete", {"slug": SLUG}, cookie)
        assert st == 200, data
        st, data, _ = call("/api/videos", None, cookie)
        assert not any(v["slug"] == SLUG for v in data["videos"])
        print("delete: OK (entry removed from catalog)")
    finally:
        ddb.delete_item(TableName="svg_videos", Key={"slug": {"S": SLUG}})
        ddb.delete_item(TableName="svg_users", Key={"username": {"S": "managetest"}})
        print("cleanup done")


if __name__ == "__main__":
    main()
