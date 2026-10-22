"""Common upload formats and optional sandboxed Office conversion after malware scanning."""

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FORMATS = {
    "application/pdf": {".pdf"}, "image/jpeg": {".jpg", ".jpeg"}, "image/png": {".png"},
    "text/plain": {".txt"}, "text/markdown": {".md", ".markdown"}, "text/csv": {".csv"},
    "application/json": {".json"}, DOCX: {".docx"}, "application/msword": {".doc"},
    "application/rtf": {".rtf"}, "text/rtf": {".rtf"},
    "application/vnd.oasis.opendocument.text": {".odt"},
    "application/vnd.ms-powerpoint": {".ppt"}, "application/vnd.ms-excel": {".xls"},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {".pptx"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {".xlsx"},
}
ZIP_PARTS = {
    DOCX: "word/document.xml",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ppt/presentation.xml",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xl/workbook.xml",
    "application/vnd.oasis.opendocument.text": "content.xml",
}
OLE_STREAMS = {"application/msword": "WordDocument", "application/vnd.ms-excel": "Workbook",
               "application/vnd.ms-powerpoint": "PowerPoint Document"}
CONVERTED_FORMATS = set(ZIP_PARTS) | set(OLE_STREAMS) | {"application/rtf", "text/rtf"}


def office_signature(file_object, media_type):
    try:
        if media_type in ZIP_PARTS:
            with zipfile.ZipFile(file_object) as archive:
                entries = archive.infolist()
                total = sum(item.file_size for item in entries)
                compressed = sum(max(1, item.compress_size) for item in entries)
                names = {item.filename for item in entries}
                if len(entries) > 10000 or total > 50 * 1024 * 1024 or total / max(1, compressed) > 100:
                    return False
                if any("vbaproject" in name.casefold() or "/scripts/" in ("/" + name.casefold()) for name in names):
                    return False
                if media_type == "application/vnd.oasis.opendocument.text":
                    return "mimetype" in names and archive.read("mimetype").decode() == media_type and "content.xml" in names
                return "[Content_Types].xml" in names and ZIP_PARTS[media_type] in names
        if media_type in OLE_STREAMS:
            import olefile
            with olefile.OleFileIO(file_object) as ole:
                names = ole.listdir()
                if any(part.upper() in {"VBA", "MACROS", "_VBA_PROJECT"} for stream in names for part in stream):
                    return False
                return ole.exists(OLE_STREAMS[media_type]) or (media_type == "application/vnd.ms-excel" and ole.exists("Book"))
        if media_type in {"application/rtf", "text/rtf"}:
            return file_object.read(5) == b"{\\rtf"
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, UnicodeError):
        return False
    finally:
        file_object.seek(0)
    return False


def prepare_document(content, media_type):
    """Keep originals immutable. Only explicitly enabled trusted converter binaries run."""
    if media_type not in CONVERTED_FORMATS:
        return content, media_type, False
    if media_type == DOCX and not settings.OFFICE_CONVERTER_ENABLED:
        return content, media_type, False  # Native text/table extraction still available.
    if not settings.OFFICE_CONVERTER_ENABLED:
        raise ValueError("OFFICE_CONVERTER_REQUIRED")
    executable = shutil.which(settings.OFFICE_CONVERTER_EXECUTABLE)
    if not executable:
        raise ValueError("OFFICE_CONVERTER_UNAVAILABLE")
    suffix = sorted(FORMATS[media_type])[0]
    with tempfile.TemporaryDirectory(prefix="property-convert-") as temporary:
        root = Path(temporary)
        source = root / ("source" + suffix)
        source.write_bytes(content)
        output = root / "output"
        output.mkdir()
        profile = root / "profile"
        (profile / "user").mkdir(parents=True)
        # Highest macro security in the isolated throwaway profile.
        (profile / "user" / "registrymodifications.xcu").write_text(
            '<?xml version="1.0"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>',
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [executable, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--nologo", "--nodefault",
                 "--norestore", "--convert-to", "pdf", "--outdir", str(output), str(source)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=settings.OFFICE_CONVERTER_TIMEOUT_SECONDS, check=False, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("OFFICE_CONVERSION_TIMEOUT") from exc
        target = output / "source.pdf"
        if result.returncode or not target.exists() or target.stat().st_size > settings.MAX_UPLOAD_BYTES:
            raise ValueError("OFFICE_CONVERSION_FAILED")
        data = target.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise ValueError("OFFICE_CONVERSION_FAILED")
        return data, "application/pdf", True