import hashlib
from io import BytesIO

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageOps

from .artifact_storage import storage
from .floor_plan_visibility import project_gallery_assets
from .infrastructure.source_documents import render_source_pages


def picture_candidates(project):
    return project_gallery_assets(project, can_review=True).filter(
        byte_length__lte=10 * 1024 * 1024,
    ).select_related("document").order_by("document__created_at", "page_number", "created_at", "pk")


def first_picture_document(project):
    return project.documents.filter(scan_status="clean", declared_media_type="application/pdf").order_by("created_at", "pk").first()


def normalize_picture(content):
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Choose an image up to 10 MB.")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 20_000_000:
                raise ValueError("Choose a JPEG, PNG or WebP image up to 20 megapixels.")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
            normalized.thumbnail((2400, 2400))
            normalized.info.clear()
            output = BytesIO()
            normalized.save(output, format="PNG")
            return output.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Choose a valid project image.") from exc


def extracted_picture(project, asset_id):
    asset = picture_candidates(project).filter(pk=asset_id).first()
    if asset is None:
        raise ValueError("This image is no longer available for this project.")
    try:
        with storage.open(asset.object_key, "rb") as stream:
            content = stream.read(asset.byte_length + 1)
    except OSError as exc:
        raise ValueError("The extracted image is missing.") from exc
    if len(content) != asset.byte_length or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise ValueError("The extracted image failed checksum validation.")
    return SimpleUploadedFile("project.png", normalize_picture(content), content_type="image/png")


def default_picture(project):
    document = first_picture_document(project)
    if document is None:
        raise ValueError("No clean PDF is available for a project picture.")
    key = f"project-picture:first-page:v1:{document.pk}:{document.sha256}"
    content = cache.get(key)
    if content is None:
        content = normalize_picture(render_source_pages(document, [1])[0][1])
        cache.set(key, content, 300)
    return content
