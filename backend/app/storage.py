from fastapi import Depends, UploadFile

from app.config import settings


class Storage:
    def __init__(self) -> None:
        from aioboto3 import Session

        self._session = Session()
        self._endpoint = settings.S3_ENDPOINT
        self._access_key = settings.S3_ACCESS_KEY
        self._secret_key = settings.S3_SECRET_KEY
        self._bucket = settings.S3_BUCKET

    async def upload(self, key: str, file: UploadFile) -> str:
        async with self._session.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        ) as client:
            await client.ensure_bucket_exists(self._bucket)
            await client.upload_fileobj(file.file, self._bucket, key)
        return f"{self._endpoint}/{self._bucket}/{key}"


storage = Storage()
