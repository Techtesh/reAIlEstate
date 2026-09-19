"""Explicit external lookups; provider results never silently become project facts."""

import json
import hashlib
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from .project_enrichment import backend, fetch, source_url


PLACE_FIELDS = "id,displayName,formattedAddress,location,googleMapsUri,types,businessStatus,attributions"
REGISTRY_SEARCH = "https://maharera.maharashtra.gov.in/projects-search-result"
REGISTRATION = re.compile(r"(?:P\d{10,12}|P[MR]\d{11,13})", re.I)


def coordinates(value):
    if not isinstance(value, dict):
        raise ValueError("Coordinates are missing.")
    result = {}
    for key, limit in (("latitude", 90), ("longitude", 180)):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or abs(number) > limit:
            raise ValueError("Invalid coordinates.")
        result[key] = number
    return result


def map_link(place_id, name):
    return "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": name, "query_place_id": place_id})


def direct_location(latitude=None, longitude=None, maps_url=""):
    from .management.commands.import_google_project_place_decisions import (
        PLACE_COORDINATES, resolve_share_url, validate_maps_url,
    )

    if maps_url and (latitude is not None or longitude is not None):
        raise ValueError("Enter either a coordinate pair or a Google Maps link, not both.")
    if maps_url:
        parsed = validate_maps_url(maps_url)
        if parsed.hostname == "maps.app.goo.gl":
            location = resolve_share_url(maps_url)["location"]
        else:
            if parsed.path.startswith("/maps/dir"):
                raise ValueError("Use a single project pin, not a directions link.")
            matches = PLACE_COORDINATES.findall(unquote(parsed.path))
            if len(matches) == 1:
                latitude, longitude = map(float, matches[0])
            else:
                query = parse_qs(parsed.query, max_num_fields=30)
                values = query.get("query", []) + query.get("q", [])
                if matches or len(values) != 1 or not re.fullmatch(r"\s*[+-]?\d+(?:\.\d+)?\s*,\s*[+-]?\d+(?:\.\d+)?\s*", values[0]):
                    raise ValueError("This link has no single pin location. Paste a pin/share link or enter latitude and longitude.")
                latitude, longitude = map(float, values[0].split(","))
            location = coordinates({"latitude": latitude, "longitude": longitude})
    else:
        if latitude is None or longitude is None:
            raise ValueError("Enter both latitude and longitude, or a Google Maps link.")
        location = coordinates({"latitude": latitude, "longitude": longitude})
    result = {"location": location, "sourceUrl": "https://www.google.com/maps/search/?" + urlencode({
        "api": 1, "query": f"{location['latitude']},{location['longitude']}"}),
        "evidenceMethod": "manual_maps_link" if maps_url else "manual_coordinates"}
    if maps_url:
        result["sourceUrlDigest"] = hashlib.sha256(maps_url.encode()).hexdigest()
    return result


def route_link(origin, destination, mode):
    parameters = {
        "api": 1, "origin": f"{origin['latitude']},{origin['longitude']}",
        "destination": f"{destination['latitude']},{destination['longitude']}", "travelmode": mode,
    }
    if route_uses_places(origin, destination):
        parameters.update(origin_place_id=origin["placeId"], destination_place_id=destination["placeId"])
    return "https://www.google.com/maps/dir/?" + urlencode(parameters)


def route_uses_places(origin, destination):
    return all(isinstance(point.get("placeId"), str) and
               re.fullmatch(r"[A-Za-z0-9_-]{1,255}", point["placeId"]) for point in (origin, destination))


class GoogleProjectMaps:
    def _request(self, endpoint, fields, body):
        key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
        if not key:
            raise ValueError("Google Maps is not configured. Set GOOGLE_MAPS_API_KEY on the server.")
        request = Request(endpoint, data=json.dumps(body, allow_nan=False).encode(), headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields,
        }, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("Provider response exceeded the limit.")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Invalid provider response.")
            return result
        except HTTPError as exc:
            reasons = {
                "SERVICE_DISABLED": "Enable this API in the Google Cloud project owning the key.",
                "API_KEY_SERVICE_BLOCKED": "Allow this API in the key's API restrictions.",
                "API_KEY_HTTP_REFERRER_BLOCKED": "Use a server key with suitable IP restrictions, not website referrers.",
                "API_KEY_IP_ADDRESS_BLOCKED": "Allow the server's outbound public IP in the key restrictions.",
                "BILLING_DISABLED": "Enable billing for the Google Cloud project.",
                "API_KEY_INVALID": "The configured API key is invalid.",
            }
            reason = None
            try:
                error_data = exc.read(65537)
                if len(error_data) <= 65536:
                    details = json.loads(error_data).get("error", {}).get("details", [])
                    reason = next((detail.get("reason") for detail in details
                        if isinstance(detail, dict) and detail.get("reason") in reasons), None)
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            if reason:
                service = "Routes API" if endpoint.startswith("https://routes.googleapis.com/") else "Places API"
                raise ValueError(f"Google {service} request failed (HTTP {exc.code}, {reason}). {reasons[reason]}") from None
            raise ValueError(f"Google Maps request failed (HTTP {exc.code}). Check API access, billing and quota.") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise ValueError("Google Maps is temporarily unavailable. Retry the lookup.") from None

    def _place(self, raw):
        if not isinstance(raw, dict) or not isinstance(raw.get("displayName"), dict):
            raise ValueError("Invalid Google Maps place response.")
        place_id = raw.get("id")
        name = (raw.get("displayName") or {}).get("text")
        if not isinstance(place_id, str) or not place_id or len(place_id) > 255 or not isinstance(name, str):
            raise ValueError("Invalid Google Maps place response.")
        return {"placeId": place_id, "name": name[:255], "address": str(raw.get("formattedAddress", ""))[:500],
                "location": coordinates(raw.get("location")), "sourceUrl": map_link(place_id, name),
                "businessStatus": raw.get("businessStatus", "UNKNOWN"), "types": raw.get("types", []),
                "attributions": [str(item.get("provider", ""))[:255] for item in raw.get("attributions", [])]}

    def search(self, query):
        if not isinstance(query, str) or not 3 <= len(query.strip()) <= 500:
            raise ValueError("Enter a project name and locality (3 to 500 characters).")
        response = self._request("https://places.googleapis.com/v1/places:searchText",
                                 ",".join("places." + field for field in PLACE_FIELDS.split(",")),
                                 {"textQuery": query.strip(), "pageSize": 5, "regionCode": "IN", "languageCode": "en"})
        return [self._place(raw) for raw in response.get("places", [])[:5]]

    def nearby(self, origin, kind, radius):
        if kind not in {"railway", "metro"} or radius not in {5000, 10000, 20000}:
            raise ValueError("Invalid station search scope.")
        response = self._request("https://places.googleapis.com/v1/places:searchNearby",
                                 ",".join("places." + field for field in PLACE_FIELDS.split(",")), {
                                     "includedTypes": ["train_station" if kind == "railway" else "subway_station"],
                                     "maxResultCount": 5, "rankPreference": "DISTANCE",
                                     "locationRestriction": {"circle": {"center": coordinates(origin), "radius": radius}},
                                 })
        places = [self._place(raw) for raw in response.get("places", [])[:5]]
        return [place | {"kind": kind, "serviceStatus": "existing_candidate", "statusSourceUrl": place["sourceUrl"]}
                for place in places if place["businessStatus"] not in {"CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY", "FUTURE_OPENING"}
                and (kind != "railway" or "subway_station" not in place["types"])]

    def details(self, place_id):
        if not isinstance(place_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", place_id):
            raise ValueError("Invalid Google place ID.")
        key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
        if not key:
            raise ValueError("Google Maps is not configured. Set GOOGLE_MAPS_API_KEY on the server.")
        request = Request("https://places.googleapis.com/v1/places/" + quote(place_id, safe=""), headers={
            "X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACE_FIELDS,
        })
        try:
            with urlopen(request, timeout=10) as response:
                data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("Provider response exceeded the limit.")
            return self._place(json.loads(data))
        except (URLError, OSError, json.JSONDecodeError):
            raise ValueError("Google place details are unavailable. Check API access or retry.") from None

    def route(self, origin, destination, mode):
        if mode not in {"WALK", "DRIVE"}:
            raise ValueError("Choose walking or driving.")
        origin_location, destination_location = coordinates(origin), coordinates(destination)
        use_places = route_uses_places(origin, destination)
        link = route_link(origin, destination, "walking" if mode == "WALK" else "driving")
        provenance = {"directionsUrl": link, "endpointMode": "places" if use_places else "coordinates"}
        try:
            response = self._request("https://routes.googleapis.com/directions/v2:computeRoutes",
                                     "routes.distanceMeters,routes.duration", {
                                         "origin": {"placeId": origin["placeId"]} if use_places else {"location": {"latLng": origin_location}},
                                         "destination": {"placeId": destination["placeId"]} if use_places else {"location": {"latLng": destination_location}},
                                         "travelMode": mode, "computeAlternativeRoutes": False, "units": "METRIC",
                                     })
            routes = response.get("routes", [])
            if not routes:
                return {"status": "no_route", **provenance}
            distance, duration = routes[0].get("distanceMeters"), routes[0].get("duration", "")
            if type(distance) is not int or distance < 0 or not re.fullmatch(r"\d+(?:\.\d+)?s", duration):
                raise ValueError("Invalid route response.")
            seconds = float(duration[:-1])
            if not math.isfinite(seconds):
                raise ValueError("Invalid route duration.")
            return {"status": "available", "distanceMeters": distance, "minutes": round(seconds / 60, 1),
                    **provenance}
        except ValueError as exc:
            return {"status": "unavailable", **provenance, "error": str(exc)}


def metro_catalogue():
    records = getattr(settings, "PROJECT_METRO_CATALOGUE", [])
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError("Invalid metro catalogue configuration.")
    result = []
    for item in records:
        parsed = urlsplit(item.get("sourceUrl", ""))
        allowed = getattr(settings, "PROJECT_METRO_SOURCE_HOSTS", ())
        if not source_url(item.get("sourceUrl")) or parsed.hostname not in allowed:
            raise ValueError("Metro catalogue requires an approved authority source URL.")
        if item.get("serviceStatus") not in {"operational", "under_construction", "planned"}:
            raise ValueError("Metro catalogue status is invalid.")
        from django.utils.dateparse import parse_date
        if not isinstance(item.get("asOf"), str) or not parse_date(item["asOf"]) or not item.get("name"):
            raise ValueError("Metro catalogue requires a station name and evidence date.")
        if parse_date(item["asOf"]) > timezone.localdate():
            raise ValueError("Metro evidence cannot be future-dated.")
        result.append({"name": str(item["name"])[:255], "location": coordinates(item.get("location")),
                       "placeId": item.get("googlePlaceId"),
                       "kind": "metro", "serviceStatus": item["serviceStatus"], "line": str(item.get("line", ""))[:128],
                       "statusSourceUrl": item["sourceUrl"], "sourceUrl": item["sourceUrl"], "asOf": item["asOf"]})
    return result


def nearby_catalogue(origin, radius, *, records=None):
    from math import asin, cos, radians, sin, sqrt
    def distance(item):
        location = item["location"]
        latitude_delta = radians(location["latitude"] - origin["latitude"])
        longitude_delta = radians(location["longitude"] - origin["longitude"])
        value = sin(latitude_delta / 2) ** 2 + cos(radians(origin["latitude"])) * cos(radians(location["latitude"])) * sin(longitude_delta / 2) ** 2
        return 6371000 * 2 * asin(min(1, sqrt(value)))
    candidates = [(distance(item), item) for item in (metro_catalogue() if records is None else records)]
    return [item for meters, item in sorted(candidates, key=lambda pair: pair[0]) if meters <= radius][:5]


def station_routes(place, radius, maps=None):
    from concurrent.futures import ThreadPoolExecutor
    maps = maps or GoogleProjectMaps()
    if radius not in {5000, 10000, 20000}:
        raise ValueError("Invalid station search radius.")
    origin = coordinates(place["location"])
    stations, errors = [], []
    for kind in ("railway", "metro"):
        try:
            stations.extend(maps.nearby(origin, kind, radius))
        except ValueError as exc:
            errors.append(str(exc))
    authority_stations = nearby_catalogue(origin, radius)
    authority_ids = {station["placeId"] for station in authority_stations if station.get("placeId")}
    stations = [station for station in stations if station.get("placeId") not in authority_ids]
    stations.extend(authority_stations)
    from .map_catalogue import map_stations

    catalogue = map_stations(include_saved=False)
    authority_names = {station["name"].strip().casefold() for station in authority_stations}
    user_stations = nearby_catalogue(origin, radius, records=[station for station in catalogue["stations"]
        if station["source"] == "user_candidate" and station["kind"] == "metro"
        and station["name"].strip().casefold() not in authority_names])
    stations.extend(user_stations)
    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = [(station, mode, executor.submit(maps.route, origin | {"placeId": place.get("placeId")},
            station["location"] | {"placeId": station.get("placeId")}, travel))
                for station in stations for mode, travel in (("walking", "WALK"), ("driving", "DRIVE"))]
        for station, mode, future in jobs:
            station[mode] = future.result()
    groups = []
    for kind, upcoming, label in (("railway", False, "Railway stations"), ("metro", False, "Existing metro candidates"),
                                   ("metro", True, "Upcoming metro"), ("metro", None, "Unverified metro candidates")):
        candidates = [station for station in stations if station["kind"] == kind and
                      ((station.get("source") == "user_candidate") if upcoming is None else
                       station.get("source") != "user_candidate" and
                       (station["serviceStatus"] in {"planned", "under_construction"}) == upcoming)]
        nearest = {}
        for mode in ("walking", "driving"):
            routed = [station for station in candidates if station[mode]["status"] == "available"]
            nearest[mode] = min(routed, key=lambda station: station[mode]["distanceMeters"])["name"] if routed else None
        groups.append({"label": label, "stations": candidates, "nearest": nearest})
    return {"groups": groups, "errors": errors, "radiusMeters": radius, "retrievedAt": timezone.now().isoformat(),
            "candidateCount": len(user_stations), "originPlaceId": place.get("placeId"),
            "isNearestVerified": False, "catalogueConfigured": bool(getattr(settings, "PROJECT_METRO_CATALOGUE", []))}


def registry_lookup(number):
    number = str(number).strip().upper()
    if not REGISTRATION.fullmatch(number):
        raise ValueError("Enter a valid MahaRERA project registration number.")
    result = fetch(backend(), "registration", number, maharera=True)
    if result["status"] == "available" and result["data"].get("registrationNumber") != number:
        return {"status": "unavailable", "reasonCode": "REGISTRATION_MISMATCH", "data": None}
    return result