import ipaddress
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from django.conf import settings


class ProviderUnavailable(Exception):
    pass


class InvalidProviderResponse(Exception):
    pass


@dataclass(frozen=True)
class MahaReraResult:
    provider_record_id: str
    retrieved_at: datetime
    fields: dict
    source_version: str
    source_url: str | None = None


@dataclass(frozen=True)
class RouteResult:
    duration_seconds: int
    distance_meters: int
    retrieved_at: datetime
    assumptions: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TransactionResult:
    provider_record_id: str
    registration_date: str
    amount: str
    currency: str
    area: str
    area_unit: str
    unit_type: str = ""
    attributes: dict = field(default_factory=dict)


class MahaReraProvider(Protocol):
    def resolve(self, registration_id: str) -> MahaReraResult: ...


class RoutingProvider(Protocol):
    def estimate(self, origin: dict, destination: dict, mode: str, departure_time: datetime | None) -> RouteResult: ...


class TransactionProvider(Protocol):
    def fetch_for_project(self, project) -> list[TransactionResult]: ...


class DocumentScanner(Protocol):
    def scan(self, file_object) -> str: ...


class DocumentExtractor(Protocol):
    def extract(self, file_object, media_type: str) -> list[dict]: ...


class UnavailableMahaReraProvider:
    def resolve(self, registration_id: str) -> MahaReraResult:
        raise ProviderUnavailable("MahaRERA provider is not configured.")


class UnavailableRoutingProvider:
    def estimate(self, origin: dict, destination: dict, mode: str, departure_time: datetime | None) -> RouteResult:
        raise ProviderUnavailable("Routing provider is not configured.")


class UnavailableTransactionProvider:
    def fetch_for_project(self, project) -> list[TransactionResult]:
        raise ProviderUnavailable("Transaction provider is not configured.")


class RejectingScanner:
    def scan(self, file_object) -> str:
        raise ProviderUnavailable("A malware scanner must be configured before documents can be processed.")


class ClamAvCommandScanner:
    code = "clamav"

    def __init__(self, executable=None, timeout_seconds=None):
        self.executable = executable or getattr(settings, "CLAMAV_EXECUTABLE", "clamscan")
        self.timeout_seconds = timeout_seconds or getattr(settings, "CLAMAV_TIMEOUT_SECONDS", 30)

    def scan(self, file_object):
        executable = shutil.which(self.executable)
        if not executable:
            raise ProviderUnavailable("ClamAV executable is unavailable.")
        file_object.seek(0)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temporary:
                temporary_path = temporary.name
                while chunk := file_object.read(1024 * 1024):
                    temporary.write(chunk)
            try:
                result = subprocess.run(
                    [executable, "--no-summary", "--stdout", temporary_path],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderUnavailable("Malware scan timed out.") from exc
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
        file_object.seek(0)
        if result.returncode == 0:
            return "clean"
        if result.returncode == 1:
            return "infected"
        raise ProviderUnavailable("Malware scanner failed.")


def get_document_scanner():
    if getattr(settings, "DOCUMENT_SCANNER", "unavailable") == "clamav":
        return ClamAvCommandScanner()
    return RejectingScanner()


class UnavailableExtractor:
    def extract(self, file_object, media_type: str) -> list[dict]:
        raise ProviderUnavailable("Document extraction provider is not configured.")


def validate_provider_url(url: str, allowed_hosts: set[str]) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ImproperlyConfigured("Provider URLs must be absolute HTTPS URLs without credentials or fragments.")
    if parsed.port not in (None, 443):
        raise ImproperlyConfigured("Provider URLs must use the default HTTPS port.")
    normalized_host = parsed.hostname.rstrip(".").casefold()
    normalized_allowlist = {host.rstrip(".").casefold() for host in allowed_hosts}
    if normalized_host not in normalized_allowlist or any("*" in host for host in normalized_allowlist):
        raise ImproperlyConfigured("Provider host is not exactly allowlisted.")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ImproperlyConfigured("Private or reserved provider addresses are forbidden.")
    return url
