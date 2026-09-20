"""Explicit development-only exception while builder-upload malware scanning is deferred."""

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .builder_access import builder_can_manage
from .ingestion_runtime import event
from .models import AuditEvent


def scan_builder_document(document, content, scanner):
    trust_local = getattr(settings, "LOCAL_TRUST_BUILDER_UPLOADS", False)
    if trust_local and (not settings.DEBUG or settings.ENVIRONMENT not in {"local", "development"}):
        raise ImproperlyConfigured("LOCAL_TRUST_BUILDER_UPLOADS requires local/development with DEBUG enabled.")
    if document.scan_status == "rejected":
        return "infected"
    if trust_local and builder_can_manage(document.uploaded_by, document.project):
        AuditEvent.objects.get_or_create(
            action="document.malware_scan_skipped", entity_type="EvidenceDocument", entity_id=document.pk,
            defaults={
                "correlation_id": uuid4(), "reason_code": "LOCAL_TRUSTED_BUILDER_UPLOAD",
                "metadata": {"scanner": "skipped", "malwareScanned": False,
                             "uploadedBy": str(document.uploaded_by_id), "sha256": document.sha256},
            },
        )
        event("malware_scan_skipped", reasonCode="LOCAL_TRUSTED_BUILDER_UPLOAD")
        content.seek(0)
        # Existing extraction/media gates use "clean" for accepted source documents.
        # The audit above distinguishes local trust from an actual malware scan.
        return "clean"
    event("malware_scan")
    return scanner.scan(content)
