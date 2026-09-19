"""Explicit, private commute lookups using the server-side Maps integration."""

from uuid import uuid4

from django.conf import settings

from .external_sources import GoogleProjectMaps, coordinates, direct_location


class CommuteLocationError(ValueError):
    def __init__(self, code, detail, status=400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status


def search_locations(payload):
    query = payload.get("query") if isinstance(payload, dict) else None
    if not isinstance(query, str) or not 3 <= len(query.strip()) <= 500:
        raise CommuteLocationError("INVALID_LOCATION_QUERY", "Enter a place name or Plus Code (3 to 500 characters).")
    if not getattr(settings, "GOOGLE_MAPS_API_KEY", ""):
        raise CommuteLocationError(
            "GOOGLE_MAPS_NOT_CONFIGURED",
            "Google Maps search is not configured. Enter latitude and longitude instead.",
            503,
        )
    try:
        places = GoogleProjectMaps().search(query.strip())
        results = []
        for place in places:
            place_id, name = place["placeId"], place["name"]
            if (not isinstance(place_id, str) or not place_id.strip() or len(place_id) > 255
                    or any(ord(character) < 32 for character in place_id)
                    or not isinstance(name, str) or not name.strip()):
                raise ValueError("Invalid place.")
            address = place.get("address", "")
            if not isinstance(address, str):
                raise ValueError("Invalid address.")
            label = f"{name}, {address}" if address else name
            results.append({
                "placeId": place_id, "label": label[:255],
                **coordinates(place["location"]), "precision": "exact",
            })
        return {"results": results}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError):
        raise CommuteLocationError(
            "GOOGLE_MAPS_UNAVAILABLE",
            "Google Maps search is temporarily unavailable. Retry or enter latitude and longitude.",
            503,
        ) from None


def resolve_location(payload):
    if not isinstance(payload, dict):
        raise CommuteLocationError("INVALID_LOCATION", "Enter a coordinate pair or a Google Maps share link.")
    has_url = "mapsUrl" in payload
    has_coordinates = "latitude" in payload or "longitude" in payload
    if has_url == has_coordinates:
        raise CommuteLocationError("INVALID_LOCATION", "Enter either a coordinate pair or a Google Maps share link, not both.")
    if has_url:
        maps_url = payload["mapsUrl"]
        if not isinstance(maps_url, str) or not maps_url.strip() or len(maps_url) > 8192:
            raise CommuteLocationError("INVALID_MAPS_LOCATION", "Enter a valid HTTPS Google Maps share link.")
        try:
            location = direct_location(maps_url=maps_url.strip())["location"]
        except (ValueError, TypeError, OverflowError, OSError):
            raise CommuteLocationError(
                "INVALID_MAPS_LOCATION",
                "This Google Maps link could not be resolved to a single pin. Use a pin/share link or enter latitude and longitude.",
            ) from None
    else:
        try:
            location = direct_location(latitude=payload.get("latitude"), longitude=payload.get("longitude"))["location"]
        except (ValueError, TypeError, OverflowError):
            raise CommuteLocationError(
                "INVALID_COORDINATES", "Enter finite numeric latitude (-90 to 90) and longitude (-180 to 180).",
            ) from None
    # The identifier must not encode a private coordinate pair or original share URL.
    return {"placeId": f"private-{uuid4()}", "label": "Private commute destination", **location, "precision": "exact"}
