"""Upload prepared HLS folders and thumbnails to S3, with resume support."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import boto3
import botocore
import botocore.exceptions
from boto3.s3.transfer import TransferConfig

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".json": "application/json",
}


class Uploader:
    """Thin wrapper around boto3 with sane multipart settings and skip-if-done."""

    def __init__(self, config: dict[str, Any]):
        aws = config.get("aws", {})
        self.region = aws.get("region", "us-east-1")
        profile = aws.get("profile") or None
        session = boto3.session.Session(profile_name=profile, region_name=self.region)
        self.s3 = session.client(
            "s3",
            config=botocore.config.Config(
                retries={"max_attempts": 10, "mode": "adaptive"},
            ),
        )
        self.bucket = config["s3"]["bucket"]
        self.prefix = config["s3"].get("prefix", "videos").strip("/")
        upload_cfg = config.get("upload", {})
        self.transfer = TransferConfig(
            multipart_threshold=upload_cfg.get("multipart_threshold_mb", 64) * 1024 * 1024,
            multipart_chunksize=upload_cfg.get("multipart_chunksize_mb", 16) * 1024 * 1024,
            max_concurrency=upload_cfg.get("max_concurrency", 8),
            use_threads=True,
        )

    def check_bucket(self) -> None:
        """Fail fast with a helpful message when the bucket or credentials are wrong."""
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except botocore.exceptions.ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchBucket"):
                raise SystemExit(
                    f"Bucket '{self.bucket}' does not exist in {self.region}. "
                    "Create it first (see the README, Phase 1)."
                ) from err
            if code == "403":
                raise SystemExit(
                    f"No permission to access '{self.bucket}'. "
                    "Run `aws configure` or check the IAM permissions."
                ) from err
            raise
        except botocore.exceptions.NoCredentialsError as err:
            raise SystemExit(
                "No AWS credentials found. Run `aws configure` and enter your access key."
            ) from err
        log.info("Bucket OK: s3://%s (%s)", self.bucket, self.region)

    def upload_video(self, slug: str, hls_dir: Path) -> int:
        """Upload every file in an HLS folder. Returns the number uploaded."""
        count = 0
        for file in sorted(hls_dir.rglob("*")):
            if not file.is_file():
                continue
            key = f"{self.prefix}/{slug}/{file.relative_to(hls_dir).as_posix()}"
            if self._put(file, key):
                count += 1
        return count

    def upload_thumbnail(self, slug: str, thumb: Path) -> bool:
        key = f"{self.prefix}/{slug}/thumbnail.jpg"
        return self._put(thumb, key)

    def _put(self, local: Path, key: str) -> bool:
        """Upload one file unless S3 already has it with the same size."""
        if self._already_uploaded(local, key):
            log.debug("Skip (already on S3): %s", key)
            return False
        extra: dict[str, Any] = {}
        content_type = CONTENT_TYPES.get(local.suffix.lower())
        if content_type:
            extra["ContentType"] = content_type
        self.s3.upload_file(
            str(local),
            self.bucket,
            key,
            Config=self.transfer,
            ExtraArgs=extra or None,
        )
        log.debug("Uploaded %s -> s3://%s/%s", local.name, self.bucket, key)
        return True

    def _already_uploaded(self, local: Path, key: str) -> bool:
        try:
            head = self.s3.head_object(Bucket=self.bucket, Key=key)
        except botocore.exceptions.ClientError as err:
            status = err.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise
        return head.get("ContentLength") == local.stat().st_size
