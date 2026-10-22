import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .document_formats import FORMATS

IGNORED_DIRECTORY = ".ignore"
MEDIA_TYPES = {suffix: media_type for media_type, suffixes in FORMATS.items() for suffix in suffixes}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".m4v", ".mpeg", ".mpg", ".3gp"}
MAHA_RERA_PATTERN = re.compile(r"\b(?:P\d{10,12}|P[MR]\d{11,13})\b", re.IGNORECASE)


@dataclass(frozen=True)
class SampleDocument:
    path: Path
    locality: str
    project_key: str
    project_name: str
    category: str
    byte_length: int
    sha256: str
    eligible: bool
    maha_rera_ids: tuple[str, ...]
    duplicate_of: Path | None = None
    media_type: str = "application/pdf"
    project_directory: str = ""

    @property
    def source_role(self) -> str:
        if self.media_type in {"image/png", "image/jpeg"}:
            return "precut_floor_plan_candidate"
        if self.category == "floor_plan":
            return "floor_plan_document"
        return "supporting_evidence"

    @property
    def processing_priority(self) -> int:
        """Process likely pre-cut plans before documents that may contain full floors."""
        return {"precut_floor_plan_candidate": 0, "floor_plan_document": 1}.get(self.source_role, 2)


def categorize_samples(root: Path, max_upload_bytes: int) -> tuple[list[SampleDocument], list[Path]]:
    """Group supported sources by domain/project directory; preserve legacy flat layouts."""
    root = root.resolve()
    documents = []
    ignored = []
    seen_hashes = {}
    for path in sorted(root.rglob("*"), key=_sample_sort_key):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if IGNORED_DIRECTORY in [part.casefold() for part in relative_parts]:
            ignored.append(path)
            continue
        media_type = MEDIA_TYPES.get(path.suffix.casefold())
        if not media_type:
            continue
        locality = relative_parts[0] if len(relative_parts) > 1 else "Uncategorized"
        digest = _file_digest(path)
        project_directory = "/".join(relative_parts[:2]) if len(relative_parts) > 2 else ""
        project_name = relative_parts[1] if project_directory else _project_name(path.stem)
        project_key = project_directory.casefold() if project_directory else _project_key(locality, project_name)
        duplicate_of = seen_hashes.setdefault((project_key, digest), path)
        documents.append(SampleDocument(
            path=path,
            locality=locality,
            project_key=project_key,
            project_name=project_name,
            category=_category(path.stem),
            byte_length=path.stat().st_size,
            sha256=digest,
            eligible=path.stat().st_size <= max_upload_bytes,
            maha_rera_ids=_maha_rera_ids(path) if media_type == "application/pdf" else (),
            duplicate_of=None if duplicate_of == path else duplicate_of,
            media_type=media_type,
            project_directory=project_directory,
        ))
    return documents, ignored


def prioritize_project_sources(documents):
    """Keep a project together while making explicit image plans available first."""
    return sorted(documents, key=lambda item: (item.processing_priority, _sample_sort_key(item.path)))


def skipped_samples(root: Path) -> list[dict]:
    """Account for excluded sources without opening ignored files or videos."""
    skipped = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if IGNORED_DIRECTORY in [part.casefold() for part in relative.parts]:
            reason = "ignored_directory"
        elif path.suffix.casefold() in VIDEO_EXTENSIONS:
            reason = "video"
        elif path.suffix.casefold() not in MEDIA_TYPES:
            reason = "unsupported_format"
        else:
            continue
        skipped.append({"path": relative.as_posix(), "reason": reason})
    return skipped


def _category(stem: str) -> str:
    normalized = re.sub(r"[_-]+", " ", stem.casefold())
    if re.search(r"\b(elevation|elev\b)", normalized):
        return "elevation"
    if re.search(r"\b(agreement|sale deed|registration)\b", normalized):
        return "agreement"
    if re.search(r"\b(floor[ _-]*plans?|unit[ _-]*plans?|typical[ _-]*floor|sale[ _-]*unit|plan[ _-]*book)\b", normalized):
        return "floor_plan"
    if re.search(r"\b(brochure|booklet)\b", normalized):
        return "brochure"
    return "project_material"


def _project_name(stem: str) -> str:
    value = re.sub(r"\s*\(\d+\)$", "", stem).strip()
    value = re.sub(r"[_-]+", " ", value)
    value = re.sub(r"\b(brochure|booklet|floor[ _-]*plans?|unit[ _-]*plans?|plan[ _-]*book|elevation)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" _-.")
    return value or stem.strip()


def _project_key(locality: str, project_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", project_name.casefold()).strip("-")[:48] or "unnamed"
    locality_slug = re.sub(r"[^a-z0-9]+", "-", locality.casefold()).strip("-")[:24] or "uncategorized"
    return f"{locality_slug}-{slug}"


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _maha_rera_ids(path: Path) -> tuple[str, ...]:
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
    try:
        identifiers = []
        with pymupdf.open(path) as document:
            if document.needs_pass:
                return ()
            for page in document:
                for match in MAHA_RERA_PATTERN.finditer(page.get_text("text")):
                    identifier = match.group(0).upper()
                    if identifier not in identifiers:
                        identifiers.append(identifier)
        return tuple(identifiers)
    except Exception:
        return ()
    finally:
        pymupdf.TOOLS.mupdf_display_errors(True)
        pymupdf.TOOLS.mupdf_display_warnings(True)


def _sample_sort_key(path: Path):
    stem_without_copy_suffix = re.sub(r"\s*\(\d+\)$", "", path.stem).casefold()
    is_copy = path.stem.casefold() != stem_without_copy_suffix
    return (str(path.parent).casefold(), stem_without_copy_suffix, is_copy, path.name.casefold())
