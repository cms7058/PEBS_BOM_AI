"""Object storage abstraction.

- `local`: plain filesystem under settings.storage_local_path (zero deps)
- `minio`: S3-compatible server (only imported if selected)
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.config import settings


class ObjectStore(Protocol):
    def ensure_bucket(self) -> None: ...
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...


class LocalStore:
    def __init__(self, root: str) -> None:
        self._root = Path(root).resolve()

    def ensure_bucket(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return (self._root / key).read_bytes()


class MinioStore:
    def __init__(self) -> None:
        from io import BytesIO

        from minio import Minio

        self._BytesIO = BytesIO
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password,
            secure=settings.minio_secure,
        )
        self._bucket = settings.minio_bucket

    def ensure_bucket(self) -> None:
        from minio.error import S3Error

        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except S3Error:
            pass

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            self._bucket,
            key,
            self._BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def get(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()


def _make_store() -> ObjectStore:
    if settings.storage_backend == "minio":
        return MinioStore()
    return LocalStore(settings.storage_local_path)


store: ObjectStore = _make_store()
