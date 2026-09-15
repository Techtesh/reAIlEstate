"""Private local paths; clients receive authenticated URIs, never filesystem URLs."""

import hashlib
import logging
from pathlib import PurePosixPath
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.db import transaction


class RoutedStorage:
    def backend(self, key):
        if key.startswith(("user/media/", "output/media/")):
            return FileSystemStorage(location=settings.WORKSPACE_DATA_ROOT)
        return default_storage  # Backward-compatible historical media.

    def open(self, key, mode="rb"):
        return self.backend(key).open(key, mode)

    def save(self, key, content):
        return self.backend(key).save(key, content)

    def exists(self, key):
        return self.backend(key).exists(key)

    def delete(self, key):
        return self.backend(key).delete(key)


storage = RoutedStorage()


def move_document(document, bucket):
    """Copy and verify first; delete the old object only after the DB commit."""
    if bucket not in {"todo", "completed"}:
        raise ValueError("INVALID_DOCUMENT_BUCKET")
    if document.object_key.startswith(f"user/media/{bucket}/"):
        return
    old_key = document.object_key
    suffix = PurePosixPath(document.original_name).suffix.lower()
    target = f"user/media/{bucket}/{document.project_id}/{document.id}/{uuid4().hex}{suffix}"
    with storage.open(old_key) as source:
        content = source.read(document.byte_length + 1)
    if len(content) != document.byte_length or hashlib.sha256(content).hexdigest() != document.sha256:
        raise ValueError("SOURCE_INTEGRITY_MISMATCH")
    saved = storage.save(target, ContentFile(content))
    try:
        with storage.open(saved) as copied:
            if hashlib.sha256(copied.read()).hexdigest() != document.sha256:
                raise ValueError("COPY_INTEGRITY_MISMATCH")
        document.object_key = saved
        document.save(update_fields=["object_key", "updated_at"])
    except Exception:
        storage.delete(saved)
        raise

    def remove_old():
        try:
            storage.delete(old_key)
        except OSError:
            # Retain a duplicate rather than failing a committed extraction.
            logging.getLogger(__name__).warning("Old evidence copy retained after relocation")
    transaction.on_commit(remove_old)