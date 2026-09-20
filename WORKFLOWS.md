# MMR Homefinder Workflows

This is the source of truth for running, operating, testing, and changing the
MMR Homefinder application. Detailed subsystem references remain in
[`reailEstate/MAINTAINERS.md`](reailEstate/MAINTAINERS.md), the
[`backend README`](reailEstate/reailEstate_common/backend/README.md), and the
[`frontend README`](reailEstate/reailEstate_common/frontend/README.md).

## Runtime map

| Component | Port | Owns |
| --- | ---: | --- |
| Admin service | 8002 | Django admin, independent review, publication, operational APIs, migrations |
| Builder service | 8003 | Builder authentication, organizations, project editing, uploads, media and layouts |
| Buyer service | 8004 | Public catalogue, buyer accounts, requirements, matches, comparisons, shortlists |
| Shared worker | None | Durable document, media-evaluation, and recommendation events |
| Buyer Vite development server | 5173 | Optional hot-reload UI; proxies `/api` to buyer service on 8004 |
| Builder Vite development server | 5174 | Optional hot-reload UI; proxies `/api` to builder service on 8003 |
| PostgreSQL | Configuration-dependent | Shared durable application state |

The three Django entrypoints are intentionally separate, but share the
`platform_api` domain, migrations, PostgreSQL schema, and media store. The
browser must use the API owned by its service; do not point buyer development
traffic at the admin service.

## 1. First-time local setup

Prerequisites:

- PowerShell 7
- Python and the existing virtual environment at
  `reailEstate/reailEstate_common/backend/.venv`
- Node.js 22.12 or newer and npm
- A reachable PostgreSQL database
- Optional local provider configuration kept outside version control

Install dependencies only for a new checkout or after dependency files change:

```powershell
$Python = '.\reailEstate\reailEstate_common\backend\.venv\Scripts\python.exe'
& $Python -m pip install -r .\reailEstate\reailEstate_common\backend\requirements.txt
npm --prefix .\reailEstate\reailEstate_common\frontend ci
```

Database configuration comes from the environment or the existing ignored
local database configuration. Supported PostgreSQL variables are
`PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGHOST`, `PGPORT`, and `PGSSLMODE`.
Runtime startup must not recreate the database, reimport samples, or publish
projects.

For AI-assisted floor-plan extraction, copy
`vision-profiles.example.json` to the ignored `vision-profiles.local.json`
beside it. If more than one profile is enabled, set
`FLOOR_PLAN_DEFAULT_VISION_PROFILE`.

## 2. Local service lifecycle

Run lifecycle commands from the repository root.

### Start the application

This builds the buyer and builder bundles, applies pending migrations through
the admin service, then starts the three web services and shared worker:

```powershell
.\reailEstate\manage_services.ps1 -Action start
```

Use `-SkipBuild` only when the current bundles are already known to match the
source. Use `-SkipMigrations` only when migration state was checked separately.
Logs are written under the ignored `reailEstate/.runtime/logs` directory.

### Inspect status

```powershell
.\reailEstate\manage_services.ps1 -Action status
```

Health endpoints:

- Admin: `http://127.0.0.1:8002/health/live` and `/health/ready`
- Builder: `http://127.0.0.1:8003/health/live` and `/health/ready`
- Buyer: `http://127.0.0.1:8004/health/live` and `/health/ready`

Process IDs alone do not prove worker health. Use the admin queue/worker view and
the persisted worker heartbeat for operational decisions.

### Stop or restart

```powershell
.\reailEstate\manage_services.ps1 -Action stop
.\reailEstate\manage_services.ps1 -Action restart
```

These default commands manage admin, builder, buyer, and worker processes from
this checkout only. PostgreSQL is not stopped.

The optional Vite process is managed explicitly:

```powershell
.\reailEstate\manage_services.ps1 -Action start -Service frontend -SkipBuild -SkipMigrations
.\reailEstate\manage_services.ps1 -Action stop -Service frontend
```

Use `-Service all` to include Vite with all application processes. A process
from another checkout occupying a canonical port is reported and is never
silently terminated.

### Foreground troubleshooting

Use these when interactive output or a debugger is required:

```powershell
.\reailEstate\build_service_frontends.ps1
.\reailEstate\run_service.ps1 -Service admin
.\reailEstate\run_service.ps1 -Service builder
.\reailEstate\run_service.ps1 -Service buyer
.\reailEstate\run_worker.ps1
```

Each command occupies its terminal and stops with Ctrl+C.

### Frontend hot reload

Start the corresponding Django service first, then:

```powershell
npm --prefix .\reailEstate\reailEstate_common\frontend run dev:buyer
npm --prefix .\reailEstate\reailEstate_common\frontend run dev:builder
```

Use `http://127.0.0.1:5173` for buyer development and
`http://127.0.0.1:5174` for builder development. Keep the hostname consistent
so session and CSRF cookies remain same-origin.

## 3. Compose lifecycle

Compose owns PostgreSQL, migration, web-service, worker, and media-volume
lifecycle. Supply secrets through the environment:

```powershell
$env:POSTGRES_PASSWORD = '<private value>'
$env:DJANGO_SECRET_KEY = '<private value>'
docker compose -f .\reailEstate\compose.services.yml up --build -d
docker compose -f .\reailEstate\compose.services.yml ps
docker compose -f .\reailEstate\compose.services.yml logs --tail=100 admin builder buyer worker
docker compose -f .\reailEstate\compose.services.yml down
```

The one-shot `migrate` container must complete successfully before the other
services start. ClamAV and OpenTelemetry remain disabled unless explicitly
configured.

## 4. Buyer workflows

| ID | Workflow | Steps and outcome |
| --- | --- | --- |
| B1 | Browse published projects | Open `/` or `/browse`; filter and sort the public catalogue; open project details and approved media. Unreviewed project coordinates are withheld. |
| B2 | Inspect the map | Switch to map/split view or select **On map**. Reviewed projects render as project pins; transit stations and explicitly unverified candidates retain distinct labels. |
| B3 | Sign in/out | Use the buyer login flow. Sign-out and session expiry clear private in-memory state and cancel pending private requests. |
| B4 | Create requirements | Use `/requirements` to enter home, destination, amenity, budget, locality, and priority data. Priorities may total 1–100 points and never auto-redistribute. |
| B5 | Save a draft | Grant consent, then save. Optimistic concurrency protects a draft from overwriting a newer version. |
| B6 | Submit for matching | Submit a valid profile. The API creates a durable recommendation event and operation; the UI polls until the immutable snapshot is ready or fails. |
| B7 | Review matches | Open `/matches/<snapshot-id>` to inspect ranks, component scores, evidence confidence, and hard-constraint exclusions. |
| B8 | Compare projects | Select two to four published projects, request the comparison, and optionally share the generated comparison token. |
| B9 | Shortlist projects | Add/remove published projects. `/shortlist` renders reviewed shortlisted projects with distinct pins. |
| B10 | Add commute destinations | Enter locality-only or exact private destinations. Exact coordinates render only on the authenticated shortlist map; locality-only entries are explicitly reported as unmapped. |
| B11 | Reuse a profile | Open `/profiles`, resume a draft, or create a new draft from a submitted profile. |
| B12 | Privacy actions | Export or delete account data through the protected account endpoints. Shared requirement/comparison links expose only their scoped payloads. |

## 5. Builder workflows

| ID | Workflow | Steps and outcome |
| --- | --- | --- |
| D1 | Builder sign-in | Sign in at `/builder/login`. Access requires an active owner/editor membership in an active organization. |
| D2 | Select organization/project | Choose an authorized organization, then create or open one of its projects. Object-level authorization is checked on every request. |
| D3 | Edit project metadata | Save name, registration, locality, address, and processing attestation using `expectedUpdatedAt`. Conflicts require reload rather than overwrite. |
| D4 | Submit a location | Enter coordinates or resolve a Google Maps reference. This creates an unreviewed proposal; it does not publish coordinates. |
| D5 | Upload evidence | Create an upload intent, upload the exact declared bytes, then complete the document. Hash, media type, size, malware, and idempotency checks apply. |
| D6 | Monitor ingestion | Inspect the project queue and operation status. The persistent worker performs extraction outside the HTTP request. |
| D7 | Edit media | Crop pages, save drafts, attach/discard crops, rename assets, set active state, restore/remove records, or choose a project picture. |
| D8 | Edit layouts | Edit layout name, configuration, carpet area, and total INR price with optimistic concurrency. Layout prices drive project min/max pricing. |
| D9 | Evaluate layouts | Queue or requeue extraction for eligible layouts and inspect durable job status. Generated results remain review proposals. |
| D10 | Submit for review | Attest processing and submit the project. Publication remains an independent reviewer/admin action. |
| D11 | Archive project | Archive through the version-checked endpoint. Archived or merged projects cannot be edited or processed. |

## 6. Reviewer and administrator workflows

| ID | Workflow | Steps and outcome |
| --- | --- | --- |
| A1 | Provision access | Create users and organizations, then assign active owner/editor memberships. Buyer or staff status alone does not grant builder membership. |
| A2 | Review extraction | Inspect immutable source evidence, candidates, conflicts, and extraction outputs before resolving review tasks. |
| A3 | Review floor plans/layouts | Approve, reject, correct, activate, or deactivate plans. Deactivating a floor plan also deactivates its derived layouts without deleting evidence. |
| A4 | Review project locations | Open **External sources**, verify the saved reference, and explicitly accept it. Published APIs receive coordinates only after acceptance. Enforced second-review policy prohibits self-approval when enabled. |
| A5 | Enrich station routes | After a location is accepted, fetch or refresh bounded station-route evidence. Candidate stations remain labelled unverified until separately reviewed. |
| A6 | Publish a project | Resolve required review gates and publish. Approved active layout prices become the project price range; otherwise the buyer UI shows `contact to know`. |
| A7 | Reprocess evidence | Supply a reason and queue document processing. The command is idempotent and refuses an already-pending event. |
| A8 | Reanalyse a project | Queue eligible project documents/layouts for reanalysis without deleting prior evidence. |
| A9 | Merge projects | Select an active canonical target and reason. Merge state prevents edits and processing on the retired source. |
| A10 | Operate the platform | Inspect integration health, operational metrics, audit events, queue workers, retry state, and dead letters. Replay requires an explicit reason. |

## 7. Worker workflows

The persistent worker started by `run_worker.ps1` is the normal local runtime. It
claims PostgreSQL-backed capacity and handles:

1. `document.processing.requested`
2. `source.page_evaluation.requested`
3. `floor_plan.crop_evaluation.requested`
4. `floor_plan.units_evaluation.requested`
5. `recommendation.computation.requested`

Use `process_outbox` only for bounded diagnostics or deliberate maintenance:

```powershell
$Python = '.\reailEstate\reailEstate_common\backend\.venv\Scripts\python.exe'
& $Python .\reailEstate\adminService\manage.py process_outbox --settings=admin_service.settings --once --limit 5
& $Python .\reailEstate\adminService\manage.py process_outbox --settings=admin_service.settings --event-type extraction --project-id <uuid> --drain --limit 5
& $Python .\reailEstate\adminService\manage.py process_outbox --settings=admin_service.settings --event-type recommendations --once --limit 5
```

Do not run overlapping persistent dispatchers. Do not treat browser polling as a
worker.

## 8. Data and maintenance commands

Run command help before any write:

```powershell
$Python = '.\reailEstate\reailEstate_common\backend\.venv\Scripts\python.exe'
& $Python .\reailEstate\adminService\manage.py help <command> --settings=admin_service.settings
```

| Command | Purpose | Safety |
| --- | --- | --- |
| `cleanup_unreferenced_images` | Preview orphaned generated images | Deletes only with `--execute` |
| `compare_plan_modes` | Compare script and vision output | Writes a new local report; no DB writes |
| `classify_plan_assets_ai` | Create AI-classified review proposals | Provider calls; originals remain unchanged |
| `enrich_google_project_routes` | Prefetch routes or accept bounded project locations | Acceptance requires explicit `--execute` |
| `export_demo_sqlite` | Produce a sanitized portable snapshot from PostgreSQL | Writes a new file; never converts the live DB |
| `fetch_maharera_project` | Fetch one exact registry record | May require manual CAPTCHA |
| `import_google_project_place_decisions` | Submit saved place decisions for review | Does not bypass review |
| `import_sample_corpus` | Import supported project evidence | Write operation; use report/resume controls |
| `import_station_candidates` | Import station candidates | Does not overwrite existing edits |
| `isolate_floor_plan_units` | Create per-flat crop proposals | Local/development only |
| `process_outbox` | Consume a bounded or filtered outbox set | Changes durable work state |
| `rebuild_script_outputs` | Rebuild unreviewed script outputs | Requires backup and exclusive maintenance window |
| `refresh_google_project_places` | Submit strict place matches for review | Does not auto-publish locations |
| `refresh_sample_corpus` | Refresh a corpus with bounded concurrency | PostgreSQL locks; writes project data |
| `requeue_all_layout_evaluations` | Preview or force-queue eligible layouts | Writes only with its explicit force option |
| `run_extraction_dispatcher` | Run the persistent shared worker | PostgreSQL only; one dispatcher |
| `separate_all_floor_plan_units` | Generate missing script crops | Local/development only |
| `sync_feature_flags` | Preview registered feature flags | Creates only with `--apply` |
| `verify_database_transfer` | Compare transferred fixture data | Stores digests/counts, not raw sensitive data |

## 9. Test and validation workflows

Frontend:

```powershell
npm --prefix .\reailEstate\reailEstate_common\frontend test
npm --prefix .\reailEstate\reailEstate_common\frontend run lint
npm --prefix .\reailEstate\reailEstate_common\frontend run build:services
npm --prefix .\reailEstate\reailEstate_common\frontend run test:e2e
```

Backend:

```powershell
. .\reailEstate\configure_runtime.ps1
Set-RealEstateRuntimeEnvironment -ApplicationRoot (Resolve-Path .\reailEstate).Path
$env:DATABASE_BACKEND = 'sqlite'
$env:PROJECT_METRO_CATALOGUE_FILE = ''
Push-Location .\reailEstate\reailEstate_common\backend
try {
  & .\.venv\Scripts\python.exe -m pytest --tb=short
} finally {
  Pop-Location
}
```

Use the smallest relevant test files first. Tests may use isolated SQLite
configuration; application runtime and production workflows use PostgreSQL.
Always retain the service-boundary, authorization, idempotency, concurrency,
privacy, and publication-gate tests. The current full-suite baseline is recorded
in `reailEstate/MAINTAINERS.md`; do not treat its known failures as a green gate.

## 10. Deployment workflows

### Azure

Azure preparation and live validation are complete for the recorded target in
[`.azure/deployment-plan.md`](.azure/deployment-plan.md). Deployment remains
explicitly deferred.

Allowed without deployment approval:

```powershell
.\.deployments\azure\scripts\Initialize-AzureDeployment.ps1
```

Do not run Terraform apply, image pushes, migrations, or deployment smoke tests
without new approval.

### AWS

AWS artifacts are offline-validated, but no account or deployment is approved.

Allowed without deployment approval:

```powershell
.\.deployments\aws\scripts\Initialize-AwsDeployment.ps1
```

Do not run foundation/workload apply, ECR pushes, migrations, or deployment smoke
tests without explicit account selection and approval.

## 11. Change workflow

Before changing behavior:

1. Identify the owning service and authorization boundary.
2. Identify records, evidence, privacy classification, and audit impact.
3. Keep expensive/provider work in the durable worker.
4. Preserve idempotency and optimistic concurrency.
5. Reuse shared helpers before adding another implementation.
6. Add or update the narrowest regression test.
7. Build the affected frontend/service.
8. Verify the exact public or private output shape.
9. Update this catalogue when a route, role, event, command, or lifecycle changes.

Never bypass independent review, invent unavailable prices or coordinates, expose
private destinations publicly, or silently turn a failed operation into success.
