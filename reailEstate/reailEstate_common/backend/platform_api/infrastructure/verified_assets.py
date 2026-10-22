"""Bounded, checksum-verified reads without authorization or database queries."""

import hashlib


def read_verified_asset(asset, storage_backend=None, *, max_bytes=None):
    """Read an object with object_key, byte_length and sha256 attributes.

    Read at most the recorded length plus one byte to detect oversized objects.
    An optional max_bytes ceiling rejects oversized metadata before opening storage;
    it never returns truncated evidence. Callers own readiness and access checks.
    Missing objects and storage errors propagate unchanged.
    """
    length = asset.byte_length
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        raise ValueError("max_bytes must be a non-negative integer.")
    if type(length) is not int or length < 0 or (max_bytes is not None and length > max_bytes):
        raise ValueError("IMAGE_INTEGRITY_MISMATCH")
    if storage_backend is None:
        from ..artifact_storage import storage
        storage_backend = storage
    with storage_backend.open(asset.object_key, "rb") as stream:
        data = stream.read(length + 1)
    if len(data) != length or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("IMAGE_INTEGRITY_MISMATCH")
    return data