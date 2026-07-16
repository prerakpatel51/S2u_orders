import csv
import re
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from .delivery_storage import (
    DeliveryStorageError,
    asset_key,
    build_delivery_zip,
    confirm_object,
    create_metadata_backup,
    dr_is_configured,
    is_configured,
    notes_snapshot,
    presigned_upload,
    queue_asset_replication,
    safe_filename,
    signed_asset_download,
    signed_dr_download,
    validate_image_object,
)
from .models import (
    Delivery,
    DeliveryAsset,
    DeliveryAssetReplica,
    DeliveryBackup,
    DeliveryEvent,
    DeliveryKeyword,
    Store,
)


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
REVIEW_STATUSES = {
    Delivery.Status.UNDER_REVIEW,
    Delivery.Status.NEEDS_INFO,
    Delivery.Status.ISSUE_FOUND,
    Delivery.Status.VERIFIED,
    Delivery.Status.RESOLVED,
}


def is_delivery_admin(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return is_delivery_admin(self.request.user)


class IsDeliveryAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_delivery_admin(request.user)


class DeliveryDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "orders/deliveries.html"


class DeliveryCreateView(LoginRequiredMixin, TemplateView):
    template_name = "orders/delivery_create.html"


class DeliveryReviewView(LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = "orders/delivery_review.html"


class DeliveryDetailView(LoginRequiredMixin, TemplateView):
    template_name = "orders/delivery_detail.html"

    def dispatch(self, request, *args, **kwargs):
        delivery = get_object_or_404(Delivery, uuid=kwargs["delivery_uuid"])
        if not is_delivery_admin(request.user) and delivery.submitted_by_id != request.user.id:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        self.delivery = delivery
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["delivery"] = self.delivery
        context["delivery_admin"] = is_delivery_admin(self.request.user)
        return context


def parse_delivered_at(value):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        return timezone.now()
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def optional_count(value, field, *, default=None):
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number.")
    if number < 0:
        raise ValueError(f"{field} cannot be negative.")
    return number


def user_label(user):
    if not user:
        return None
    return user.get_full_name().strip() or user.get_username()


def serialize_asset(asset):
    replica = getattr(asset, "replica", None)
    return {
        "uuid": str(asset.uuid),
        "category": asset.category,
        "filename": asset.original_filename,
        "content_type": asset.content_type,
        "size_bytes": asset.size_bytes,
        "checksum_sha256": asset.checksum_sha256,
        "status": asset.upload_status,
        "position": asset.position,
        "replica_status": replica.status if replica else "pending",
        "replicated_at": replica.replicated_at if replica else None,
        "replica_verified_at": replica.verified_at if replica else None,
        "view_url": f"/api/delivery-assets/{asset.uuid}/view/",
    }


def serialize_event(event):
    return {
        "type": event.event_type,
        "label": event.get_event_type_display(),
        "message": event.message,
        "actor": user_label(event.actor),
        "details": event.details,
        "created_at": event.created_at,
    }


def delivery_issue_flag(delivery, assets=None):
    assets = assets if assets is not None else delivery.assets.all()
    has_damage = any(
        asset.category == DeliveryAsset.Category.DAMAGE
        and asset.upload_status == DeliveryAsset.UploadStatus.UPLOADED
        for asset in assets
    )
    short = (
        delivery.expected_cases is not None
        and delivery.delivered_cases is not None
        and delivery.delivered_cases < delivery.expected_cases
    )
    return bool(delivery.issue_notes.strip() or delivery.damaged_cases or has_damage or short)


def serialize_delivery(delivery, request, *, detail=False):
    assets = list(delivery.assets.all())
    counts = {category: 0 for category, _ in DeliveryAsset.Category.choices}
    for asset in assets:
        if asset.upload_status == DeliveryAsset.UploadStatus.UPLOADED:
            counts[asset.category] += 1
    data = {
        "uuid": str(delivery.uuid),
        "store": {
            "id": delivery.store_id,
            "number": delivery.store.number,
            "name": delivery.store.name,
        },
        "delivered_at": delivery.delivered_at,
        "reference_number": delivery.reference_number,
        "general_notes": delivery.general_notes,
        "issue_notes": delivery.issue_notes,
        "expected_cases": delivery.expected_cases,
        "delivered_cases": delivery.delivered_cases,
        "damaged_cases": delivery.damaged_cases,
        "status": delivery.status,
        "status_label": delivery.get_status_display(),
        "has_issue": delivery_issue_flag(delivery, assets),
        "submitted_by": user_label(delivery.submitted_by),
        "submitted_at": delivery.submitted_at,
        "reviewed_by": user_label(delivery.reviewed_by),
        "reviewed_at": delivery.reviewed_at,
        "admin_notes": delivery.admin_notes if is_delivery_admin(request.user) else "",
        "keywords": [keyword.name for keyword in delivery.keywords.all()],
        "asset_counts": counts,
        "created_at": delivery.created_at,
        "updated_at": delivery.updated_at,
        "can_edit": delivery.status in {Delivery.Status.DRAFT, Delivery.Status.NEEDS_INFO}
        and (delivery.submitted_by_id == request.user.id or is_delivery_admin(request.user)),
        "detail_url": f"/deliveries/{delivery.uuid}/",
    }
    if detail:
        data["assets"] = [serialize_asset(asset) for asset in assets]
        data["events"] = [serialize_event(event) for event in delivery.events.all()]
        data["storage_prefix"] = delivery.storage_prefix if is_delivery_admin(request.user) else None
        data["download_url"] = (
            f"/api/deliveries/{delivery.uuid}/download.zip/"
            if is_delivery_admin(request.user)
            else None
        )
    return data


def delivery_queryset(*, include_events=False):
    related = ["keywords", "assets__replica"]
    if include_events:
        related.append("events__actor")
    return Delivery.objects.select_related("store", "submitted_by", "reviewed_by").prefetch_related(
        *related
    )


def get_visible_delivery(request, delivery_uuid):
    delivery = get_object_or_404(delivery_queryset(include_events=True), uuid=delivery_uuid)
    if not is_delivery_admin(request.user) and delivery.submitted_by_id != request.user.id:
        from rest_framework.exceptions import PermissionDenied

        raise PermissionDenied("You do not have access to this delivery.")
    return delivery


class DeliveryListAPIView(APIView):
    def get(self, request):
        deliveries = delivery_queryset()
        if not is_delivery_admin(request.user):
            deliveries = deliveries.filter(submitted_by=request.user)
        query = request.query_params.get("q", "").strip()
        if query:
            deliveries = deliveries.filter(
                Q(reference_number__icontains=query)
                | Q(store__number__icontains=query)
                | Q(store__name__icontains=query)
                | Q(general_notes__icontains=query)
                | Q(issue_notes__icontains=query)
                | Q(admin_notes__icontains=query)
                | Q(keywords__name__icontains=query)
                | Q(submitted_by__username__icontains=query)
            ).distinct()
        if request.query_params.get("store"):
            deliveries = deliveries.filter(store_id=request.query_params["store"])
        if request.query_params.get("status"):
            deliveries = deliveries.filter(status=request.query_params["status"])
        if request.query_params.get("issues") == "1":
            deliveries = deliveries.filter(
                Q(issue_notes__gt="") | Q(damaged_cases__gt=0) | Q(assets__category="damage")
            ).distinct()
        if request.query_params.get("date_from"):
            deliveries = deliveries.filter(delivered_at__date__gte=request.query_params["date_from"])
        if request.query_params.get("date_to"):
            deliveries = deliveries.filter(delivered_at__date__lte=request.query_params["date_to"])
        rows = list(deliveries[:200])
        return Response(
            {
                "deliveries": [serialize_delivery(row, request) for row in rows],
                "storage_configured": is_configured(),
                "statuses": [
                    {"value": value, "label": label} for value, label in Delivery.Status.choices
                ],
            }
        )

    def post(self, request):
        store = get_object_or_404(Store, pk=request.data.get("store_id"), active=True)
        try:
            expected = optional_count(request.data.get("expected_cases"), "Expected cases")
            delivered = optional_count(request.data.get("delivered_cases"), "Delivered cases")
            damaged = optional_count(request.data.get("damaged_cases"), "Damaged cases", default=0)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        delivery = Delivery.objects.create(
            store=store,
            delivered_at=parse_delivered_at(request.data.get("delivered_at")),
            reference_number=str(request.data.get("reference_number", "")).strip()[:120],
            general_notes=str(request.data.get("general_notes", "")).strip(),
            issue_notes=str(request.data.get("issue_notes", "")).strip(),
            expected_cases=expected,
            delivered_cases=delivered,
            damaged_cases=damaged,
            submitted_by=request.user,
        )
        DeliveryEvent.objects.create(
            delivery=delivery,
            event_type=DeliveryEvent.EventType.CREATED,
            actor=request.user,
            message="Delivery draft created",
        )
        delivery = get_visible_delivery(request, delivery.uuid)
        return Response(serialize_delivery(delivery, request, detail=True), status=201)


class DeliveryDetailAPIView(APIView):
    def get(self, request, delivery_uuid):
        return Response(serialize_delivery(get_visible_delivery(request, delivery_uuid), request, detail=True))

    def patch(self, request, delivery_uuid):
        delivery = get_visible_delivery(request, delivery_uuid)
        if delivery.status not in {Delivery.Status.DRAFT, Delivery.Status.NEEDS_INFO} and not is_delivery_admin(request.user):
            return Response({"detail": "Submitted deliveries cannot be edited."}, status=409)
        before = {}
        for field in ("reference_number", "general_notes", "issue_notes"):
            if field in request.data:
                before[field] = getattr(delivery, field)
                limit = 120 if field == "reference_number" else None
                value = str(request.data[field] or "").strip()
                setattr(delivery, field, value[:limit] if limit else value)
        has_assets = delivery.assets.exclude(category=DeliveryAsset.Category.NOTES).exists()
        if "store_id" in request.data and str(request.data["store_id"]) != str(delivery.store_id):
            if has_assets:
                return Response(
                    {"detail": "The store cannot be changed after photos have been uploaded."},
                    status=409,
                )
            before["store"] = delivery.store_id
            delivery.store = get_object_or_404(Store, pk=request.data["store_id"], active=True)
        if "delivered_at" in request.data:
            proposed = parse_delivered_at(request.data["delivered_at"])
            if has_assets and proposed != delivery.delivered_at:
                return Response(
                    {"detail": "The delivery date cannot be changed after photos have been uploaded."},
                    status=409,
                )
            before["delivered_at"] = delivery.delivered_at.isoformat()
            delivery.delivered_at = proposed
        try:
            for field, label in (
                ("expected_cases", "Expected cases"),
                ("delivered_cases", "Delivered cases"),
                ("damaged_cases", "Damaged cases"),
            ):
                if field in request.data:
                    before[field] = getattr(delivery, field)
                    setattr(
                        delivery,
                        field,
                        optional_count(
                            request.data[field], label, default=0 if field == "damaged_cases" else None
                        ),
                    )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        delivery.save()
        DeliveryEvent.objects.create(
            delivery=delivery,
            event_type=DeliveryEvent.EventType.UPDATED,
            actor=request.user,
            message="Delivery information updated",
            details={"changed_fields": list(before)},
        )
        return Response(serialize_delivery(get_visible_delivery(request, delivery_uuid), request, detail=True))


class DeliveryAssetPresignAPIView(APIView):
    def post(self, request, delivery_uuid):
        delivery = get_visible_delivery(request, delivery_uuid)
        if delivery.status not in {Delivery.Status.DRAFT, Delivery.Status.NEEDS_INFO} and not is_delivery_admin(request.user):
            return Response({"detail": "Photos cannot be added after submission."}, status=409)
        category = str(request.data.get("category", ""))
        allowed_categories = {
            DeliveryAsset.Category.INVOICE,
            DeliveryAsset.Category.BOXES,
            DeliveryAsset.Category.DAMAGE,
        }
        if category not in allowed_categories:
            return Response({"detail": "Choose invoice, boxes, or damage."}, status=400)
        content_type = str(request.data.get("content_type", "")).lower().split(";")[0]
        if content_type not in ALLOWED_IMAGE_TYPES:
            return Response({"detail": "Use a JPEG, PNG, or WebP image."}, status=400)
        try:
            size_bytes = int(request.data.get("size_bytes", 0))
        except (TypeError, ValueError):
            size_bytes = 0
        if size_bytes < 1 or size_bytes > settings.DELIVERY_UPLOAD_MAX_BYTES:
            return Response(
                {"detail": f"Each image must be under {settings.DELIVERY_UPLOAD_MAX_BYTES // (1024 * 1024)} MB."},
                status=400,
            )
        if delivery.assets.exclude(category=DeliveryAsset.Category.NOTES).count() >= 60:
            return Response({"detail": "This delivery already has the maximum of 60 photos."}, status=400)
        filename = safe_filename(request.data.get("filename"), content_type)
        next_position = (
            delivery.assets.filter(category=category).aggregate(value=Count("id"))["value"] or 0
        )
        asset = DeliveryAsset(
            delivery=delivery,
            category=category,
            original_filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            uploaded_by=request.user,
            position=next_position,
        )
        asset.object_key = asset_key(delivery, category, asset.uuid, filename, content_type)
        try:
            upload_url = presigned_upload(asset.object_key, content_type, size_bytes)
        except DeliveryStorageError as exc:
            return Response({"detail": str(exc)}, status=503)
        asset.save()
        return Response(
            {
                "asset_uuid": str(asset.uuid),
                "upload_url": upload_url,
                "method": "PUT",
                "headers": {"Content-Type": content_type},
                "expires_in": settings.DELIVERY_SIGNED_URL_SECONDS,
            },
            status=201,
        )


class DeliveryAssetCompleteAPIView(APIView):
    def post(self, request, delivery_uuid, asset_uuid):
        delivery = get_visible_delivery(request, delivery_uuid)
        asset = get_object_or_404(DeliveryAsset, delivery=delivery, uuid=asset_uuid)
        try:
            head = confirm_object(asset.object_key)
        except DeliveryStorageError as exc:
            asset.upload_status = DeliveryAsset.UploadStatus.FAILED
            asset.save(update_fields=["upload_status", "updated_at"])
            return Response({"detail": str(exc)}, status=409)
        actual_size = int(head.get("ContentLength", 0))
        actual_type = str(head.get("ContentType", "")).split(";")[0]
        if actual_size != asset.size_bytes or actual_type not in ALLOWED_IMAGE_TYPES:
            asset.upload_status = DeliveryAsset.UploadStatus.FAILED
            asset.save(update_fields=["upload_status", "updated_at"])
            return Response({"detail": "The uploaded image did not pass validation."}, status=400)
        try:
            validate_image_object(asset.object_key, actual_type)
        except DeliveryStorageError as exc:
            asset.upload_status = DeliveryAsset.UploadStatus.FAILED
            asset.save(update_fields=["upload_status", "updated_at"])
            return Response({"detail": str(exc)}, status=400)
        asset.upload_status = DeliveryAsset.UploadStatus.UPLOADED
        supplied_checksum = str(request.data.get("checksum_sha256", "")).lower()
        asset.checksum_sha256 = (
            supplied_checksum if re.fullmatch(r"[0-9a-f]{64}", supplied_checksum) else ""
        )
        asset.save(update_fields=["upload_status", "checksum_sha256", "updated_at"])
        queue_asset_replication(asset.pk)
        DeliveryEvent.objects.create(
            delivery=delivery,
            event_type=DeliveryEvent.EventType.PHOTO_ADDED,
            actor=request.user,
            message=f"{asset.get_category_display()} photo added",
            details={"asset_uuid": str(asset.uuid), "filename": asset.original_filename},
        )
        return Response(serialize_asset(asset))


class DeliverySubmitAPIView(APIView):
    def post(self, request, delivery_uuid):
        delivery = get_visible_delivery(request, delivery_uuid)
        if delivery.status not in {Delivery.Status.DRAFT, Delivery.Status.NEEDS_INFO}:
            return Response({"detail": "This delivery has already been submitted."}, status=409)
        previous_status = delivery.status
        previous_submitted_at = delivery.submitted_at
        uploaded = delivery.assets.filter(upload_status=DeliveryAsset.UploadStatus.UPLOADED)
        if not uploaded.filter(category=DeliveryAsset.Category.INVOICE).exists():
            return Response({"detail": "Add at least one invoice photo."}, status=400)
        if not uploaded.filter(category=DeliveryAsset.Category.BOXES).exists():
            return Response({"detail": "Add at least one boxes photo."}, status=400)
        delivery.status = Delivery.Status.SUBMITTED
        delivery.submitted_at = timezone.now()
        delivery.save(update_fields=["status", "submitted_at", "updated_at"])
        try:
            notes_snapshot(delivery)
        except DeliveryStorageError as exc:
            delivery.status = previous_status
            delivery.submitted_at = previous_submitted_at
            delivery.save(update_fields=["status", "submitted_at", "updated_at"])
            return Response({"detail": str(exc)}, status=503)
        DeliveryEvent.objects.create(
            delivery=delivery,
            event_type=DeliveryEvent.EventType.SUBMITTED,
            actor=request.user,
            message=(
                "Additional proof submitted for verification"
                if previous_status == Delivery.Status.NEEDS_INFO
                else "Delivery proof submitted for verification"
            ),
        )
        return Response(serialize_delivery(get_visible_delivery(request, delivery_uuid), request, detail=True))


class DeliveryReviewAPIView(APIView):
    permission_classes = [IsDeliveryAdmin]

    def post(self, request, delivery_uuid):
        delivery = get_object_or_404(delivery_queryset(), uuid=delivery_uuid)
        if delivery.status == Delivery.Status.DRAFT:
            return Response(
                {"detail": "A draft must be submitted before it can be reviewed."}, status=409
            )
        new_status = str(request.data.get("status", ""))
        if new_status not in REVIEW_STATUSES:
            return Response({"detail": "Choose a valid review status."}, status=400)
        if new_status in {Delivery.Status.VERIFIED, Delivery.Status.RESOLVED}:
            uploaded = delivery.assets.filter(upload_status=DeliveryAsset.UploadStatus.UPLOADED)
            if not uploaded.filter(category=DeliveryAsset.Category.INVOICE).exists() or not uploaded.filter(
                category=DeliveryAsset.Category.BOXES
            ).exists():
                return Response(
                    {"detail": "Invoice and box photos are required before verification."},
                    status=409,
                )
        previous = delivery.status
        with transaction.atomic():
            delivery.status = new_status
            delivery.admin_notes = str(request.data.get("admin_notes", delivery.admin_notes)).strip()
            delivery.reviewed_by = request.user
            delivery.reviewed_at = timezone.now()
            try:
                notes_snapshot(delivery)
            except DeliveryStorageError as exc:
                return Response({"detail": str(exc)}, status=503)
            delivery.save(
                update_fields=["status", "admin_notes", "reviewed_by", "reviewed_at", "updated_at"]
            )
        DeliveryEvent.objects.create(
            delivery=delivery,
            event_type=DeliveryEvent.EventType.REVIEWED,
            actor=request.user,
            message=f"Status changed from {dict(Delivery.Status.choices).get(previous)} to {delivery.get_status_display()}",
            details={"from": previous, "to": new_status},
        )
        return Response(serialize_delivery(get_visible_delivery(request, delivery_uuid), request, detail=True))


class DeliveryKeywordsAPIView(APIView):
    permission_classes = [IsDeliveryAdmin]

    def get(self, request, delivery_uuid=None):
        query = request.query_params.get("q", "").strip()
        keywords = DeliveryKeyword.objects.all()
        if query:
            keywords = keywords.filter(name__icontains=query)
        return Response([item.name for item in keywords[:50]])

    def put(self, request, delivery_uuid):
        delivery = get_object_or_404(Delivery, uuid=delivery_uuid)
        raw_names = request.data.get("keywords", [])
        if not isinstance(raw_names, list):
            return Response({"detail": "Keywords must be a list."}, status=400)
        names = []
        for raw in raw_names[:20]:
            name = re.sub(r"\s+", " ", str(raw)).strip(" ,#")[:64]
            if name and name.casefold() not in {item.casefold() for item in names}:
                names.append(name)
        selected = []
        try:
            with transaction.atomic():
                for name in names:
                    normalized = name.casefold()
                    keyword, _ = DeliveryKeyword.objects.get_or_create(
                        normalized_name=normalized,
                        defaults={"name": name, "created_by": request.user},
                    )
                    selected.append(keyword)
                delivery.keywords.set(selected)
                DeliveryEvent.objects.create(
                    delivery=delivery,
                    event_type=DeliveryEvent.EventType.KEYWORDS_CHANGED,
                    actor=request.user,
                    message="Search keywords updated",
                    details={"keywords": names},
                )
                notes_snapshot(delivery)
        except DeliveryStorageError as exc:
            return Response({"detail": str(exc)}, status=503)
        return Response({"keywords": names})


class DeliveryAssetViewAPIView(APIView):
    def get(self, request, asset_uuid):
        asset = get_object_or_404(
            DeliveryAsset.objects.select_related("delivery", "replica"),
            uuid=asset_uuid,
            upload_status=DeliveryAsset.UploadStatus.UPLOADED,
        )
        if not is_delivery_admin(request.user) and asset.delivery.submitted_by_id != request.user.id:
            return Response({"detail": "You do not have access to this photo."}, status=403)
        try:
            url = signed_asset_download(asset)
        except DeliveryStorageError as exc:
            return Response({"detail": str(exc)}, status=503)
        return HttpResponseRedirect(url)


class DeliveryDownloadAPIView(APIView):
    permission_classes = [IsDeliveryAdmin]

    def get(self, request, delivery_uuid):
        delivery = get_object_or_404(delivery_queryset(), uuid=delivery_uuid)
        try:
            archive = build_delivery_zip(delivery)
        except DeliveryStorageError as exc:
            return Response({"detail": str(exc)}, status=503)
        DeliveryEvent.objects.create(
            delivery=delivery,
            event_type=DeliveryEvent.EventType.DOWNLOADED,
            actor=request.user,
            message="Delivery proof bundle downloaded",
        )
        filename = f"delivery-{delivery.store.number}-{timezone.localtime(delivery.delivered_at):%Y%m%d}-{delivery.uuid}.zip"
        return FileResponse(archive, as_attachment=True, filename=filename)


class DeliveryCSVExportAPIView(APIView):
    permission_classes = [IsDeliveryAdmin]

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="delivery-log-{timezone.localdate():%Y%m%d}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "Delivery ID",
                "Store number",
                "Store name",
                "Delivered at",
                "Reference",
                "Status",
                "Submitted by",
                "Submitted at",
                "Expected cases",
                "Delivered cases",
                "Damaged cases",
                "General notes",
                "Issue notes",
                "Admin notes",
                "Keywords",
                "Invoice photos",
                "Box photos",
                "Damage photos",
                "DR verified files",
                "DR pending or failed files",
            ]
        )
        for delivery in delivery_queryset():
            assets = list(delivery.assets.all())
            category_count = lambda category: sum(
                item.category == category and item.upload_status == "uploaded" for item in assets
            )
            verified_count = sum(
                1
                for item in assets
                if item.upload_status == DeliveryAsset.UploadStatus.UPLOADED
                and getattr(item, "replica", None)
                and item.replica.status == DeliveryAssetReplica.Status.VERIFIED
            )
            unprotected_count = sum(
                1
                for item in assets
                if item.upload_status == DeliveryAsset.UploadStatus.UPLOADED
                and (
                    not getattr(item, "replica", None)
                    or item.replica.status != DeliveryAssetReplica.Status.VERIFIED
                )
            )
            writer.writerow(
                [
                    delivery.uuid,
                    delivery.store.number,
                    delivery.store.name,
                    timezone.localtime(delivery.delivered_at).isoformat(),
                    delivery.reference_number,
                    delivery.get_status_display(),
                    user_label(delivery.submitted_by),
                    timezone.localtime(delivery.submitted_at).isoformat() if delivery.submitted_at else "",
                    delivery.expected_cases,
                    delivery.delivered_cases,
                    delivery.damaged_cases,
                    delivery.general_notes,
                    delivery.issue_notes,
                    delivery.admin_notes,
                    ", ".join(delivery.keywords.values_list("name", flat=True)),
                    category_count("invoice"),
                    category_count("boxes"),
                    category_count("damage"),
                    verified_count,
                    unprotected_count,
                ]
            )
        return response


class DeliveryBackupAPIView(APIView):
    permission_classes = [IsDeliveryAdmin]

    def get(self, request):
        backups = DeliveryBackup.objects.select_related("created_by")[:30]
        uploaded = DeliveryAsset.objects.filter(upload_status=DeliveryAsset.UploadStatus.UPLOADED)
        total_assets = uploaded.count()
        verified_assets = uploaded.filter(
            replica__status=DeliveryAssetReplica.Status.VERIFIED
        ).count()
        failed_assets = uploaded.filter(
            replica__status__in=[
                DeliveryAssetReplica.Status.FAILED,
                DeliveryAssetReplica.Status.MISSING,
            ]
        ).count()
        return Response(
            {
                "storage_configured": is_configured(),
                "dr_storage_configured": dr_is_configured(),
                "replication": {
                    "total_assets": total_assets,
                    "verified_assets": verified_assets,
                    "pending_assets": total_assets - verified_assets - failed_assets,
                    "failed_assets": failed_assets,
                    "coverage_percent": round((verified_assets / total_assets) * 100, 1)
                    if total_assets
                    else 100.0,
                },
                "backups": [
                    {
                        "uuid": str(item.uuid),
                        "status": item.status,
                        "created_at": item.created_at,
                        "created_by": user_label(item.created_by) or "Scheduled backup",
                        "delivery_count": item.delivery_count,
                        "asset_count": item.asset_count,
                        "size_bytes": item.size_bytes,
                        "error": item.error_message,
                        "download_url": f"/api/delivery-backups/{item.uuid}/download/"
                        if item.status == DeliveryBackup.Status.COMPLETE
                        else None,
                    }
                    for item in backups
                ],
            }
        )

    def post(self, request):
        if request.data.get("action") == "sync":
            if not dr_is_configured():
                return Response(
                    {"detail": "Delivery disaster-recovery storage is not configured."},
                    status=503,
                )
            from .tasks import reconcile_delivery_replicas_task

            reconcile_delivery_replicas_task.delay()
            return Response({"queued": True}, status=202)
        backup = DeliveryBackup.objects.create(created_by=request.user)
        try:
            key, delivery_count, asset_count, size_bytes = create_metadata_backup(backup)
            backup.object_key = key
            backup.delivery_count = delivery_count
            backup.asset_count = asset_count
            backup.size_bytes = size_bytes
            backup.status = DeliveryBackup.Status.COMPLETE
        except Exception as exc:
            backup.status = DeliveryBackup.Status.FAILED
            backup.error_message = str(exc)[:2000]
        backup.save()
        code = 201 if backup.status == DeliveryBackup.Status.COMPLETE else 503
        return Response(
            {
                "uuid": str(backup.uuid),
                "status": backup.status,
                "delivery_count": backup.delivery_count,
                "asset_count": backup.asset_count,
                "size_bytes": backup.size_bytes,
                "error": backup.error_message,
            },
            status=code,
        )


class DeliveryBackupDownloadAPIView(APIView):
    permission_classes = [IsDeliveryAdmin]

    def get(self, request, backup_uuid):
        backup = get_object_or_404(
            DeliveryBackup, uuid=backup_uuid, status=DeliveryBackup.Status.COMPLETE
        )
        try:
            url = signed_dr_download(
                backup.object_key,
                filename=f"s2u-delivery-metadata-{backup.created_at:%Y%m%d-%H%M%S}.json.gz",
                content_type="application/gzip",
            )
        except DeliveryStorageError as exc:
            return Response({"detail": str(exc)}, status=503)
        return HttpResponseRedirect(url)
