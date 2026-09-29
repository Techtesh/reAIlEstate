"""Contracts for licensed/approved external sources. No invented registry or travel data."""

from urllib.parse import urlencode, urlsplit
import json

from django.conf import settings
from django.utils.module_loading import import_string
from django.utils.dateparse import parse_datetime


class UnavailableProjectEnrichment:
    def registration(self, number):
        return None

    def stations(self, location):
        return None

    def nearby_amenities(self, location):
        return None

    def developer(self, numbers):
        return None

    def sentiment(self, developer_name):
        return None


def backend():
    path = getattr(settings, "PROJECT_ENRICHMENT_BACKEND", "")
    return import_string(path)() if path else UnavailableProjectEnrichment()


def source_url(value, maharera=False):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if maharera:
        host = parsed.hostname.lower()
        return host in {"maharera.mahaonline.gov.in", "maharera.maharashtra.gov.in", "maharerait.maharashtra.gov.in"} or host.endswith(".maharera.maharashtra.gov.in")
    return True


def fetch(provider, method, argument, *, maharera=False):
    """Adapters must return sourceUrl/retrievedAt/data; failures never mean verified."""
    try:
        result = getattr(provider, method)(argument)
        if result is None:
            return {"status": "unavailable", "data": None}
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ValueError
        timestamp = result.get("retrievedAt", "")
        if not isinstance(timestamp, str) or parse_datetime(timestamp) is None:
            raise ValueError
        if not source_url(result.get("sourceUrl"), maharera):
            raise ValueError
        if len(json.dumps(result, allow_nan=False).encode()) > 1_000_000:
            raise ValueError
        if method == "sentiment":
            data = result["data"]
            if type(data.get("sampleCount")) is not int or data["sampleCount"] < 1:
                raise ValueError
            if not all(isinstance(data.get(key), str) and parse_datetime(data[key]) for key in ("windowStart", "windowEnd")):
                raise ValueError
        return {"status": "available", "data": result["data"],
                "sourceUrl": result["sourceUrl"], "retrievedAt": timestamp}
    except Exception:
        return {"status": "unavailable", "data": None, "reasonCode": "ENRICHMENT_UNAVAILABLE"}


def journey_link(origin, destination, mode):
    if not origin or not destination:
        return None
    return "https://www.google.com/maps/dir/?" + urlencode({
        "api": 1, "origin": origin, "destination": destination, "travelmode": mode,
    })