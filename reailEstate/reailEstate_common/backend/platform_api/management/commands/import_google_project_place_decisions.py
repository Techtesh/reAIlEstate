"""Import project-bound Google Maps share links as unreviewed references."""

import hashlib
import json
import math
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from filelock import FileLock, Timeout

from platform_api.external_sources import GoogleProjectMaps, coordinates
from .refresh_google_project_places import active_projects, create_reference, existing_reference, read_report, write_report


PLACE_COORDINATES = re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)")
TERMINAL = {"imported", "skipped", "skipped_existing", "skipped_unavailable"}


def distance_meters(first, second):
    first, second = coordinates(first), coordinates(second)
    latitude_first, latitude_second = math.radians(first["latitude"]), math.radians(second["latitude"])
    value = math.sin((latitude_second - latitude_first) / 2) ** 2 + math.cos(latitude_first) * math.cos(latitude_second) * math.sin(math.radians(second["longitude"] - first["longitude"]) / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(value)))


def validate_maps_url(value, *, initial=False):
    try:
        parsed = urlsplit(value)
        allowed = {"maps.app.goo.gl"} if initial else {"maps.app.goo.gl", "google.com", "www.google.com", "maps.google.com"}
        if (len(value) > 8192 or any(ord(char) <= 32 for char in value) or "\\" in value
                or parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username is not None
                or parsed.password is not None or parsed.port not in {None, 443}
                or parsed.hostname != "maps.app.goo.gl" and not (parsed.path.startswith("/maps/")
                    or parsed.path == "/maps" or parsed.hostname == "maps.google.com" and parsed.path in {"", "/"})):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Only HTTPS Google Maps URLs without credentials or custom ports are allowed.") from None
    return parsed


class MapsRedirectHandler(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, request, response, code, message, headers, new_url):
        validate_maps_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def resolve_share_url(value):
    validate_maps_url(value, initial=True)
    try:
        with build_opener(MapsRedirectHandler()).open(Request(value, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as response:
            final = validate_maps_url(response.geturl())
    except (HTTPError, URLError, TimeoutError, OSError):
        raise ValueError("Google Maps share link could not be resolved.") from None
    matches = PLACE_COORDINATES.findall(unquote(final.path))
    parts = final.path.split("/maps/place/", 1)
    if len(matches) != 1 or len(parts) != 2:
        raise ValueError("Share link must identify one place with actual place coordinates, not viewport coordinates.")
    name = unquote(parts[1].split("/", 1)[0]).replace("+", " ").strip()
    if not name or len(name) > 255:
        raise ValueError("Share link has no valid place name.")
    latitude, longitude = matches[0]
    return {"name": name, "location": coordinates({"latitude": float(latitude), "longitude": float(longitude)})}


class Command(BaseCommand):
    help = "Resolve target-project Google Maps share decisions and submit place IDs for review (maximum five per run)."

    def add_arguments(self, parser):
        parser.add_argument("decisions", type=Path)
        parser.add_argument("--source-report", type=Path, required=True)
        parser.add_argument("--report", type=Path, required=True)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--batch-size", type=int, default=5)

    def handle(self, *args, **options):
        if not options["execute"]:
            self.stdout.write("Preview only: add --execute to resolve links and create unreviewed references.")
            return
        if not getattr(settings, "GOOGLE_MAPS_API_KEY", ""):
            raise CommandError("GOOGLE_MAPS_API_KEY is not configured in this process.")
        if not 1 <= options["batch_size"] <= 5:
            raise CommandError("--batch-size must be between 1 and 5.")
        path = options["report"].expanduser().resolve()
        if path in {options["decisions"].resolve(), options["source_report"].resolve()}:
            raise CommandError("Output report must not overwrite decisions or the source report.")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(path.with_suffix(".lock")), timeout=0):
                self._run(path, options)
        except Timeout:
            raise CommandError("Another import is already using this report.") from None

    def _run(self, path, options):
        try:
            if options["decisions"].stat().st_size > 2_000_000:
                raise ValueError
            decisions = json.loads(options["decisions"].read_text(encoding="utf-8"))
            if not isinstance(decisions, list) or not all(isinstance(row, dict) for row in decisions):
                raise ValueError
        except (OSError, ValueError):
            raise CommandError("Decisions must be a JSON array smaller than 2 MB.") from None
        source = read_report(options["source_report"])
        rows = [row for row in source.get("projects", []) if row.get("status") == "no_match" and 2 <= row.get("candidateCount", 0) <= 5]
        if (len(decisions) != len(rows) or [row.get("index") for row in decisions] != list(range(1, len(rows) + 1))
                or any(decision.get("projectId") != row["projectId"] or decision.get("action") not in {"skip", "select"}
                    for decision, row in zip(decisions, rows))):
            raise CommandError("Decision indexes and projectId values must match this workspace's source report; use action select or skip.")
        fingerprint = hashlib.sha256(json.dumps([rows, decisions], sort_keys=True).encode()).hexdigest()
        old = read_report(path) if options["resume"] else {}
        if old and old.get("inputDigest") != fingerprint:
            raise CommandError("Checkpoint belongs to different inputs. Use a new report path.")
        previous = {row["index"]: row for row in old.get("decisions", [])}
        report = {"state": "running", "inputDigest": fingerprint, "routesRequested": False, "decisions": list(previous.values())}
        resolved_cache = {row["urlDigest"]: (row["distanceMeters"], {"placeId": row["placeId"]})
            for row in previous.values() if row.get("status") == "imported"}
        attempted = 0
        maps = GoogleProjectMaps()
        for decision, source_row in zip(decisions, rows):
            index, project_id = decision["index"], source_row["projectId"]
            if previous.get(index, {}).get("status") in TERMINAL:
                continue
            row = {"index": index, "projectId": project_id}
            project = active_projects().filter(pk=project_id).first()
            if decision["action"] == "skip":
                row["status"] = "skipped"
            elif not project:
                row["status"] = "skipped_unavailable"
            elif existing_reference(project):
                row["status"] = "skipped_existing"
            elif attempted >= options["batch_size"]:
                report["state"] = "paused"
                break
            else:
                attempted += 1
                url = decision.get("url", "")
                digest = hashlib.sha256(str(url).encode()).hexdigest()
                row["urlDigest"] = digest
                try:
                    validate_maps_url(url, initial=True)
                    if digest not in resolved_cache:
                        resolved = resolve_share_url(url)
                        places = maps.search(f"{resolved['name']}, {project.locality}, Maharashtra, India")
                        ranked = sorted(((distance_meters(resolved["location"], place["location"]), place) for place in places), key=lambda item: item[0])
                        if not ranked or ranked[0][0] > 500 or len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
                            raise ValueError("No unique nearest Places candidate within 500 metres of the reviewed link.")
                        resolved_cache[digest] = ranked[0]
                    distance, place = resolved_cache[digest]
                    task = create_reference(project, place["placeId"], "reviewed_google_maps_share", sourceUrlDigest=digest)
                    row.update(status="imported", distanceMeters=round(distance), placeId=task.resolution["googlePlaceId"], reviewTaskId=str(task.pk))
                except ValueError as exc:
                    row.update(status="failed", error=str(exc)[:300])
            report["decisions"] = [item for item in report["decisions"] if item["index"] != index] + [row]
            write_report(path, report)
        else:
            report["state"] = "completed"
        report["counts"] = {state: sum(row["status"] == state for row in report["decisions"])
            for state in {row["status"] for row in report["decisions"]}}
        write_report(path, report)
        self.stdout.write(f"Manual Google decisions {report['state']}: {report['counts']}")