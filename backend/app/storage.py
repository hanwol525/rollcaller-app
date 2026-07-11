"""Single interface for all blob reads/writes.

    storage.save(data: bytes) -> key
    storage.load(key: str) -> bytes
    storage.url(key: str) -> str
    storage.delete(key: str) -> None

Swap backends by changing `settings.storage_backend` in config.py ("filesystem",
"minio", or "gcs"). Only this file needs to change to introduce a new backend.
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
        from botocore.config import Config

        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
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
        # Serve through the backend's /media/{key} endpoint so MinIO
        # doesn't need to be exposed externally.
        return f"/media/{key}"

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)


class GCSStorage(StorageBackend):
    """Prod storage: Google Cloud Storage via Workload Identity (ADC).

    No credentials in env — the google-cloud-storage client picks up
    Application Default Credentials from the GKE metadata server
    (Workload Identity) automatically. The pod's GSA needs
    roles/storage.objectAdmin on the bucket.
    """

    def __init__(self, bucket: str):
        from google.cloud import storage  # imported lazily

        self.bucket_name = bucket
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket)

    def save(self, data: bytes, ext: str = "wav") -> str:
        key = f"{uuid.uuid4().hex}.{ext.lstrip('.')}"
        blob = self.bucket.blob(key)
        blob.upload_from_string(data, content_type=f"audio/{ext}")
        return key

    def load(self, key: str) -> bytes:
        return self.bucket.blob(key).download_as_bytes()

    def url(self, key: str) -> str:
        from datetime import timedelta

        blob = self.bucket.blob(key)
        return blob.generate_signed_url(
            expiration=timedelta(days=7),
            method="GET",
        )

    def delete(self, key: str) -> None:
        self.bucket.blob(key).delete()


def _build_storage() -> StorageBackend:
    if settings.storage_backend == "gcs":
        return GCSStorage(bucket=settings.gcs_bucket)
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