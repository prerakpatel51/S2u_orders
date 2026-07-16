import gzip
import hashlib
import io
import json
import mimetypes
import re
import tempfile
import zipfile
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone


class DeliveryStorageError(RuntimeError):
    pass


def is_configured():
    return all(
        [
            settings.DELIVERY_BUCKET_ENDPOINT,
            settings.DELIVERY_BUCKET_NAME,
            settings.DELIVERY_BUCKET_ACCESS_KEY_ID,
            settings.DELIVERY_BUCKET_SECRET_ACCESS_KEY,
        ]
    )


def dr_is_configured():
    configured = all(
        [
            settings.DELIVERY_DR_BUCKET_ENDPOINT,
            settings.DELIVERY_DR_BUCKET_NAME,
            settings.DELIVERY_DR_BUCKET_ACCESS_KEY_ID,
            settings.DELIVERY_DR_BUCKET_SECRET_ACCESS_KEY,
        ]
    )
    return configured and (
        settings.DELIVERY_DR_BUCKET_ENDPOINT.rstrip("/"), settings.DELIVERY_DR_BUCKET_NAME
    ) != (settings.DELIVERY_BUCKET_ENDPOINT.rstrip("/"), settings.DELIVERY_BUCKET_NAME)


def _client(endpoint, region, access_key, secret_key):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def client():
    if not is_configured():
        raise DeliveryStorageError("Delivery photo storage is not configured yet.")
    return _client(
        settings.DELIVERY_BUCKET_ENDPOINT,
        settings.DELIVERY_BUCKET_REGION,
        settings.DELIVERY_BUCKET_ACCESS_KEY_ID,
        settings.DELIVERY_BUCKET_SECRET_ACCESS_KEY,
    )


def dr_client():
    if not dr_is_configured():
        raise DeliveryStorageError("Delivery disaster-recovery storage is not configured yet.")
    return _client(
        settings.DELIVERY_DR_BUCKET_ENDPOINT,
        settings.DELIVERY_DR_BUCKET_REGION,
        settings.DELIVERY_DR_BUCKET_ACCESS_KEY_ID,
        settings.DELIVERY_DR_BUCKET_SECRET_ACCESS_KEY,
    )


def safe_filename(filename, content_type=""):
    raw = PurePosixPath(str(filename or "photo").replace("\\", "/")).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")[:180] or "photo"
    if "." not in stem:
        stem += mimetypes.guess_extension(content_type) or ".bin"
    return stem


def asset_key(delivery, category, asset_uuid, filename, content_type=""):
    return f"{delivery.storage_prefix}/{category}/{asset_uuid}-{safe_filename(filename, content_type)}"


def presigned_upload(object_key, content_type, size_bytes):
    params = {
        "Bucket": settings.DELIVERY_BUCKET_NAME,
        "Key": object_key,
        "ContentType": content_type,
        "ContentLength": size_bytes,
    }
    return client().generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=settings.DELIVERY_SIGNED_URL_SECONDS,
        HttpMethod="PUT",
    )


def confirm_object(object_key):
    try:
        return client().head_object(Bucket=settings.DELIVERY_BUCKET_NAME, Key=object_key)
    except Exception as exc:
        raise DeliveryStorageError("The uploaded file could not be confirmed.") from exc


def validate_image_object(object_key, content_type):
    """Verify the stored bytes match the declared image type before accepting proof."""
    try:
        response = client().get_object(
            Bucket=settings.DELIVERY_BUCKET_NAME, Key=object_key, Range="bytes=0-31"
        )
        header = response["Body"].read(32)
    except Exception as exc:
        raise DeliveryStorageError("The uploaded image could not be validated.") from exc
    signatures = {
        "image/jpeg": lambda value: value.startswith(b"\xff\xd8\xff"),
        "image/png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": lambda value: value.startswith(b"RIFF") and value[8:12] == b"WEBP",
    }
    if content_type not in signatures or not signatures[content_type](header):
        raise DeliveryStorageError("The uploaded file is not a valid supported image.")
    return True


def signed_download(object_key, *, filename=None, content_type=None):
    params = {"Bucket": settings.DELIVERY_BUCKET_NAME, "Key": object_key}
    if filename:
        params["ResponseContentDisposition"] = f'inline; filename="{safe_filename(filename)}"'
    if content_type:
        params["ResponseContentType"] = content_type
    return client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=settings.DELIVERY_SIGNED_URL_SECONDS
    )


def signed_dr_download(object_key, *, filename=None, content_type=None):
    params = {"Bucket": settings.DELIVERY_DR_BUCKET_NAME, "Key": object_key}
    if filename:
        params["ResponseContentDisposition"] = f'inline; filename="{safe_filename(filename)}"'
    if content_type:
        params["ResponseContentType"] = content_type
    return dr_client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=settings.DELIVERY_SIGNED_URL_SECONDS
    )


def signed_asset_download(asset):
    """Prefer the live object and automatically fail over to its verified DR copy."""
    try:
        client().head_object(Bucket=settings.DELIVERY_BUCKET_NAME, Key=asset.object_key)
        return signed_download(
            asset.object_key, filename=asset.original_filename, content_type=asset.content_type
        )
    except Exception as primary_error:
        replica = getattr(asset, "replica", None)
        if replica and replica.status == "verified" and dr_is_configured():
            try:
                dr_client().head_object(
                    Bucket=settings.DELIVERY_DR_BUCKET_NAME, Key=replica.object_key
                )
                return signed_dr_download(
                    replica.object_key,
                    filename=asset.original_filename,
                    content_type=asset.content_type,
                )
            except Exception:
                pass
        raise DeliveryStorageError(
            "This file is currently unavailable in both live and recovery storage."
        ) from primary_error


def put_bytes(object_key, data, content_type, *, metadata=None):
    body = data.encode("utf-8") if isinstance(data, str) else data
    client().put_object(
        Bucket=settings.DELIVERY_BUCKET_NAME,
        Key=object_key,
        Body=body,
        ContentType=content_type,
        Metadata=metadata or {"source": "s2u-delivery-proof"},
    )
    return len(body), hashlib.sha256(body).hexdigest()


def put_dr_bytes(object_key, data, content_type, *, metadata=None):
    body = data.encode("utf-8") if isinstance(data, str) else data
    dr_client().put_object(
        Bucket=settings.DELIVERY_DR_BUCKET_NAME,
        Key=object_key,
        Body=body,
        ContentType=content_type,
        Metadata=metadata or {"source": "s2u-delivery-disaster-recovery"},
    )
    return len(body), hashlib.sha256(body).hexdigest()


def get_bytes(object_key, *, allow_dr=True):
    try:
        response = client().get_object(Bucket=settings.DELIVERY_BUCKET_NAME, Key=object_key)
        return response["Body"].read()
    except Exception as primary_error:
        if allow_dr and dr_is_configured():
            try:
                response = dr_client().get_object(
                    Bucket=settings.DELIVERY_DR_BUCKET_NAME, Key=object_key
                )
                return response["Body"].read()
            except Exception:
                pass
        raise DeliveryStorageError(
            f"Object {object_key} is unavailable in live and recovery storage."
        ) from primary_error


def delete_object(object_key):
    client().delete_object(Bucket=settings.DELIVERY_BUCKET_NAME, Key=object_key)


def queue_asset_replication(asset_id):
    """Queue only after the database row is committed and visible to the worker."""
    from .tasks import replicate_delivery_asset_task

    # Redis downtime must not turn a successfully confirmed upload into a user
    # error; the 15-minute reconciliation job will catch any missed enqueue.
    transaction.on_commit(lambda: replicate_delivery_asset_task.delay(asset_id), robust=True)


def replicate_asset(asset):
    """Copy and cryptographically verify one immutable primary object in the DR bucket."""
    from .models import DeliveryAssetReplica

    if not dr_is_configured():
        raise DeliveryStorageError("Delivery disaster-recovery storage is not configured yet.")
    replica, _ = DeliveryAssetReplica.objects.get_or_create(
        asset=asset, defaults={"object_key": asset.object_key}
    )
    replica.object_key = asset.object_key
    replica.status = DeliveryAssetReplica.Status.COPYING
    replica.attempts += 1
    replica.last_attempt_at = timezone.now()
    replica.error_message = ""
    replica.save()
    try:
        source = client().get_object(Bucket=settings.DELIVERY_BUCKET_NAME, Key=asset.object_key)
        body = source["Body"].read()
        checksum = hashlib.sha256(body).hexdigest()
        if len(body) != asset.size_bytes:
            raise DeliveryStorageError("The primary object size no longer matches its asset record.")
        if asset.checksum_sha256 and asset.checksum_sha256.lower() != checksum:
            raise DeliveryStorageError("The primary object checksum does not match its upload record.")
        dr_client().put_object(
            Bucket=settings.DELIVERY_DR_BUCKET_NAME,
            Key=asset.object_key,
            Body=body,
            ContentType=asset.content_type,
            Metadata={
                "sha256": checksum,
                "delivery-id": str(asset.delivery.uuid),
                "asset-id": str(asset.uuid),
                "source": "s2u-primary-delivery-bucket",
            },
        )
        head = dr_client().head_object(
            Bucket=settings.DELIVERY_DR_BUCKET_NAME, Key=asset.object_key
        )
        if int(head.get("ContentLength", -1)) != len(body):
            raise DeliveryStorageError("The recovery copy size verification failed.")
        if head.get("Metadata", {}).get("sha256") != checksum:
            raise DeliveryStorageError("The recovery copy checksum verification failed.")
        now = timezone.now()
        replica.status = DeliveryAssetReplica.Status.VERIFIED
        replica.size_bytes = len(body)
        replica.checksum_sha256 = checksum
        replica.replicated_at = now
        replica.verified_at = now
        replica.error_message = ""
        replica.save()
        if asset.checksum_sha256 != checksum:
            asset.checksum_sha256 = checksum
            asset.save(update_fields=["checksum_sha256", "updated_at"])
        return replica
    except Exception as exc:
        replica.status = DeliveryAssetReplica.Status.FAILED
        replica.error_message = str(exc)[:2000]
        replica.save(update_fields=["status", "error_message", "updated_at"])
        if isinstance(exc, DeliveryStorageError):
            raise
        raise DeliveryStorageError("The delivery file could not be replicated.") from exc


def verify_asset_replica(replica):
    """Recheck that a DR object is present and still matches the verified catalog."""
    from .models import DeliveryAssetReplica

    replica.last_attempt_at = timezone.now()
    replica.attempts += 1
    try:
        head = dr_client().head_object(
            Bucket=settings.DELIVERY_DR_BUCKET_NAME, Key=replica.object_key
        )
        matches = (
            int(head.get("ContentLength", -1)) == replica.size_bytes
            and head.get("Metadata", {}).get("sha256") == replica.checksum_sha256
        )
        if not matches:
            raise DeliveryStorageError("The recovery object failed its integrity check.")
        replica.status = DeliveryAssetReplica.Status.VERIFIED
        replica.verified_at = timezone.now()
        replica.error_message = ""
    except Exception as exc:
        replica.status = DeliveryAssetReplica.Status.MISSING
        replica.error_message = str(exc)[:2000]
    replica.save()
    return replica


def notes_snapshot(delivery):
    from .models import DeliveryAsset

    delivered = timezone.localtime(delivery.delivered_at)
    submitted = timezone.localtime(delivery.submitted_at) if delivery.submitted_at else None
    reviewed = timezone.localtime(delivery.reviewed_at) if delivery.reviewed_at else None
    keywords = ", ".join(delivery.keywords.values_list("name", flat=True)) or "None"
    text = "\n".join(
        [
            "S2U DELIVERY PROOF NOTES",
            "=" * 28,
            f"Delivery ID: {delivery.uuid}",
            f"Store: {delivery.store.number} - {delivery.store.name}",
            f"Delivered: {delivered:%Y-%m-%d %I:%M %p %Z}",
            f"Reference: {delivery.reference_number or 'Not provided'}",
            f"Submitted by: {delivery.submitted_by.get_username()}",
            f"Submitted: {submitted:%Y-%m-%d %I:%M %p %Z}" if submitted else "Submitted: Draft",
            f"Status: {delivery.get_status_display()}",
            f"Expected cases: {delivery.expected_cases if delivery.expected_cases is not None else 'Not provided'}",
            f"Delivered cases: {delivery.delivered_cases if delivery.delivered_cases is not None else 'Not provided'}",
            f"Damaged cases: {delivery.damaged_cases}",
            "",
            "DELIVERY NOTES",
            delivery.general_notes or "None",
            "",
            "ISSUE NOTES",
            delivery.issue_notes or "None",
            "",
            "ADMIN NOTES",
            delivery.admin_notes or "None",
            "",
            f"Keywords: {keywords}",
            f"Reviewed by: {delivery.reviewed_by.get_username() if delivery.reviewed_by else 'Not reviewed'}",
            f"Reviewed: {reviewed:%Y-%m-%d %I:%M %p %Z}" if reviewed else "Reviewed: Not reviewed",
            "",
        ]
    )
    snapshot_uuid = DeliveryAsset().uuid
    stamp = timezone.localtime()
    key = (
        f"{delivery.storage_prefix}/notes/"
        f"delivery-notes-{stamp:%Y%m%d-%H%M%S-%f}-{snapshot_uuid}.txt"
    )
    size, checksum = put_bytes(key, text, "text/plain; charset=utf-8")
    position = delivery.assets.filter(category=DeliveryAsset.Category.NOTES).count()
    asset = DeliveryAsset.objects.create(
        uuid=snapshot_uuid,
        delivery=delivery,
        category=DeliveryAsset.Category.NOTES,
        object_key=key,
        original_filename=f"delivery-notes-{stamp:%Y%m%d-%H%M%S}.txt",
        content_type="text/plain; charset=utf-8",
        size_bytes=size,
        checksum_sha256=checksum,
        upload_status=DeliveryAsset.UploadStatus.UPLOADED,
        uploaded_by=delivery.reviewed_by or delivery.submitted_by,
        position=position,
    )
    queue_asset_replication(asset.pk)
    return asset


def build_delivery_zip(delivery):
    manifest = {
        "delivery_id": str(delivery.uuid),
        "store": {"number": delivery.store.number, "name": delivery.store.name},
        "delivered_at": delivery.delivered_at,
        "reference_number": delivery.reference_number,
        "status": delivery.status,
        "general_notes": delivery.general_notes,
        "issue_notes": delivery.issue_notes,
        "admin_notes": delivery.admin_notes,
        "keywords": list(delivery.keywords.values_list("name", flat=True)),
        "submitted_by": delivery.submitted_by.get_username(),
        "submitted_at": delivery.submitted_at,
        "reviewed_by": delivery.reviewed_by.get_username() if delivery.reviewed_by else None,
        "reviewed_at": delivery.reviewed_at,
    }
    output = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json", json.dumps(manifest, cls=DjangoJSONEncoder, indent=2)
        )
        for asset in delivery.assets.filter(upload_status="uploaded"):
            relative = asset.object_key.removeprefix(delivery.storage_prefix + "/")
            archive.writestr(relative, get_bytes(asset.object_key))
    output.seek(0)
    return output


def create_metadata_backup(backup):
    from .models import Delivery

    deliveries = Delivery.objects.select_related(
        "store", "submitted_by", "reviewed_by"
    ).prefetch_related("keywords", "assets__replica", "events__actor")
    rows = []
    asset_count = 0
    for delivery in deliveries.iterator(chunk_size=100):
        assets = list(delivery.assets.all())
        asset_count += len(assets)
        rows.append(
            {
                "uuid": delivery.uuid,
                "store": {"number": delivery.store.number, "name": delivery.store.name},
                "delivered_at": delivery.delivered_at,
                "reference_number": delivery.reference_number,
                "general_notes": delivery.general_notes,
                "issue_notes": delivery.issue_notes,
                "expected_cases": delivery.expected_cases,
                "delivered_cases": delivery.delivered_cases,
                "damaged_cases": delivery.damaged_cases,
                "status": delivery.status,
                "submitted_by": delivery.submitted_by.get_username(),
                "submitted_at": delivery.submitted_at,
                "reviewed_by": delivery.reviewed_by.get_username() if delivery.reviewed_by else None,
                "reviewed_at": delivery.reviewed_at,
                "admin_notes": delivery.admin_notes,
                "keywords": list(delivery.keywords.values_list("name", flat=True)),
                "assets": [
                    {
                        "uuid": item.uuid,
                        "category": item.category,
                        "object_key": item.object_key,
                        "filename": item.original_filename,
                        "content_type": item.content_type,
                        "size_bytes": item.size_bytes,
                        "checksum_sha256": item.checksum_sha256,
                        "status": item.upload_status,
                        "replica": {
                            "status": item.replica.status,
                            "object_key": item.replica.object_key,
                            "checksum_sha256": item.replica.checksum_sha256,
                            "verified_at": item.replica.verified_at,
                        }
                        if hasattr(item, "replica")
                        else None,
                    }
                    for item in assets
                ],
                "events": [
                    {
                        "type": event.event_type,
                        "actor": event.actor.get_username() if event.actor else None,
                        "message": event.message,
                        "details": event.details,
                        "created_at": event.created_at,
                    }
                    for event in delivery.events.all()
                ],
            }
        )
    document = {
        "format": "s2u-delivery-metadata-backup-v1",
        "created_at": timezone.now(),
        "delivery_count": len(rows),
        "asset_count": asset_count,
        "deliveries": rows,
    }
    raw = json.dumps(document, cls=DjangoJSONEncoder, indent=2).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9)
    stamp = timezone.localtime()
    key = f"backups/delivery-metadata/{stamp:%Y/%m/%d}/delivery-metadata-{stamp:%Y%m%d-%H%M%S}-{backup.uuid}.json.gz"
    size, _ = put_dr_bytes(
        key,
        compressed,
        "application/gzip",
        metadata={"backup-format": "s2u-delivery-v1", "storage-role": "disaster-recovery"},
    )
    return key, len(rows), asset_count, size
