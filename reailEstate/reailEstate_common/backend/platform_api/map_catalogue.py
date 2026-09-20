import csv
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings

from .external_sources import coordinates, metro_catalogue
from .models import ReviewTask, StationCandidate


def requested_connections(stations, lines):
    endpoints = [
        ("Line 9", {"miragaon"}, "Line 9", {"dahisar east"}),
        ("Line 7", {"gundavali"}, "Line 7A", {"airport colony"}),
        ("Line 4", {"kasarvadavali", "kasarwadavli"}, "Line 4A", {"gowniwada"}),
    ]
    existing = {frozenset(pair) for line in lines
                for pair in zip(line["stationIds"], line["stationIds"][1:])}
    connections = []
    for start_line, start_names, end_line, end_names in endpoints:
        matches = [
            [station for station in stations.values()
             if station["source"] == "user_candidate" and station["kind"] == "metro"
             and station["line"].casefold() == line.casefold()
             and station["name"].casefold() in names]
            for line, names in ((start_line, start_names), (end_line, end_names))
        ]
        if any(len(group) != 1 for group in matches):
            continue
        station_ids = [group[0]["id"] for group in matches]
        if frozenset(station_ids) in existing:
            continue
        connections.append({
            "id": str(uuid5(NAMESPACE_URL, repr(("requested_connection", station_ids)))),
            "kind": "metro", "name": end_line, "lineNames": list(dict.fromkeys([start_line, end_line])),
            "stationIds": station_ids, "geometryStatus": "schematic_unverified", "source": "user_requested",
        })
    return connections


def map_stations(*, include_saved=True):
    stations = {}
    lines = []
    warnings = []

    def add(record, source, *, as_of=None):
        if not isinstance(record, dict) or record.get("kind") not in {"metro", "railway"}:
            return
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            return
        try:
            location = coordinates(record.get("location"))
        except (TypeError, ValueError):
            return
        line = str(record.get("line", ""))[:128]
        identity = (record["kind"], name.strip().casefold(), line.casefold(), location["latitude"], location["longitude"])
        key = str(uuid5(NAMESPACE_URL, repr(identity)))
        service_status = record.get("serviceStatus") if source == "authority" else "unverified"
        stations[key] = {
            "id": key, "name": name.strip()[:255], "kind": record["kind"], "line": line,
            "location": location, "serviceStatus": service_status, "source": source,
            "asOf": record.get("asOf") if source == "authority" else as_of,
            "sourceUrl": record["sourceUrl"] if source == "authority" else None,
        }
        if record.get("candidateId"):
            stations[key]["candidateId"] = record["candidateId"]
        return key

    candidate_file = getattr(settings, "PROJECT_MAP_STATION_CANDIDATES_FILE", "")
    if candidate_file:
        try:
            path = Path(candidate_file)
            if path.stat().st_size > 1_000_000:
                raise ValueError("Candidate file too large.")
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            if len(rows) > 1000:
                raise ValueError("Too many candidates.")
            candidates = {}
            candidate_order = []
            for row in rows:
                try:
                    location = coordinates({"latitude": float(row["latitude"]), "longitude": float(row["longitude"])})
                    name, line = row["name"].strip(), row["line"].strip()
                    kind = row.get("kind", "metro").strip()
                    if not name or kind not in {"metro", "railway"}:
                        raise ValueError("Invalid candidate.")
                except (KeyError, TypeError, ValueError, AttributeError):
                    warnings.append("invalid_candidate")
                    candidate_order.append(None)
                    continue
                identity = (kind, name.casefold(), line.casefold())
                candidate_order.append(identity)
                candidates.setdefault(identity, {})[(location["latitude"], location["longitude"])] = {
                    "kind": kind, "name": name, "line": line, "location": location,
                }
            candidate_ids = {}
            for identity, locations in candidates.items():
                if len(locations) != 1:
                    warnings.append("conflicting_candidates")
                    continue
                candidate_ids[identity] = add(next(iter(locations.values())), "user_candidate")
            segments = []
            segment = []
            previous_group = None
            for identity in candidate_order:
                station_id = candidate_ids.get(identity)
                group = (identity[0], identity[2]) if station_id and identity[2] else None
                if group is None:
                    segment = []
                    previous_group = None
                    continue
                if group == previous_group and segment and station_id == segment[-1]:
                    continue
                if group != previous_group or station_id in segment:
                    segment = []
                    segments.append((identity[0], stations[station_id]["line"], segment))
                segment.append(station_id)
                previous_group = group
            lines = [{"id": str(uuid5(NAMESPACE_URL, repr(station_ids))), "kind": kind,
                      "name": name, "stationIds": station_ids,
                      "geometryStatus": "schematic_unverified", "source": "user_candidate"}
                     for kind, name, station_ids in segments if len(station_ids) >= 2]
        except (OSError, UnicodeError, csv.Error, ValueError):
            warnings.append("candidates_unavailable")
    else:
        segment, previous_group = [], None
        for candidate in StationCandidate.objects.all():
            group = (candidate.kind, candidate.line)
            if group != previous_group or not candidate.active or candidate.needs_review:
                segment = []
            previous_group = group
            if not candidate.active or candidate.needs_review:
                continue
            station_id = add({"name": candidate.name, "kind": candidate.kind, "line": candidate.line,
                "location": {"latitude": float(candidate.latitude), "longitude": float(candidate.longitude)},
                "candidateId": str(candidate.pk)}, "user_candidate")
            if not station_id or not candidate.line:
                segment = []
                continue
            segment.append(station_id)
            if len(segment) == 2:
                lines.append({"id": str(uuid5(NAMESPACE_URL, repr(segment))), "kind": candidate.kind,
                    "name": candidate.line, "stationIds": segment, "geometryStatus": "schematic_unverified",
                    "source": "user_candidate"})

    references = ReviewTask.objects.filter(
        project__status="published", task_type="external_sources", state="resolved",
        resolution__reviewState="accepted", resolution__stationRoutes__isnull=False,
    ).order_by("project_id", "-created_at", "-pk")
    seen_projects = set()
    for task in references.iterator() if include_saved else ():
        if task.project_id in seen_projects:
            continue
        seen_projects.add(task.project_id)
        routes = task.resolution.get("stationRoutes")
        if not isinstance(routes, dict) or not isinstance(routes.get("groups"), list):
            continue
        for group in routes["groups"][:4]:
            if not isinstance(group, dict) or not isinstance(group.get("stations"), list):
                continue
            for station in group["stations"][:15]:
                if isinstance(station, dict) and (station.get("candidateId") or station.get("source") == "user_candidate"):
                    continue
                add(station, "saved_reference", as_of=routes.get("retrievedAt"))
    try:
        for station in metro_catalogue():
            add(station, "authority")
    except (ValueError, TypeError, AttributeError):
        warnings.append("authority_catalogue_unavailable")
    return {"stations": sorted(stations.values(), key=lambda item: (item["kind"], item["name"], item["line"])),
            "lines": lines,
            "connections": requested_connections(stations, lines),
            "warnings": sorted(set(warnings))}