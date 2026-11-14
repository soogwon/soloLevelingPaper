"""S3 호환 비공개 저장소의 제한된 presigned URL 어댑터."""

from dataclasses import dataclass
from datetime import datetime
import base64


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


class UnavailableObjectStorage:
    def prepare_upload(self, *args, **kwargs):
        raise RuntimeError("object_storage_unavailable")


class S3ObjectStorage:
    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    @classmethod
    def create(cls, settings):
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "s3", endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name=settings.region, config=Config(signature_version="s3v4"),
        )
        return cls(client, settings.bucket)

    def prepare_upload(self, reservation, *, file_size: int, content_type: str, sha256_hex: str) -> PresignedUpload:
        checksum = base64.b64encode(bytes.fromhex(sha256_hex)).decode("ascii")
        expires = int((reservation.expires_at - datetime.now(reservation.expires_at.tzinfo)).total_seconds())
        if expires < 1:
            raise RuntimeError("upload_reservation_expired")
        params = {
            "Bucket": self._bucket, "Key": reservation.object_key,
            "ContentLength": file_size, "ContentType": content_type, "ChecksumSHA256": checksum,
        }
        url = self._client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires, HttpMethod="PUT")
        return PresignedUpload(url, "PUT", {
            "Content-Length": str(file_size), "Content-Type": content_type,
            "x-amz-checksum-sha256": checksum,
        }, reservation.expires_at)

