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
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

TABLE_PREFIX = os.environ.get("TABLE_PREFIX", "svg_")
DOMAIN = os.environ["DOMAIN"]
KEY_PAIR_ID = os.environ["KEY_PAIR_ID"]
SSM_PARAM = os.environ["SSM_PARAM"]
BUCKET = os.environ.get("BUCKET", "")
UPLOAD_PREFIX = "uploads"

USERS_TABLE = f"{TABLE_PREFIX}users"
SESSIONS_TABLE = f"{TABLE_PREFIX}sessions"
VIDEOS_TABLE = f"{TABLE_PREFIX}videos"

SESSION_COOKIE = "svg_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600      # 30 days
STREAM_TTL_SECONDS = 12 * 3600            # signed cookies live 12 hours
PART_SIZE = 16 * 1024 * 1024              # 16 MB parts, same as the CLI pipeline
PARTS_PER_PAGE = 100                      # presigned URLs per response
PRESIGN_TTL = 3600                        # part URLs live 1 hour
MAX_UPLOAD_BYTES = 50 * 1024 * 1024 * 1024  # packager streams segments out
                                            # as they are produced, so file
                                            # size is bounded by the 15-min
                                            # limit, not scratch space
ALLOWED_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".avi", ".webm"}
FFMPEG = str(Path(__file__).resolve().parent / "bin" / "ffmpeg")
# vendored static build (fetch_ffmpeg.py) - extracted next to handler.py
PBKDF2_ITERATIONS = 200_000

DUMMY_HASH = "0" * 64  # compared against when the user does not exist (timing)

_tables = {}
_ssm = None
_private_key_cache: str | None = None


def lambda_handler(event, context):
    # Background invocations fired by _upload_complete (Event = async).
    if event.get("action") == "package":
        return _package_job(event)
    if event.get("action") == "finish":
        event["context_arn"] = context.invoked_function_arn
        return _finish_job(event)
    if event.get("action") == "yt_push":
        event["context_arn"] = context.invoked_function_arn
        return _yt_push_job(event)
    if event.get("action") == "yt_push_all":
        event["context_arn"] = context.invoked_function_arn
        return _yt_push_all_job(event)

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
        if path == "/api/videos/delete" and method == "POST":
            return _video_delete(event)
        if path == "/api/videos/rename" and method == "POST":
            return _video_rename(event)
        if path == "/api/videos/move" and method == "POST":
            return _video_move(event)
        if path == "/api/folders/rename" and method == "POST":
            return _folder_rename(event)
        if path == "/api/stream-auth" and method == "GET":
            return _stream_auth(event)
        if path == "/api/uploads" and method == "GET":
            return _uploads_list(event)
        if path == "/api/uploads" and method == "POST":
            return _upload_start(event)
        if path.startswith("/api/uploads/") and method == "POST":
            last = event["rawPath"].rsplit("/", 1)[-1]
            if last == "complete":
                return _upload_complete(event, context)
            if last == "abort":
                return _upload_abort(event)
            return _upload_parts(event)
        if path.startswith("/api/uploads/") and method == "DELETE":
            return _upload_abort(event)  # body carries key + upload_id
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


# ------------------------------------------------------- archive management

def _video_delete(event: dict) -> dict:
    """Remove a video from the archive: S3 objects + catalog entry."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})
    slug = str(_parse_body(event).get("slug", ""))
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return _json(400, {"error": "invalid slug"})

    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"videos/{slug}/"):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=BUCKET, Delete={"Objects": objects})
            deleted += len(objects)
    _table(VIDEOS_TABLE).delete_item(Key={"slug": slug})
    print(f"ARCHIVE {username} deleted {slug} ({deleted} objects)")
    return _json(200, {"ok": True, "slug": slug, "objects": deleted})


def _video_rename(event: dict) -> dict:
    """Change a video's display title."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})
    body = _parse_body(event)
    slug = str(body.get("slug", ""))
    title = str(body.get("title", "")).strip()
    if not re.fullmatch(r"[a-z0-9-]+", slug) or not title:
        return _json(400, {"error": "slug and title are required"})
    if len(title) > 120:
        return _json(400, {"error": "title too long (max 120)"})
    _table(VIDEOS_TABLE).update_item(
        Key={"slug": slug},
        UpdateExpression="SET #t = :t",
        ExpressionAttributeNames={"#t": "title"},
        ExpressionAttributeValues={":t": title},
        ConditionExpression="attribute_exists(slug)",
    )
    return _json(200, {"ok": True, "slug": slug, "title": title})


def _video_move(event: dict) -> dict:
    """Move a video into a folder (creates the folder by naming it)."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})
    body = _parse_body(event)
    slug = str(body.get("slug", ""))
    folder = str(body.get("folder", "")).strip()
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return _json(400, {"error": "invalid slug"})
    if not folder or len(folder) > 60:
        return _json(400, {"error": "folder name required (max 60)"})
    _table(VIDEOS_TABLE).update_item(
        Key={"slug": slug},
        UpdateExpression="SET #f = :f",
        ExpressionAttributeNames={"#f": "folder"},
        ExpressionAttributeValues={":f": folder},
        ConditionExpression="attribute_exists(slug)",
    )
    return _json(200, {"ok": True, "slug": slug, "folder": folder})


def _folder_rename(event: dict) -> dict:
    """Rename a folder: bulk-update every video in it."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})
    body = _parse_body(event)
    old = str(body.get("from", "")).strip()
    new = str(body.get("to", "")).strip()
    if not old or not new or len(new) > 60:
        return _json(400, {"error": "from and to are required (max 60)"})
    if old == new:
        return _json(200, {"ok": True, "moved": 0})

    table = _table(VIDEOS_TABLE)
    moved = 0
    response = table.scan(
        FilterExpression="#f = :old",
        ExpressionAttributeNames={"#f": "folder"},
        ExpressionAttributeValues={":old": old},
    )
    items = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        response = table.scan(
            ExclusiveStartKey=response["LastEvaluatedKey"],
            FilterExpression="#f = :old",
            ExpressionAttributeNames={"#f": "folder"},
            ExpressionAttributeValues={":old": old},
        )
        items.extend(response.get("Items", []))
    for item in items:
        table.update_item(
            Key={"slug": item["slug"]},
            UpdateExpression="SET #f = :new",
            ExpressionAttributeNames={"#f": "folder"},
            ExpressionAttributeValues={":new": new},
        )
        moved += 1
    print(f"ARCHIVE {username} renamed folder {old!r} -> {new!r} ({moved} videos)")
    return _json(200, {"ok": True, "moved": moved})


# ------------------------------------------------------------ upload flow
#
# Design: zero extra infrastructure. S3 multipart state IS the database -
# ListMultipartUploads shows what is staged. On completion the route fires
# an async self-invocation that runs the lossless ffmpeg remux inside
# Lambda (streaming from S3, never storing the source) and publishes the
# HLS output straight to videos/. No manual publish step, still free-tier.

def _sanitize_filename(name: str) -> str:
    """Keep the basename, strip path tricks and characters S3 dislikes."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in base)
    return cleaned.strip(". ") or "recording"


def _sanitize_folder(name: str) -> str:
    """Folder segment for uploads/<user>/<folder>/<file> - no slashes, no
    traversal, no dots-only names."""
    cleaned = "".join(c if c.isalnum() or c in " _-()" else "_" for c in name)
    return cleaned.strip(". ")[:60] or "Uploads"


def _validate_upload(filename: str, size: int) -> str | None:
    if not BUCKET:
        return "uploads are not configured (BUCKET env missing)"
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        return f"file size must be 1 byte to {MAX_UPLOAD_BYTES // (1024**3)} GB"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return f"unsupported file type {ext or '(none)'} - allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
    return None


def _part_count(size: int, part_size: int) -> int:
    return max(1, -(-size // part_size))


GB = 1024 ** 3


def _part_size_for(size: int) -> int:
    """Larger parts for larger files. S3's CompleteMultipartUpload assembly
    time scales with part count, and the API gateway cuts requests at 30 s -
    the classic large-file 'FINISH FAILED' that actually succeeded
    server-side. Keeping the count <=~512 keeps the finish call fast."""
    if size <= 8 * GB:
        return PART_SIZE              # 16 MB -> <=512 parts at 8 GB
    if size <= 32 * GB:
        return 64 * 1024 * 1024      # 64 MB -> <=512 parts at 32 GB
    return 128 * 1024 * 1024         # 128 MB -> <=400 parts at 50 GB


def _uploads_list(event: dict) -> dict:
    """List in-flight multipart uploads straight from S3 (no tracking table)."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    s3 = boto3.client("s3")
    uploads = []
    paginator = s3.get_paginator("list_multipart_uploads")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{UPLOAD_PREFIX}/"):
        for up in page.get("Uploads", []):
            # key format: uploads/<username>[/<folder>]/<filename>
            parts = up["Key"].split("/")
            owner = parts[1] if len(parts) >= 2 else "?"
            folder = parts[2] if len(parts) >= 4 else ""
            uploads.append({
                "filename": up["Key"].split("/")[-1],
                "key": up["Key"],
                "owner": owner,
                "folder": folder,
                "initiated": up["Initiated"].isoformat(),
                "upload_id": up["UploadId"],
            })
    return _json(200, {"uploads": uploads})


def _upload_start(event: dict) -> dict:
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    body = _parse_body(event)
    filename = _sanitize_filename(str(body.get("filename", "")))
    size = int(body.get("size_bytes", 0))
    folder = _sanitize_folder(str(body.get("folder", "")))  # "" → Uploads

    if err := _validate_upload(filename, size):
        return _json(400, {"error": err})

    s3 = boto3.client("s3")
    key = f"{UPLOAD_PREFIX}/{username}/{folder}/{filename}"
    # Deterministic slug (same formula the packager uses): if this exact
    # file is already archived, say so instead of re-uploading it.
    slug = _slug_for(key, filename)
    if _table(VIDEOS_TABLE).get_item(Key={"slug": slug}).get("Item"):
        return _json(409, {"error": "already in the archive",
                           "slug": slug})
    part_size = _part_size_for(size)
    created = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = created["UploadId"]
    return _json(200, {
        "upload_id": upload_id,
        "key": key,
        "part_size": part_size,
        "part_count": _part_count(size, part_size),
        "urls": _presign_parts(key, upload_id, 1,
                               min(_part_count(size, part_size), PARTS_PER_PAGE)),
    })


def _own_key(username: str, key: str) -> bool:
    """uploads/<username>/... - any depth below the user's prefix."""
    return key.startswith(f"{UPLOAD_PREFIX}/{username}/")


def _upload_parts(event: dict) -> dict:
    """Presign the next page of parts, and report already-uploaded parts so
    the browser can resume after a close/refresh."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    upload_id = event["rawPath"].rsplit("/", 1)[-1]
    body = _parse_body(event)
    key = str(body.get("key", ""))
    if not _own_key(username, key):
        return _json(403, {"error": "not your upload"})

    s3 = boto3.client("s3")
    try:
        listed = s3.list_parts(Bucket=BUCKET, Key=key, UploadId=upload_id)
    except ClientError:
        return _json(404, {"error": "upload not found - expired or aborted"})
    received = [{
        "part_number": p["PartNumber"],
        "etag": p["ETag"].strip('"'),
        "size": int(p["Size"]),
    } for p in listed.get("Parts", [])]

    from_part = max(1, int(body.get("from_part", 1)))
    to_part = max(1, int(body.get("to_part", from_part + PARTS_PER_PAGE - 1)))
    return _json(200, {"received": received,
                       "urls": _presign_parts(key, upload_id, from_part,
                                              min(to_part, PARTS_PER_PAGE * 20))})


def _slug_for(key: str, filename: str) -> str:
    """Deterministic video slug shared with scripts/publish.py: the sanitized
    filename stem plus the first 8 hex chars of md5(key). The browser learns
    the slug from the complete response and polls for it."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[^a-z0-9-_]+", "-", stem.lower()).strip("-") or "video"
    digest = hashlib.md5(key.encode()).hexdigest()[:8]
    return f"{base}-{digest}"


def _upload_complete(event: dict, context) -> dict:
    """Hand the finished upload to an async finisher and return instantly.

    The browser must NOT wait on S3's CompleteMultipartUpload: assembly time
    scales with part count and the API gateway cuts requests at 30 s - the
    classic large-file 'FINISH FAILED' that actually succeeded server-side.
    The finisher assembles from S3's own part list with a 10-minute read
    budget, then hands off to the packager as its own async job."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    body = _parse_body(event)
    key = str(body.get("key", ""))
    upload_id = str(body.get("upload_id", ""))
    if not _own_key(username, key):
        return _json(403, {"error": "not your upload"})
    if not upload_id:
        return _json(400, {"error": "upload_id is required"})

    filename = key.rsplit("/", 1)[-1]
    slug = _slug_for(key, filename)
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    try:
        boto3.client("lambda").invoke(
            FunctionName=context.invoked_function_arn,
            InvocationType="Event",           # fire-and-forget background job
            Payload=json.dumps({"action": "finish", "key": key,
                                "upload_id": upload_id, "slug": slug,
                                "title": title}),
        )
    except Exception as err:  # noqa: BLE001 - upload still counts as staged
        print(f"WARN finish invoke failed for {key}: {err!r}")
        return _json(500, {"error": "could not start the finish job"})
    return _json(200, {"ok": True, "key": key, "slug": slug})


def _finish_job(event: dict) -> dict:
    """Assemble the multipart upload from S3's own part list (the source of
    truth - the browser's list is never trusted), then fire the packager as
    its own async job so assembly and remux each get a full 15-minute
    Lambda window."""
    key = str(event.get("key", ""))
    upload_id = str(event.get("upload_id", ""))
    slug = str(event.get("slug", ""))
    title = str(event.get("title", slug))
    if not key or not upload_id or not slug or not BUCKET:
        return {"ok": False, "reason": "bad invocation"}

    from botocore.config import Config
    # S3 assembly time scales with part count; the default 60 s client read
    # timeout loses races with slow assemblies. 10 minutes covers 10,000.
    s3 = boto3.client("s3", config=Config(read_timeout=600, connect_timeout=60,
                                          retries={"max_attempts": 5}))
    parts, token = [], None
    while True:
        kw = dict(Bucket=BUCKET, Key=key, UploadId=upload_id)
        if token:
            kw["PartNumberMarker"] = token
        r = s3.list_parts(**kw)
        parts.extend(r.get("Parts", []))
        token = r.get("NextPartNumberMarker")
        if not r.get("IsTruncated"):
            break
    if not parts:
        print(f"FINISH {key}: no parts found - nothing to assemble")
        return {"ok": False, "reason": "no parts"}
    parts.sort(key=lambda p: p["PartNumber"])
    s3.complete_multipart_upload(
        Bucket=BUCKET, Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": [
            {"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})
    gb = sum(p["Size"] for p in parts) / 1e9
    print(f"FINISH {key}: assembled {len(parts)} parts ({gb:.2f} GB)")

    boto3.client("lambda").invoke(
        FunctionName=event["context_arn"], InvocationType="Event",
        Payload=json.dumps({"action": "package", "key": key, "slug": slug,
                            "title": title}))
    return {"ok": True, "slug": slug, "assembled_gb": round(gb, 2)}


def _upload_abort(event: dict) -> dict:
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    body = _parse_body(event)
    key = str(body.get("key", ""))
    upload_id = str(body.get("upload_id", ""))
    if not _own_key(username, key):
        return _json(403, {"error": "not your upload"})

    boto3.client("s3").abort_multipart_upload(
        Bucket=BUCKET, Key=key, UploadId=upload_id
    )
    return _json(200, {"ok": True})


# ------------------------------------------------------- background packager
#
# Runs as an Event-invoked copy of this same function. Reads the staged
# upload from S3 as a stream (the source never touches /tmp), remuxes it to
# fMP4 HLS with -c copy (lossless, fast - no encoding), grabs a poster
# frame in the same pass, uploads the segments, writes the catalog item and
# deletes the staged original. A 6 GB recording fits the whole flow inside
# Lambda's 15-minute limit and the 10 GB /tmp (only the output lands there).

def _package_job(event: dict) -> dict:
    key = str(event.get("key", ""))
    slug = str(event.get("slug", ""))
    title = str(event.get("title", slug))
    if not key or not slug or not BUCKET:
        return {"ok": False, "reason": "bad invocation"}

    work = Path("/tmp/hls") / slug
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    s3 = boto3.client("s3")

    try:
        # ffmpeg reads straight from a presigned S3 URL: its HTTP backend
        # issues range requests, so MP4s with a trailing moov atom (typical
        # for recordings) seek fine. A stdin pipe cannot seek and breaks
        # exactly there - learned the hard way.
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=4000,
        )
        # Pass 1 - the remux. Stream copy only: the same recipe as
        # scripts/lib/hls.py. ffmpeg reads the source straight from a
        # presigned URL; a watcher thread uploads each finished segment and
        # deletes it locally, so even a 40 GB source only ever occupies a
        # few segments' worth of /tmp.
        remux = subprocess.Popen(
            [
                FFMPEG, "-hide_banner", "-loglevel", "info", "-nostdin", "-y",
                "-i", url,
                "-map", "0:v:0", "-map", "0:a:0?", "-sn",
                "-c", "copy",
                "-f", "hls",
                "-hls_time", "6", "-hls_list_size", "0",
                "-hls_playlist_type", "vod",
                "-hls_flags", "independent_segments",
                "-hls_segment_type", "fmp4",
                "-hls_segment_filename", "seg_%05d.m4s",
                "index.m3u8",
            ],
            cwd=str(work), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        stderr_chunks: list[bytes] = []
        err_reader = threading.Thread(
            target=lambda: stderr_chunks.append(remux.stderr.read()))
        err_reader.start()

        uploaded_files: set[str] = set()

        def watcher() -> None:
            """Upload init + finished segments as they appear, then delete
            the local copies to keep /tmp small. A segment is considered
            finished once ffmpeg has opened the NEXT one (or exited)."""
            s3w = boto3.client("s3")
            while True:
                segs = sorted(work.glob("seg_*.m4s"))
                # everything except possibly the last is complete
                for seg in segs[:-1]:
                    if seg.name in uploaded_files:
                        continue
                    if seg.stat().st_size > 0:
                        _put_hls_object(s3w, seg, slug)
                        uploaded_files.add(seg.name)
                        seg.unlink(missing_ok=True)
                init = work / "init.mp4"
                if init.exists() and "init.mp4" not in uploaded_files \
                        and init.stat().st_size > 0:
                    _put_hls_object(s3w, init, slug)
                    uploaded_files.add("init.mp4")
                    init.unlink(missing_ok=True)
                if remux.poll() is not None:
                    break
                time.sleep(0.5)

        pump = threading.Thread(target=watcher)
        pump.start()
        try:
            remux.wait(timeout=15 * 60 - 30)
        except subprocess.TimeoutExpired:
            remux.kill()
            raise RuntimeError("ffmpeg exceeded the time limit")
        err_reader.join()
        pump.join()
        stderr = (stderr_chunks[0].decode("utf-8", "replace")
                  if stderr_chunks else "")

        if remux.returncode != 0:
            raise RuntimeError(f"ffmpeg exit {remux.returncode}: {stderr[-600:]}")

        width, height, vcodec = _parse_streams(stderr)
        if vcodec != "h264":
            # Stream copy needs H.264. Leave the staged file for publish.py.
            print(f"PACKAGE skip {key}: video codec {vcodec or 'unknown'}")
            return {"ok": False, "reason": "codec", "codec": vcodec}

        duration = _playlist_duration(work / "index.m3u8")
        if duration <= 0:
            raise RuntimeError("zero-length playlist - demux produced nothing")

        # Poster frame from the first segment. init.mp4 may already have
        # been streamed off; re-fetch it if needed (tiny).
        init_path = work / "init.mp4"
        if not init_path.exists() and "init.mp4" in uploaded_files:
            s3.download_file(BUCKET, f"videos/{slug}/init.mp4", str(init_path))
        segments = sorted(work.glob("seg_*.m4s")) or \
            [p for p in work.iterdir() if p.name.startswith("seg_")]
        if not segments:
            # everything was streamed off; fetch the first segment back
            playlist_text = (work / "index.m3u8").read_text(encoding="utf-8")
            first_seg = next((l.strip() for l in playlist_text.splitlines()
                              if l.strip().endswith(".m4s")), None)
            if not first_seg:
                raise RuntimeError("ffmpeg produced no video segments")
            segments = [work / first_seg]
            s3.download_file(BUCKET, f"videos/{slug}/{first_seg}",
                             str(segments[0]))
        if not init_path.exists():
            raise RuntimeError("no init segment for poster frame")
        combined = work / "first.mp4"
        combined.write_bytes(
            init_path.read_bytes() + segments[0].read_bytes())
        thumb_ok = False
        for seek in ("0.5", "0"):
            cut = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-y", "-ss", seek, "-i", "first.mp4",
                 "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3",
                 "thumbnail.jpg"],
                cwd=str(work), capture_output=True)
            if cut.returncode == 0 and (work / "thumbnail.jpg").exists():
                thumb_ok = True
                break
        combined.unlink(missing_ok=True)
        if not thumb_ok:
            print(f"PACKAGE thumbnail failed for {slug} "
                  f"(publishing without one)")

        # Remaining files: playlist (+ thumbnail); segments were streamed.
        _upload_hls(work, slug, skip=uploaded_files)
        # Inherit the upload folder: uploads/<user>/<Folder>/<file> ->
        # archive folder <Folder> (loose files land in "Uploads").
        parts = key.split("/")
        upload_folder = parts[2] if len(parts) >= 4 else "Uploads"
        _table(VIDEOS_TABLE).put_item(Item={
            "slug": slug,
            "title": title,
            "folder": upload_folder,
            "duration_seconds": int(duration),
            "width": width or 0,
            "height": height or 0,
        })
        s3.delete_object(Bucket=BUCKET, Key=key)   # staged original consumed
        print(f"PACKAGE ok {slug}: {duration:.0f}s {width}x{height} "
              f"thumb={'yes' if thumb_ok else 'no'}")
        return {"ok": True, "slug": slug}
    except Exception as err:  # noqa: BLE001 - keep staged file on any failure
        print(f"PACKAGE failed {key}: {err!r}")
        return {"ok": False, "error": repr(err)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _parse_streams(stderr: str) -> tuple[int, int, str]:
    """Pull resolution + codec from ffmpeg's input report (no ffprobe)."""
    vmatch = re.search(r"Stream #0:\d+.*?: Video: (\w+).*?, (\d{2,5})x(\d{2,5})",
                       stderr)
    codec = vmatch.group(1).lower() if vmatch else ""
    width = int(vmatch.group(2)) if vmatch else 0
    height = int(vmatch.group(3)) if vmatch else 0
    return width, height, codec


def _playlist_duration(playlist: Path) -> float:
    """Sum the EXTINF tags - exact total runtime, free."""
    total = 0.0
    for line in playlist.read_text(encoding="utf-8").splitlines():
        if line.startswith("#EXTINF:"):
            total += float(line.split(":", 1)[1].split(",", 1)[0])
    return total


_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
}


def _put_hls_object(s3, path: Path, slug: str) -> None:
    """Upload one HLS artifact to videos/<slug>/."""
    s3.put_object(
        Bucket=BUCKET, Key=f"videos/{slug}/{path.name}",
        Body=path.read_bytes(),
        ContentType=_CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
    )


def _upload_hls(work: Path, slug: str, skip: set[str] | None = None) -> None:
    """Push every generated file still on disk to videos/<slug>/."""
    from concurrent.futures import ThreadPoolExecutor

    skip = skip or set()
    files = [p for p in work.iterdir() if p.is_file() and p.name not in skip]
    if not any(p.name == "index.m3u8" for p in files):
        raise RuntimeError("ffmpeg produced no playlist")
    s3 = boto3.client("s3")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda p: _put_hls_object(s3, p, slug), files))


def _presign_parts(key: str, upload_id: str,
                   from_part: int, to_part: int) -> list[dict]:
    s3 = boto3.client("s3")
    return [{
        "part_number": n,
        "url": s3.generate_presigned_url(
            "upload_part",
            Params={"Bucket": BUCKET, "Key": key,
                    "UploadId": upload_id, "PartNumber": n},
            ExpiresIn=PRESIGN_TTL,
        ),
    } for n in range(from_part, to_part + 1)]


# ------------------------------------------------------- YouTube push job
#
# Streams an archived video straight into YouTube's resumable upload:
#   - reads the OAuth token from SSM (SecureString), refreshes it when
#     expired and writes the fresh token back
#   - serves the video as random-access bytes over S3 range reads (the
#     fMP4 init + segments concatenated IS a valid MP4)
#   - files the video into a playlist named after its archive folder
#   - records the result in svg_youtube so pushes are never duplicated
#
# Queue the whole archive with: action=yt_push_all (one Event invocation
# per video, chain-driven, so every video gets its own 15-min window).

YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
             "https://www.googleapis.com/auth/youtube"]


def _yt_token() -> dict:
    """Load the OAuth token from SSM, refreshing if expired."""
    import requests
    ssm = _ssm_client()
    param = os.environ["YT_TOKEN_PARAM"]
    token = json.loads(
        ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"])

    # expiry may be an epoch float or an ISO string from the OAuth lib
    exp = token.get("expiry")
    if isinstance(exp, str):
        try:
            from datetime import datetime
            exp = datetime.fromisoformat(
                exp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            exp = 0
    if exp and time.time() < float(exp) - 60:
        return token

    client = requests.post(
        "https://oauth2.googleapis.com/token",
        data={"client_id": token["client_id"],
              "client_secret": token["client_secret"],
              "refresh_token": token["refresh_token"],
              "grant_type": "refresh_token"}, timeout=30).json()
    if "access_token" not in client:
        raise RuntimeError(f"token refresh failed: {client}")
    token["token"] = client["access_token"]
    token["expiry"] = time.time() + int(client.get("expires_in", 3600))
    ssm.put_parameter(Name=param, Value=json.dumps(token), Type="SecureString",
                      Overwrite=True)
    return token


def _yt_client():
    from googleapiclient.discovery import build
    token = _yt_token()
    return build("youtube", "v3",
                 developerKey=None,
                 credentials=None,
                 static_discovery=False,
                 _http=None) if False else _yt_build(token)


def _yt_build(token: dict):
    import google.auth.transport.requests as gtr
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    class _SSMCreds:
        """Credentials shim backed by the SSM-stored refresh token."""

        def __init__(self, tok):
            self.token = tok["token"]
            self.expiry = None

        def refresh(self, request):
            tok = _yt_token()
            self.token = tok["token"]

        def apply(self, headers, *a, **kw):
            headers["authorization"] = f"Bearer {self.token}"

    creds = Credentials(
        token=token["token"], refresh_token=token["refresh_token"],
        client_id=token["client_id"], client_secret=token["client_secret"],
        scopes=YT_SCOPES)
    return build("youtube", "v3", credentials=creds, static_discovery=False)


class _S3RangeVideo:
    """googleapiclient MediaUpload serving an S3 object with range reads -
    the archive video as one seekable MP4 without touching /tmp."""

    def __init__(self, s3, key: str, size: int, chunk: int = 16 * 1024 * 1024):
        from googleapiclient.http import MediaUpload
        self._s3, self._key, self._size, self._chunk = s3, key, size, chunk
        self.pos = 0

    def size(self):
        return self._size

    def chunksize(self):
        return self._chunk

    def resumable(self):
        return True

    def mimetype(self):
        return "video/mp4"

    def getbytes(self, begin: int, length: int) -> bytes:
        if begin >= self._size:
            return b""
        length = min(length, self._size - begin)
        rng = f"bytes={begin}-{begin + length - 1}"
        return self._s3.get_object(Bucket=BUCKET, Key=self._key,
                                   Range=rng)["Body"].read()

    def _replayable(self):
        return True

    @property
    def replayable(self):
        return True


def _yt_playlist_for(yt, folder: str, privacy: str) -> str:
    """One playlist per archive folder, created once and reused."""
    table = _table(f"{TABLE_PREFIX}youtube")
    cached = table.get_item(Key={"slug": f"pl:{folder.lower()}"}).get("Item")
    if cached:
        return cached["playlist_id"]
    token = None
    while True:
        resp = yt.playlists().list(mine=True, part="snippet", maxResults=50,
                                   pageToken=token).execute()
        for pl in resp.get("items", []):
            if pl["snippet"]["title"].lower() == folder.lower():
                table.put_item(Item={"slug": f"pl:{folder.lower()}",
                                     "playlist_id": pl["id"]})
                return pl["id"]
        token = resp.get("nextPageToken")
        if not token:
            break
    pl = yt.playlists().insert(part="snippet,status", body={
        "snippet": {"title": folder[:150],
                    "description": f"3B Archive folder — {folder}."},
        "status": {"privacyStatus": privacy},
    }).execute()
    table.put_item(Item={"slug": f"pl:{folder.lower()}",
                         "playlist_id": pl["id"]})
    print(f"YT playlist created: {folder} ({pl['id']})")
    return pl["id"]


def _yt_in_playlist(yt, playlist_id: str, video_id: str) -> bool:
    token = None
    while True:
        resp = yt.playlistItems().list(
            playlistId=playlist_id, part="contentDetails", maxResults=50,
            pageToken=token).execute()
        for it in resp.get("items", []):
            if it["contentDetails"]["videoId"] == video_id:
                return True
        token = resp.get("nextPageToken")
        if not token:
            return False


def _yt_push_job(event: dict) -> dict:
    from botocore.config import Config

    slug = str(event.get("slug", ""))
    privacy = str(event.get("privacy", "private"))
    queue = event.get("queue") or []
    if not slug:
        return {"ok": False, "reason": "no slug"}

    yt = _yt_client()
    yt_table = _table(f"{TABLE_PREFIX}youtube")
    if yt_table.get_item(Key={"slug": slug}).get("Item"):
        print(f"YT skip {slug}: already pushed")
        return {"ok": True, "slug": slug, "skipped": True}

    item = _table(VIDEOS_TABLE).get_item(Key={"slug": slug}).get("Item")
    if not item:
        return {"ok": False, "reason": "not in catalog"}
    title = str(item.get("title", slug))
    folder = str(item.get("folder", "Archive"))

    s3 = boto3.client("s3", config=Config(read_timeout=120))
    pl_text = s3.get_object(Bucket=BUCKET,
                            Key=f"videos/{slug}/index.m3u8")["Body"].read().decode()
    keys = [f"videos/{slug}/{l.strip()}" for l in pl_text.splitlines()
            if l.strip() and not l.startswith("#")]
    init = None
    for l in pl_text.splitlines():
        if l.startswith("#EXT-X-MAP:"):
            init = f"videos/{slug}/{l.split('URI=')[1].strip(chr(34))}"
            break
    ordered = ([init] if init else []) + keys
    parts, offset = [], 0
    for k in ordered:
        h = s3.head_object(Bucket=BUCKET, Key=k)
        parts.append({"key": k, "size": h["ContentLength"], "offset": offset})
        offset += h["ContentLength"]
    total = offset

    media = _yt_push_media(s3, parts, total)
    body = {
        "snippet": {"title": title[:100],
                    "description": f"3B Archive — {folder}.",
                    "tags": ["3b-archive", folder.lower().replace(" ", "-")],
                    "categoryId": "20"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    request = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=media)
    response, last_pct = None, -1
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct >= last_pct + 10:
                print(f"YT {slug}: {pct}%")
                last_pct = pct
    vid = response["id"]
    actual_priv = response.get("status", {}).get("privacyStatus", "?")
    print(f"YT pushed {slug} -> {vid} ({actual_priv})")

    pl_id = _yt_playlist_for(yt, folder, privacy)
    if not _yt_in_playlist(yt, pl_id, vid):
        yt.playlistItems().insert(part="snippet", body={
            "snippet": {"playlistId": pl_id, "resourceId": {
                "kind": "youtube#video", "videoId": vid}}}).execute()
    yt_table.put_item(Item={
        "slug": slug, "youtube_id": vid, "privacy": actual_priv,
        "pushed_at": int(time.time())})

    # chain: fire the next queued video in its own 15-min window
    if queue:
        arn = event.get("context_arn")
        if arn:
            boto3.client("lambda").invoke(
                FunctionName=arn, InvocationType="Event",
                Payload=json.dumps({"action": "yt_push", "slug": queue[0],
                                    "privacy": privacy, "queue": queue[1:],
                                    "context_arn": arn}))
    return {"ok": True, "slug": slug, "youtube_id": vid}


class _yt_push_media:
    """MediaUpload over the virtual fMP4 concat (init + segments)."""

    def __init__(self, s3, parts, total, chunk=16 * 1024 * 1024):
        from googleapiclient.http import MediaUpload
        self._s3, self._parts, self._total, self._chunk = s3, parts, total, chunk
        self.pos = 0

    def size(self):
        return self._total

    def chunksize(self):
        return self._chunk

    def resumable(self):
        return True

    def mimetype(self):
        return "video/mp4"

    def getbytes(self, begin: int, length: int) -> bytes:
        if begin >= self._total:
            return b""
        length = min(length, self._total - begin)
        out = bytearray()
        while len(out) < length:
            pos = begin + len(out)
            part = next(p for p in self._parts
                        if p["offset"] <= pos < p["offset"] + p["size"])
            s = pos - part["offset"]
            take = min(length - len(out), part["size"] - s)
            body = self._s3.get_object(
                Bucket=BUCKET, Key=part["key"],
                Range=f"bytes={s}-{s + take - 1}")["Body"].read()
            out.extend(body)
        self.pos = begin + len(out)
        return bytes(out)

    @property
    def replayable(self):
        return True


def _yt_push_all_job(event: dict) -> dict:
    """Chain-push every archived video, one Event invocation per video."""
    slugs = [i["slug"] for i in _table(VIDEOS_TABLE).scan(
        ProjectionExpression="slug").get("Items", [])]
    yt_done = {i["slug"] for i in _table(f"{TABLE_PREFIX}youtube").scan(
        ProjectionExpression="slug").get("Items", [])}
    pending = [s for s in sorted(slugs) if s not in yt_done]
    print(f"YT push-all: {len(pending)} pending")
    arn = event.get("context_arn")
    if pending and arn:
        boto3.client("lambda").invoke(
            FunctionName=arn, InvocationType="Event",
            Payload=json.dumps({"action": "yt_push", "slug": pending[0],
                                "privacy": event.get("privacy", "private"),
                                "queue": pending[1:]}))
    return {"ok": True, "remaining": len(pending)}


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
