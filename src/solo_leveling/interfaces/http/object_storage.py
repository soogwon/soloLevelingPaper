"""S3 호환 비공개 저장소의 제한된 presigned URL 어댑터."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import uuid4


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    file_size: int
    content_type: str
    sha256_hex: str
    pdf_header_valid: bool = True


class UnavailableObjectStorage:
    def prepare_upload(self, *args, **kwargs):
        raise RuntimeError("object_storage_unavailable")

    def inspect_upload(self, *args, **kwargs):
        raise RuntimeError("object_storage_unavailable")

    def finalize_upload(self, *args, **kwargs):
        raise RuntimeError("object_storage_unavailable")

    def delete_upload(self, *args, **kwargs):
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
        expires = int((reservation.expires_at - datetime.now(reservation.expires_at.tzinfo)).total_seconds())
        if expires < 1:
            raise RuntimeError("upload_reservation_expired")
        params = {
            "Bucket": self._bucket, "Key": reservation.object_key,
            "ContentLength": file_size, "ContentType": content_type,
        }
        url = self._client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires, HttpMethod="PUT")
        return PresignedUpload(url, "PUT", {
            "Content-Length": str(file_size), "Content-Type": content_type,
        }, reservation.expires_at)

    def inspect_upload(self, upload) -> StoredObject | None:
        return self._inspect_key(upload.object_key, upload.file_size)

    def _inspect_key(self, object_key: str, expected_size: int) -> StoredObject | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
        except Exception as exc:
            status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return None
            raise RuntimeError("object_read_failed") from None
        body = response["Body"]
        digest, observed = sha256(), 0
        header = b""
        try:
            while chunk := body.read(1024 * 1024):
                header += chunk[:5-len(header)]
                digest.update(chunk)
                observed += len(chunk)
                if observed > expected_size:
                    break
        except Exception:
            raise RuntimeError("object_read_failed") from None
        finally:
            body.close()
        declared = int(response["ContentLength"])
        if observed <= expected_size and observed != declared:
            raise RuntimeError("object_length_changed_during_read")
        return StoredObject(object_key, observed, response.get("ContentType", ""), digest.hexdigest(),
                            header == b"%PDF-")

    def finalize_upload(self, upload) -> StoredObject:
        prefix = upload.object_key.rsplit("/", 1)[0]
        final_key = f"{prefix}/files/{uuid4()}.pdf"
        try:
            self._client.copy_object(
                Bucket=self._bucket, Key=final_key,
                CopySource={"Bucket": self._bucket, "Key": upload.object_key},
                MetadataDirective="COPY",
            )
        except Exception:
            raise RuntimeError("object_copy_failed") from None
        try:
            finalized = self._inspect_key(final_key, upload.file_size)
        except Exception:
            try:
                self.delete_upload(final_key)
            except Exception:
                pass
            raise RuntimeError("final_object_read_failed") from None
        if finalized is None:
            raise RuntimeError("final_object_missing")
        return finalized

    def delete_upload(self, object_key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=object_key)

