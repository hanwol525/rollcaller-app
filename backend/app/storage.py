"""Single interface for all blob reads/writes.

    storage.save(data: bytes) -> key
    storage.load(key: str) -> bytes
    storage.url(key: str) -> str
    storage.delete(key: str) -> None

Swap backends by changing `settings.storage_backend` in config.py ("filesystem"
or "minio"). Only this file needs to change to introduce a new backend.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings


class StorageBackend:
    """Abstract storage interface. Subclasses implement save/load/url/delete."""

    def save(self, data: bytes, ext: str = "wav") -> str:
        raise NotImplementedError

    def load(self, key: str) -> bytes:
        raise NotImplementedError

    def url(self, key: str) -> str:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class FilesystemStorage(StorageBackend):
    """Dev storage: writes blobs to a local directory, served via /media/{key}."""

    def __init__(self, root: str, media_base_url: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_base_url = media_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        # Prevent path traversal: only allow the basename
        safe = Path(key).name
        return self.root / safe

    def save(self, data: bytes, ext: str = "wav") -> str:
        key = f"{uuid.uuid4().hex}.{ext.lstrip('.')}"
        self._path(key).write_bytes(data)
        return key

    def load(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def url(self, key: str) -> str:
        return f"{self.media_base_url}/{key}"

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()


class MinIOStorage(StorageBackend):
    """Prod storage: S3-compatible MinIO. Requires boto3 (already in deps).

    Implemented but not exercised in dev/test. Swapping to this backend only
    requires setting storage_backend="minio" and the s3_* env vars.
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str,
                 bucket: str, secure: bool):
        import boto3  # imported lazily so dev doesn't require it warm

        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config={"addressing_style": "path"},
        )
        # Ensure bucket exists
        try:
            self.s3.head_bucket(Bucket=bucket)
        except Exception:
            self.s3.create_bucket(Bucket=bucket)

    def save(self, data: bytes, ext: str = "wav") -> str:
        key = f"{uuid.uuid4().hex}.{ext.lstrip('.')}"
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def load(self, key: str) -> bytes:
        resp = self.s3.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].read()

    def url(self, key: str) -> str:
        # In prod, MinIO serves directly; presign a long-lived URL (7 days,
        # the boto3 maximum). For public-read buckets, you can instead
        # construct the URL manually: f"{self.endpoint}/{self.bucket}/{key}"
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=604800,  # 7 days (boto3 requires a positive integer)
        )

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


def _build_storage() -> StorageBackend:
    if settings.storage_backend == "minio":
        return MinIOStorage(
            endpoint=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            secure=settings.s3_secure,
        )
    return FilesystemStorage(
        root=settings.storage_fs_root,
        media_base_url=settings.media_base_url,
    )


storage = _build_storage()