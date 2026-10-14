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
    # Background invocation fired by _upload_complete (Event source = async).
    if event.get("action") == "package":
        return _package_job(event)

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


def _part_count(size: int) -> int:
    return max(1, -(-size // PART_SIZE))


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
    created = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = created["UploadId"]
    return _json(200, {
        "upload_id": upload_id,
        "key": key,
        "part_size": PART_SIZE,
        "part_count": _part_count(size),
        "urls": _presign_parts(key, upload_id, 1,
                               min(_part_count(size), PARTS_PER_PAGE)),
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
    """Finish the multipart session server-side from the part list the
    browser collected - no upload bytes ever flow through Lambda. Then kick
    off the async packaging job so the video publishes itself."""
    username = _require_session(event)
    if username is None:
        return _json(401, {"error": "not logged in"})

    body = _parse_body(event)
    key = str(body.get("key", ""))
    upload_id = str(body.get("upload_id", ""))
    raw_parts = body.get("parts", [])
    if not _own_key(username, key):
        return _json(403, {"error": "not your upload"})
    if not upload_id or not raw_parts:
        return _json(400, {"error": "upload_id and parts are required"})

    # S3 rejects CompleteMultipartUpload unless part numbers ascend.
    # Concurrent browser workers finish out of order, so sort here.
    parts = sorted(
        ({"PartNumber": int(p["part_number"]), "ETag": str(p["etag"])}
         for p in raw_parts if p.get("etag")),
        key=lambda p: p["PartNumber"],
    )
    try:
        boto3.client("s3").complete_multipart_upload(
            Bucket=BUCKET, Key=key, UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "UnknownError")
        return _json(409, {"error": f"S3 refused completion ({code})"})

    filename = key.rsplit("/", 1)[-1]
    slug = _slug_for(key, filename)
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    try:
        boto3.client("lambda").invoke(
            FunctionName=context.invoked_function_arn,
            InvocationType="Event",           # fire-and-forget background job
            Payload=json.dumps(
                {"action": "package", "key": key, "slug": slug, "title": title}),
        )
    except Exception as err:  # noqa: BLE001 - upload still counts as staged
        print(f"WARN package invoke failed for {key}: {err!r}")

    return _json(200, {"ok": True, "key": key, "slug": slug})


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
        _table(VIDEOS_TABLE).put_item(Item={
            "slug": slug,
            "title": title,
            "folder": "Uploads",
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
