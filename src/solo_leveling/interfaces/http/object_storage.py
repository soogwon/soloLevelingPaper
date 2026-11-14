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
        client = boto3.client(
            "s3", endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name=settings.region,
        )
        return cls(client, settings.bucket)

    def prepare_upload(self, reservation, *, content_type: str, sha256_hex: str) -> PresignedUpload:
        checksum = base64.b64encode(bytes.fromhex(sha256_hex)).decode("ascii")
        expires = max(1, int((reservation.expires_at - datetime.now(reservation.expires_at.tzinfo)).total_seconds()))
        params = {
            "Bucket": self._bucket, "Key": reservation.object_key,
            "ContentType": content_type, "ChecksumSHA256": checksum,
        }
        url = self._client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires, HttpMethod="PUT")
        return PresignedUpload(url, "PUT", {
            "Content-Type": content_type,
            "x-amz-checksum-sha256": checksum,
        }, reservation.expires_at)

