# MMR Real Estate Backend

Django/DRF implementation of the contracts in the workspace documentation. It includes session authentication, consent, immutable/versioned buyer requirements, developer organizations, secure upload quarantine, extraction/review boundaries, MahaRERA snapshots, evidence-backed publication, catalog search, deterministic recommendations, comparisons, shortlists/shares, notifications, audit records, operations, metrics, and health/OpenAPI endpoints.

Use the repository [`WORKFLOWS.md`](../../../WORKFLOWS.md) as the source of truth
for service lifecycle, role workflows, workers, maintenance, testing, and deployment
safety. This file remains the detailed backend command and subsystem reference.

The optional [buyer frontend](../frontend/README.md) development server runs on
port 5173 and proxies the buyer service on port 8004. The built buyer application
is served directly by the buyer service at port 8004.

## Builder Portal and Account Provisioning

Builders sign in at the frontend **`/builder/login`**, not Django `/admin`.
The portal lists only projects belonging to their active organizations. Active
`owner` and `editor` memberships grant project management; `viewer`, revoked or
suspended memberships do not. Membership and organization status are checked on
every request, including uploads and document access.

A **platform administrator** provisions accounts using Django admin:

1. Create an active organization (or select the existing one).
2. Create an active user with a securely supplied password. Leave **Staff status**
   and **Superuser status** unchecked, and do not grant reviewer/admin groups.
3. Reopen the saved user's edit page. Under **Builder organization access**, select
   the organization from the dropdown, choose `owner` or `editor`, and set status
   to `active`. Save the user. The separate Organization memberships screen is
   also available. A user may manage multiple assigned organizations.
4. Direct the builder to `http://127.0.0.1:5173/builder/login` locally, or the
   deployment's frontend `/builder/login` URL. Do not share administrator credentials.

Organization status (`active`, `inactive`, `suspended`) and verification state
(`pending`, `verified`, `rejected`) use model enums and admin dropdowns. Membership
roles and statuses also use enums; only active owner/editor memberships grant access.
Organization verification is separate from membership eligibility.

Users can also sign in on the homepage and choose **Buyer** or **Builder**.
The choice never grants permissions. A correct password without an eligible
membership produces an explicit access-assignment error on builder sign-in;
incorrect credentials still produce the generic invalid-credentials error.

There is no builder self-registration or ability to claim organizations, assign
memberships, change organization ownership, grant staff permissions or publish
projects. The legacy developer-organization API also restricts organization
creation/update/deletion to platform staff.

The builder API uses existing session cookies and CSRF protection:

| Endpoint | Methods | Purpose |
| --- | --- | --- |
| `/api/v1/auth/builder-login` | GET, POST | Initialize CSRF, then authenticate an assigned builder |
| `/api/v1/builder/organizations` | GET | Assigned active management organizations |
| `/api/v1/builder/projects` | GET, POST | List own projects or create in an assigned organization |
| `/api/v1/builder/projects/{id}` | GET, PATCH | Read/edit own project metadata |
| `/api/v1/builder/projects/{id}:archive` | POST | Retire a project without deleting evidence |
| `/api/v1/builder/projects/{id}/layouts` | GET | Eligible individual layouts |
| `/api/v1/builder/projects/{id}/layouts/{layoutId}` | PATCH | Edit a layout alias and optional total INR price |

Project edits/archive and layout edits require `expectedUpdatedAt` from the latest
object response; stale changes return HTTP 409. Organization IDs, verification,
publication state, evidence/provenance and staff permissions cannot be mass-assigned.
Project creation reuses registration normalization, duplicate protection and the
existing unavailable-verification fallback; it does not claim registry verification.
Private metadata and unrelated organizations/projects are not returned.

Document uploads reuse the existing upload-intent, binary content and idempotent
completion endpoints under `/api/v1/developer-projects/{id}`. Completion queues
scanning/extraction; the browser does not run a worker or invoke a model. Builders
submit through `:submit-review`; reviewers still approve evidence and publication
independently. Editing a published project or its layouts moves it back to
`needs_review`. Archiving preserves evidence and cancels pending extraction work
using the existing retirement service. Archived projects cannot be edited, queued
for reanalysis, uploaded to or resubmitted.

The development-only public preview flag described below still applies to buyer
media visibility; it does not grant project mutation or publishing rights.

## Admin Quick Links and Extraction Corrections

The admin sidebar includes **Active queue**, **My active cart**, **Maps: incomplete data**,
and **MahaRERA: missing ID**. The Maps queue includes active projects without an accepted
latest location reference or without coordinates; detailed lookup filters remain available.
Previous/Next and Back to queue preserve the originating project filters and ordering
on project details and External sources. Navigation never saves or commits a draft.
The cart shows the signed-in user's total private drafts across projects. Opening
these links does not launch evaluations or make provider requests.

Projects now show linked Google Maps and MahaRERA columns. Maps lookup categories
come from `output/reports/google-project-places.json`. Unresolved lookup filters
(ambiguous, no strict match, failed, and no lookup result) exclude projects that
now have a saved location reference. The historical report itself is unchanged.
**Awaiting review** shows active projects whose latest location reference is unreviewed.
**Needs lookup** reflects active projects currently without a saved Google reference,
so manually resolved projects leave that list. MahaRERA ID presence is not verification.

Under a project's **External sources**, **Direct project location** accepts either
latitude and longitude or an HTTPS Google Maps pin/share link. Coordinate pairs and
long pin links work without `GOOGLE_MAPS_API_KEY`; short `maps.app.goo.gl` links use
restricted Google-only redirects and require network access. Viewport-only and
directions links are rejected rather than treated as project coordinates.
Choose **Preview direct location**, inspect the pin, then **Commit source references**.
Manual and selected Google draft locations have a responsive 260px-high OpenStreetMap
preview. Only validated coordinates are sent to the embed, not draft tokens, project
names, registry evidence or the admin URL. OpenStreetMap receives the browser's IP
and coordinates; the frame uses no-referrer and requires network access. The Google
Maps link remains available. Only this admin page permits the OpenStreetMap frame.
The result is an unreviewed manual reference, not a Google Places identity match.
An authorized reviewer can **Apply reviewed location** without an API key.
This explicitly updates project coordinates, but never publishes the project or
calculates routes. Provider search and route calculation still require API configuration.

Browse remains published-only by default. `GET /api/v1/projects/?includeProcessing=true`
also returns restricted summaries for `ingestion` and `needs_review`, not private drafts,
retired or merged records. These summaries omit unpublished facts, configurations,
prices, amenities and registry claims. Coordinates require an accepted latest location
reference. Comparisons, shortlists and ranking remain published-only.

### Temporary public project preview

`PROJECT_PUBLIC_PREVIEW_ENABLED` is temporarily enabled by default with
`DJANGO_DEBUG=true`, and defaults to `false` in production. It accepts only `true`
or `false`. Set `PROJECT_PUBLIC_PREVIEW_ENABLED=false` and restart the backend to
restore normal guest restrictions. Disable it before sharing this development server
outside the intended audience: it deliberately exposes unreviewed project information.

When enabled, guests can read project details and the project gallery for published,
ingestion and needs-review projects. The gallery includes active visible layouts,
room measurements, and associated clean, ready PNG/JPEG/WebP media. Processing
responses also include available configurations, amenities and project facts.
It does not publish or approve records. Removed/inactive media, quarantined sources,
raw documents, internal address metadata, private buyer data and all write/admin
operations retain their existing protections. Browse's processing toggle remains off
by default.

Project list coordinates can also fall back to the latest saved unreviewed manual
location or the saved Google route origin with the same place ID. The API marks these
as `locationStatus: "provisional"`; the frontend labels them as unreviewed development
previews. Rejected, invalid or mismatched references are not used. No provider request,
coordinate write, approval or publication occurs. With preview disabled, the existing
published/reviewed coordinate rules still apply.

`GET /api/v1/projects/{id}/gallery` is cursor-paginated with presentation fields only;
image bytes use `/api/v1/media/floor-plan-assets/{id}`. Both enforce the same guest
visibility checks and use `private, no-store`. With the flag off, guests see only
approved media of published projects; authorized project editors/reviewers can still
view unreviewed processing media. No provider calls or ingestion jobs are started.

`PROJECT_LOCATION_REQUIRE_SECOND_APPROVER` controls whether the submitter may approve
their own project-location reference. It defaults to `false` with `DJANGO_DEBUG=true`
(single-person local workflow) and `true` with debug disabled. Set the environment
variable to `true` to require a different reviewer, or `false` to allow the same user;
restart the backend after changing the environment. Other values are rejected.
This applies to manual locations, Google Places approvals, and route enrichment
that accepts coordinates. Explicit confirmation, staff permissions, open-reference
and version checks still apply. Each approval records the policy and whether it
was self-approved. Project fact approval and publication rules are unchanged.

Use **Edit extraction** on a layout card or floor-plan record to add, correct, or
remove room rows and update configuration, counts, orientation, or carpet area.
Lengths and widths are in feet; carpet area is in square feet and is never inferred
from room rectangle sums. Empty dimensions remain unknown. A correction reason is
required. Raw machine extraction and image evidence remain unchanged; before/after
values are retained in an unreviewed floor-plan review task with an audit event.
These changes update the layout record, not published project facts or previously
generated output files. A later explicit extraction can replace these values.

Editing requires floor-plan change permission and project access, a clean ready
source, and an active layout. Stale edits and pending/running layout evaluations
are rejected. The generic floor-plan form displays extraction fields read-only;
use the structured editor to change them.

### Layout and project pricing

In **Project media -> Layouts**, enter **Total price (INR)** on an individual
layout and choose **Save price**. This is one optional total price per layout,
not a per-square-foot rate; it must be positive and have at most two decimal
places. Clear the field and save to remove the price. Price changes require
project and floor-plan change permissions, are audited, and reject stale edits.
The generic floor-plan admin shows pricing read-only and links to this editor.
Changing a price returns the layout to review; it does not approve or publish
evidence. The temporary public-preview policy can display unreviewed prices.

Project prices are calculated from the minimum and maximum available layout
prices, rather than manually maintained project/configuration prices. A single
known price is displayed once; when no eligible layout has a price, the project
shows **contact to know**. Missing prices are not treated as zero. Inactive and
removed layouts and inactive source pages do not contribute to the range.
The admin range includes reviewable layouts; public responses retain the
existing publication/review and public-preview visibility rules.

The admin project page combines the price range with a scrollable layout list:
saved previews, layout names, carpet areas in sq ft, total INR prices and review
labels. It uses the same eligible layouts as pricing; missing measurements and
prices remain `Unknown` and `contact to know`. Address and locality are collapsed
by default and expand automatically when either field has a validation error.

Layout prices are manually owned data, separate from extracted measurements.
Re-evaluating extraction must not overwrite an entered price. No prices are
backfilled from legacy configuration records.

Buyer `configurations` responses contain one row per eligible layout, using the
layout UUID and reviewer alias (`name`), not a merged BHK group. Layouts without a
known BHK type are retained. Carpet area and price remain specific to each layout.
`roomRectangleSumSqFt` separately sums available rectangular room measurements;
it is not certified carpet area and may include decks or overlapping spaces.
Evaluation and manual room corrections store `areaSqFt` as length times width,
rounded to two decimals. The API also calculates missing/stale room areas from
legacy dimensions on read, without changing evidence or triggering evaluation.
Gallery items expose `layoutIds` for eligible unit layouts derived from that image,
including saved crop ancestry. These IDs follow the same review/visibility policy
as layout pricing, work across gallery pagination, and do not expose raw provenance
or document IDs. The frontend uses them to scope the floor-plan comparison table.

### Buyer daily-commute locations

Private requirement profiles can store up to ten destinations, including exact finite
latitude/longitude pairs. Exact precision requires both coordinates. Legacy locality-only
entries remain supported. The existing consent, owner authorization and `If-Match`
version checks apply, including when saving an otherwise incomplete requirement draft.

Authenticated, CSRF-protected POST endpoints provide explicit lookups:

- `/api/v1/commute-locations/search` accepts `{ "query": "place name or Plus Code with locality" }`.
- `/api/v1/commute-locations/resolve` accepts `{ "mapsUrl": "Google Maps pin/share URL" }`
  or `{ "latitude": 19.1, "longitude": 72.9 }`.

Search returns a `results` array; resolution returns a single place, each with `placeId`,
`label`, numeric coordinates and `precision: "exact"`. The existing server-side
`GOOGLE_MAPS_API_KEY` is required for Places search; direct coordinates and full pin
links do not require it. Share URLs use the existing restricted Google redirect resolver.
Unavailable providers give explicit errors, not invented results. Requests are throttled
and use bodies rather than URL query strings for private search inputs. No Routes calls
or travel-time estimates are generated by this feature.

`GET /api/v1/map-stations` now includes explicit `connections` for the requested Line 9,
7/7A and 4/4A terminal links, using only available, unambiguous station candidates.
Connections are `schematic_unverified`, not verified infrastructure or service.

## Command reference

These are **PowerShell 7** examples for **Workspace-publish only**. Run individual
commands as needed, not this entire reference as a script. The existing database and
samples are already populated: routine startup does not require migration, ingestion,
reprocessing, database recreation or a new administrator.

- [Terminal setup](#terminal-setup)
- [Server and database](#server-and-database)
- [Google Maps configuration](#google-maps-configuration)
- [MahaRERA lookup](#maharera-lookup)
- [Google Places matching](#google-places-matching)
- [Manual Google share-link decisions](#manual-google-share-link-decisions)
- [Prepare metro station candidates](#prepare-metro-station-candidates)
- [Reviewed locations and station routes](#reviewed-locations-and-station-routes)
- [Ingestion and evaluation queues](#ingestion-and-evaluation-queues)
- [Tests and diagnostics](#tests-and-diagnostics)
- [Logs and progress](#logs-and-progress)
- [All custom management commands](#all-custom-management-commands)

### Terminal setup

Start in the workspace root. Run this block once per terminal; subsequent examples
assume you remain in the backend directory. The explicit interpreter avoids global
Python installations and PowerShell activation-policy issues.

```powershell
Set-Location ./reailEstate/reailEstate_common/backend
$Python = (Resolve-Path './.venv/Scripts/python.exe').Path
$Workspace = (Resolve-Path '../..').Path
$Output = Join-Path $Workspace 'output'
$Reports = Join-Path $Output 'reports'
$env:WORKSPACE_DATA_ROOT = $Workspace
$env:DATABASE_BACKEND = 'postgresql'
$env:PYTHONUTF8 = '1'
$env:PROJECT_METRO_CATALOGUE_FILE = Join-Path $PWD 'config/mmr-upcoming-metro.json'
```

For a **new checkout only**, create a missing environment with
`py -3.14 -m venv .venv` from the backend directory before the `Resolve-Path` line.
Install/update dependencies when needed, not on every start:

```powershell
& $Python -m pip install -r requirements.txt
& $Python -m playwright install chromium
```

Optional activation, when your local execution policy permits it:

```powershell
./.venv/Scripts/Activate.ps1
```

Set `DJANGO_SECRET_KEY` securely for non-development environments. Never copy the
sibling workspace's database, local configuration, credentials or generated reports.

### Perforated service architecture

The repository remains one codebase and one PostgreSQL schema while exposing
three independently runnable service boundaries:

- `admin`: Django administration, review, publishing, operational controls and
  the only deployment allowed to run schema migrations.
- `builder`: builder login, organization project management, uploads,
  extraction status and media/layout editing, with its role-specific React bundle.
- `buyer`: published project discovery plus buyer requirements, recommendations,
  shortlists, comparisons and travel estimates. Its deployment also serves the
  compiled React application.

The physical service entrypoints live in `adminService`, `builderService`, and
`buyerService`; shared models, migrations, processing code, and frontend source
live in `reailEstate_common`. The canonical lifecycle command is:

```powershell
.\reailEstate\manage_services.ps1 -Action start
```

See [`WORKFLOWS.md`](../../../WORKFLOWS.md#2-local-service-lifecycle) for status,
stop, restart, selective service, foreground-debugging, and Vite commands. The
default local ports are 8002, 8003 and 8004 respectively. All roles currently share
PostgreSQL and media storage. Keep writes behind the service that owns the capability.
Service route modules may import shared `platform_api` application code; reusable
document/image utilities belong in `realestate_common`.
`PROJECT_DATABASE_ALIAS` and a separate buyer-owned data store are future seams:
published project reads can move to a read replica, while requirements,
recommendations, shortlists and preferences move to the buyer database.

For container deployment, the root `compose.services.yml` builds one immutable artifact
and starts the three service processes. Set `POSTGRES_PASSWORD` and
`DJANGO_SECRET_KEY` privately before running it. An admin-settings migration job
owns schema changes; all three services wait for it to complete successfully.

The current database phase intentionally uses one PostgreSQL primary. The later
split should keep project publication in the admin/builder data plane, replicate
only published project read models to the buyer side, and move buyer-owned
requirements, recommendations, shortlists and preferences to a separate buyer
database. Use transactional outbox events and idempotent consumers for that
transition rather than allowing cross-database transactions.

The ClamAV network endpoint is configurable as `CLAMAV_ENDPOINT`
(`tcp://clamav:3310` by default) but `CLAMAV_ENABLED=false` is the default. No
connection or upload occurs while disabled. Enable it only after deploying and
validating an approved clamd endpoint; failed scans remain fail-closed.

### Server and database

Read-only checks and command discovery:

```powershell
& $Python manage.py check
& $Python manage.py help --commands
& $Python manage.py help process_outbox
& $Python manage.py showmigrations
& $Python manage.py migrate --plan
docker ps -a --filter 'name=^/realestate-workspace-publish-postgres$'
Test-NetConnection -ComputerName 127.0.0.1 -Port 15432
```

Start the **existing** database only if stopped, then start Django only if its task
is not already running. These commands do not recreate the database:

```powershell
docker start realestate-workspace-publish-postgres
& $Python manage.py runserver 127.0.0.1:8002
```

Open [Django admin](http://127.0.0.1:8002/admin/); API documentation is at
[API docs](http://127.0.0.1:8002/api/docs/). Stop a manually launched server with Ctrl+C.
For the VS Code task, use **Tasks: Terminate Task -> Run Django backend**, then
**Tasks: Run Task -> Run Django backend** to restart it. Do not kill an unrelated
port owner or use the sibling application's port 8000.

When a release actually introduces migrations, apply them deliberately after a
backup. Creating an administrator is for initial setup only and prompts privately:

```powershell
& $Python manage.py migrate
& $Python manage.py createsuperuser
```

**Provisioning/recovery only, not routine startup:** the setup script's defaults
belong to the sibling workspace. Use these explicit arguments only when provisioning
this target is required; never delete or replace the existing volume:

```powershell
./setup_local_postgres.ps1 -Container realestate-workspace-publish-postgres -Volume realestate-workspace-publish-postgres-data -Port 15432
```

Stop this database only after stopping this workspace's web server and workers:

```powershell
docker stop realestate-workspace-publish-postgres
```

### Portable demo snapshot

From the repository root, preflight an export to `D:\`:

```powershell
.\reailEstate\reailEstate_common\backend\export_demo_snapshot.ps1 -DestinationRoot D:\ -CheckOnly
```

Let active jobs finish, then stop this workspace's API, extraction dispatcher and
other writers. Keep PostgreSQL running. Export with:

```powershell
.\reailEstate\reailEstate_common\backend\export_demo_snapshot.ps1 -DestinationRoot D:\ -WritersStopped
```

The script does not stop or restart services itself. It refuses known workspace
Python processes or remaining database connections. Restart your original services
after the export finishes. It creates a new timestamped directory outside Git,
checks disk space and FAT32's per-file limit, and copies a PostgreSQL custom-format
dump plus `user\media`, `output\media`, and legacy
`reailEstate\reailEstate_common\backend\media`.
All copies are SHA-256 verified. `manifest.json` records the source Git commit,
working-tree status, database row counts and file hashes. `RESTORE.txt` explains
restoring to an **empty** database on the demo machine. It does not bundle code or
credentials. An interrupted/failed export retains `INCOMPLETE.txt`; restore only
a snapshot containing `COMPLETE.txt`.

The exporter sanitizes an **isolated temporary PostgreSQL database**, leaving the
source unchanged. Original accounts/password hashes, sessions, memberships,
permissions assigned to users, buyer requirements/recommendations, consent,
shortlists/shares, subscriptions, travel caches, idempotency records and operational
queues are excluded. One inactive account with an unusable password retains
required document uploader and crop-owner relationships; optional user references
are cleared. Project, layout, crop, ingestion/evaluation and review-task counts are
checked before and after sanitizing and after a full test restore. Temporary
databases and container dumps are removed on normal success or failure.

Audit metadata and recognized identity keys in JSON are cleared. This is **not
full anonymization**: project contact data, free text and original evidence files
may still contain personal information. Snapshots remain private and unencrypted.
Keep the drive private, do not commit snapshots, and use encrypted transport.
Existing incomplete exports made before this policy may still contain accounts.
Data-loss-prevention restrictions on copying documents still apply.

After restoring, create fresh demo accounts (`manage.py createsuperuser` and
Django admin) and assign builder organization memberships. Original logins will
not work. For a browse-only demo, leave the target extraction dispatcher stopped
to avoid processing/model costs. The export requires permission and local
container storage for two temporary databases plus their dumps.

### Google Maps configuration

Enable **Places API (New)**, **Routes API**, billing and quotas in Google Cloud.
The key must permit the server's requests to `places.googleapis.com` and
`routes.googleapis.com`. Enter it only in your own terminal using masked input,
not in chat, a command literal, source code, task configuration or a report:

```powershell
$env:GOOGLE_MAPS_API_KEY = Read-Host 'Google Maps API key' -MaskInput
if ($env:GOOGLE_MAPS_API_KEY) { 'Google key is set in this terminal; API access is not yet verified.' }
$env:PROJECT_METRO_CATALOGUE_FILE = Join-Path $PWD 'config/mmr-upcoming-metro.json'
```

A running Django process cannot inherit a newly entered key. Stop it and launch
`& $Python manage.py runserver 127.0.0.1:8002` **from this terminal**, or restart VS Code
from an environment containing the key before running its Django task. Merely
restarting a task does not copy environment values from another terminal.

Remove the terminal's copy when finished; already-running child processes retain
their environment until stopped:

```powershell
Remove-Item Env:GOOGLE_MAPS_API_KEY -ErrorAction SilentlyContinue
```

The bundled metro catalogue is a dated candidate snapshot, not independently
verified operating-service or entrance-location evidence. See the
[catalogue and review caveats](#connector-commands-and-port-notes).

### MahaRERA lookup

Public website access and local report writes; no project coordinates or accepted
registry facts are changed. Chromium must be installed. Replace the example
registration with the exact one you need:

```powershell
& $Python manage.py help fetch_maharera_project
$Registration = 'PR1330002502719'
& $Python manage.py fetch_maharera_project $Registration
& $Python manage.py fetch_maharera_project $Registration --timeout-seconds 120 --mapping (Join-Path $Reports 'maharera-project-map.json') --output (Join-Path $Reports "maharera/$Registration.json")
```

Complete CAPTCHA manually in the visible browser, then press Enter in the terminal.
For unattended environments, headless mode **stops** if CAPTCHA interaction is needed:

```powershell
& $Python manage.py fetch_maharera_project $Registration --headless --timeout-seconds 60
```

There is no `--resume` for this command. Rerunning refreshes the registration mapping
and its report. Timeout is 5-600 seconds; it never solves or bypasses CAPTCHA.

### Google Places matching

Without `--execute`, this command is preview-only and does not call Google or create
references. Execution incurs Places charges and creates **unreviewed references**,
not accepted coordinates. It never calls Routes.

```powershell
& $Python manage.py help refresh_google_project_places
$PlacesReport = Join-Path $Reports 'google-project-places.json'
& $Python manage.py refresh_google_project_places --report $PlacesReport
```

Start with five requests; then choose a bounded resume or a full resume. `--resume`
requires the report to exist. `--limit` counts new requests in the current invocation:

```powershell
& $Python manage.py refresh_google_project_places --execute --limit 5 --report $PlacesReport
& $Python manage.py refresh_google_project_places --execute --resume --limit 5 --report $PlacesReport
& $Python manage.py refresh_google_project_places --execute --resume --report $PlacesReport
```

Retry failed rows explicitly, optionally slowing requests (0.1-10 seconds):

```powershell
& $Python manage.py refresh_google_project_places --execute --resume --retry-failed --limit 5 --delay-seconds 1 --report $PlacesReport
```

Request a pause between projects by creating the report's `.stop` marker. Remove
only that marker before resuming; do not delete checkpoints or lock files:

```powershell
$PlacesStop = [IO.Path]::ChangeExtension($PlacesReport, '.stop')
New-Item -ItemType File -Path $PlacesStop -Force
Remove-Item -LiteralPath $PlacesStop
```

Run those two lines at different times: create to request a pause, remove after the
run has paused and you intend to resume. Omit `--resume` only for a deliberately new
report; reusing an existing report path without it replaces its checkpoint history.

### Manual Google share-link decisions

Prepare a new target-specific decisions array using the
[documented decision format](#connector-commands-and-port-notes). It must match this
workspace's source report indexes **and project IDs**. Do not reuse source-branch
decisions. Only HTTPS `maps.app.goo.gl` links are accepted.

```powershell
& $Python manage.py help import_google_project_place_decisions
$Decisions = Join-Path $Reports 'google-project-place-decisions.json'
$PlacesReport = Join-Path $Reports 'google-project-places.json'
$ImportReport = Join-Path $Reports 'google-project-place-import.json'
& $Python manage.py import_google_project_place_decisions $Decisions --source-report $PlacesReport --report $ImportReport
```

Execute one batch, then resume additional batches. Each attempts at most five
imports, incurs provider requests and creates unreviewed references only:

```powershell
& $Python manage.py import_google_project_place_decisions $Decisions --source-report $PlacesReport --report $ImportReport --execute --batch-size 5
& $Python manage.py import_google_project_place_decisions $Decisions --source-report $PlacesReport --report $ImportReport --execute --batch-size 5 --resume
```

Failed decisions are retried on resume; there is no `--retry-failed` option here.
Changing decisions or the source report requires a new import-report path. Source
URLs are hashed in the import report; the input decisions file still contains links
and should remain private. Output must not overwrite either input file.

### Prepare metro station candidates

The user-supplied [station CSV](config/mmr-metro-candidates.csv) contains 169
candidate rows. It is separate from the active metro catalogue. Prepare links and
a review report offline, from the backend directory:

```powershell
./prepare_metro_candidates.ps1 -Report (Join-Path $Reports 'mmr-metro-candidates-20260917.json')
```

Use a new report filename on each run. The command refuses to overwrite any existing
file, validates finite coordinate ranges, preserves all rows and flags duplicate
station/line coordinates and possible aliases. It needs no API key and does not call
Google, load Django, change the database or update the active catalogue.

Generated links use `https://www.google.com/maps/search/?api=1&query=LAT%2CLON`.
These open supplied coordinates; they do not prove a station's identity, entrance,
Google place ID or service status. They are not `maps.app.goo.gl` short links and must
not be passed to the project share-link decision importer. Station routes can use
coordinates directly, so shortened share links are unnecessary here.

The supplied rows have six conflicting-coordinate groups (five Line 2B stations and
Pandurang Wadi on Line 9), two identical duplicate groups and a possible Kashigaon
alias. Resolve these and obtain authority sources and dated service statuses before
promoting candidates into `PROJECT_METRO_CATALOGUE_FILE`. No operating status or
authority evidence is inferred from a Maps URL.

The active [metro catalogue](config/mmr-upcoming-metro.json) was empty when these
candidates were prepared and was deliberately left unchanged. An empty file is not
valid JSON: a new Django process configured to load it will fail during startup.
The candidate report is **not** a replacement catalogue. Do not start enrichment
until this configuration and the Google API key are ready.

### Reviewed locations and station routes

**Paid requests and accepted data changes.** Before executing, review the saved
references under **Project -> External sources**. Execution accepts project
coordinates, resolves review tasks, saves station routes and records audit events.
The reviewer must be an active staff user with project-change permission and cannot
approve their own submitted evidence. Use a real user from this target workspace.

```powershell
& $Python manage.py help enrich_google_project_routes
$Reviewer = Read-Host 'Target reviewer username'
$RoutesReport = Join-Path $Reports 'google-project-routes.json'
& $Python manage.py enrich_google_project_routes --reviewer $Reviewer --report $RoutesReport
```

First run a one-project canary, inspect it in **External sources**, and confirm at
least one walking/driving duration before continuing. The command selects eligible
projects in its own order; it has no `--project-id` filter. For a specific project,
use its admin actions instead.

```powershell
& $Python manage.py enrich_google_project_routes --execute --batch-size 1 --radius 10000 --reviewer $Reviewer --report $RoutesReport
& $Python manage.py enrich_google_project_routes --execute --resume --batch-size 5 --radius 10000 --reviewer $Reviewer --report $RoutesReport
```

Valid radii are 5000, 10000 and 20000 metres; batch size is 1-5. To retry failed or
no-duration results, generate an ID once and reuse it across resumed batches. Each
eligible result is retried at most once within that generation:

```powershell
$RetryGeneration = [Guid]::NewGuid().ToString('N')
& $Python manage.py enrich_google_project_routes --execute --resume --retry-failed --retry-generation $RetryGeneration --batch-size 5 --radius 10000 --delay-seconds 1 --reviewer $Reviewer --report $RoutesReport
```

To prefetch routes for all active projects with Google references or saved manual
pins, the runner defaults to `--prefetch-only`: it does not approve references or
change project coordinates. It resumes the default report in batches, stops after
at most 100 batches, and aborts if its first batch returns no route durations:

```powershell
./run_google_routes_all.ps1 -Reviewer $Reviewer -Radius 10000 -BatchSize 5
```

The batch loop and safety stop now live in Python. The script only configures the
environment and redirects output. A portable equivalent is:

```powershell
& $Python manage.py enrich_google_project_routes --execute --prefetch-only --drain --reviewer $Reviewer --report $RoutesReport
```

Add `--resume` for an existing report. `--max-batches` accepts 1-100; the report
lock is held throughout the drain. Single-batch invocation remains supported by
omitting `--drain`. The first-batch duration check examines only newly attempted
rows, not historical successful results.

The runner always uses this workspace's PostgreSQL backend and default
`output/reports/google-project-routes.json`; it has no custom-report option. Its log
is `output/google-project-routes.log`. No `.stop` protocol exists for route enrichment;
use single command batches when you need controlled stopping points. Interrupting
a command can leave a final database update ahead of its report checkpoint, so
inspect state before resuming. Google Maps credentials must be set in the terminal
running the script. Saved route origins appear in the admin Nearby transit panel,
labelled as not applied when the project has no accepted coordinates. Both manual
pins and Google references with a matching saved route origin display their saved
walking distances immediately, even without a configured API key. Reloading
the page does not call Google APIs. Restart a `--noreload` backend after code updates.
The empty bundled metro catalogue is not enabled automatically; nearby Google
stations and saved station candidates still use their existing discovery paths.

Only after all remaining references are reviewed, use `-ApproveLocations` to opt
into the previous coordinate-approval behavior. For a single project, use **Retry
and save station directions** on its admin page to fetch without approval.

### Ingestion and evaluation queues

These operations change records/output and may call configured paid models. They
are **not required** for Google/MahaRERA setup. Keep the current database and samples;
run only the intended scope. Inspect supported filters first:

```powershell
& $Python manage.py help import_sample_corpus
& $Python manage.py help refresh_sample_corpus
& $Python manage.py help process_outbox
```

Existing refresh launcher, with the current corpus report explicitly selected:

```powershell
./run_sample_refresh.ps1 -Workers 5 -ReportName 'sample-corpus-refresh-20260915.json' -Resume
./run_sample_refresh.ps1 -Workers 5 -ReportName 'sample-corpus-refresh-20260915.json' -RetryFailed
```

Run one, not both. These require the configured scanner and model environment.
`-TrustedSamples` is an explicit scanner bypass for approved local samples only;
do not add it routinely. Neither command resets the database, but both can resume
or repeat extraction. See [folder-based refresh](#folder-based-sample-refresh) before
starting a new import or using `--reprocess`.

For already-queued manual layouts, prefer **Run queued evaluations (exclude DLQ)**.
It consumes pending work, including scheduled retries, without reviving completed
or dead-letter jobs. **Review failures and run queued evaluations** retains the
historical-failure confirmation; it does not requeue DLQ entries either. Retry
those explicitly through Evaluation jobs. CLI alternatives below consume existing work; they do not create crop
selections. Take the project UUID from its admin URL:

```powershell
$ProjectId = Read-Host 'Target project UUID'
& $Python manage.py process_outbox --event-type extraction --project-id $ProjectId --once --limit 5
& $Python manage.py process_outbox --event-type extraction --project-id $ProjectId --drain --limit 5
```

Choose `--once` for one batch or `--drain` to include pending child jobs and retries.
Add `--owner-id` with a numeric user ID to narrow extraction work further. Do not
pass `--worker-id` manually; it identifies a managed worker-launch record. Omit
project/owner filters only when you intend workspace-wide processing:

```powershell
& $Python manage.py process_outbox --event-type recommendations --once --limit 5
& $Python manage.py process_outbox --event-type recommendations
```

The last command keeps polling until stopped. Do not run a second worker casually
while a managed evaluation worker is already processing the same scope.

### Tests and diagnostics

These checks use isolated SQLite, do not run ingestion or live providers, and restore
the terminal's database selection afterward. The browser test uses local Chromium;
install it as shown above to avoid skipping that check.

```powershell
$PreviousBackend = $env:DATABASE_BACKEND
try {
  $env:DATABASE_BACKEND = 'sqlite'
  & $Python manage.py check
  if ($LASTEXITCODE -ne 0) { throw 'Django checks failed' }
  & $Python -m pytest -c pytest.ini platform_api/tests/test_external_sources.py --tb=short
  if ($LASTEXITCODE -ne 0) { throw 'Connector tests failed' }
  & $Python -m pytest -c pytest.ini platform_api/tests/test_project_outputs.py platform_api/tests/test_layout_names.py -k 'project_admin_columns or dashboard_layout_explorer or dashboard_alias or active_toggle' --tb=short
  if ($LASTEXITCODE -ne 0) { throw 'Project UI tests failed' }
} finally {
  $env:DATABASE_BACKEND = $PreviousBackend
}
```

VS Code equivalents: **Tasks: Run Task -> Test external connectors**, **Test project
form and sidebar**, or **Test media inline controls**. The running **Run Django
backend** task remains separate from test database selection. To inspect all current
diagnostics or migration drift without applying changes:

```powershell
& $Python -m pip check
& $Python manage.py makemigrations --check --dry-run
```

### Logs and progress

Read only summary fields rather than printing whole reports or local credential files:

```powershell
Get-ChildItem -LiteralPath $Output -Filter '*.log'
Get-Content -LiteralPath (Join-Path $Output 'google-project-routes.log') -Tail 60 -Wait
```

The tail command waits for new log lines; Ctrl+C stops viewing, not the worker.
For a checkpoint that already exists:

```powershell
$Checkpoint = Get-Content -LiteralPath (Join-Path $Reports 'google-project-routes.json') -Raw | ConvertFrom-Json
$Checkpoint | Select-Object state, updatedAt, counts
$Checkpoint.projects | Group-Object status | Select-Object Name, Count
```

Use `google-project-places.json`, `google-project-place-import.json`, or
`sample-corpus-refresh-20260915.json` for their respective reports. The import report
uses `decisions`, not `projects`. Missing reports/logs generally mean that workflow
has not been run here; do not copy them from the sibling workspace.

### All custom management commands

Every application command is listed below. Use `& $Python manage.py help COMMAND`
for its full option list before a maintenance run. Django's built-ins are listed by
`& $Python manage.py help --commands`. Quoted `TARGET_*` values below are placeholders
to replace with actual target IDs, usernames or paths.

| Command | Starting point / options | Effect and caution |
| --- | --- | --- |
| `fetch_maharera_project` | `& $Python manage.py fetch_maharera_project $Registration` | Public browser lookup; writes mapping/report; manual CAPTCHA only. |
| `refresh_google_project_places` | `& $Python manage.py refresh_google_project_places` | Preview default; `--execute`, `--resume`, `--retry-failed`, `--limit`, `--delay-seconds`, `--report`. Paid matching creates unreviewed tasks. |
| `import_google_project_place_decisions` | See the input/report examples above. | Preview default; `--execute`, `--resume`, `--batch-size` 1-5. Requires project-bound decisions. |
| `enrich_google_project_routes` | `& $Python manage.py enrich_google_project_routes` | Preview default; execution accepts reviewed coordinates and persists routes. See reviewer/retry requirements above. |
| `import_sample_corpus` | `& $Python manage.py import_sample_corpus (Join-Path $Workspace 'user/Samples') --report 'sample-inventory.json'` | Inventory without `--import`; import needs `--username`. `--process`, `--reprocess`, `--resume`, `--project-key`, `--vision-profile-id`, `--retry-failed` control data/model work. |
| `refresh_sample_corpus` | `& $Python manage.py help refresh_sample_corpus` | Extends import options with `--workers`; prefer the launcher for established environment/scoping. |
| `process_outbox` | `& $Python manage.py process_outbox --event-type extraction --project-id $ProjectId --once --limit 5` | Consumes queued work. Without `--once` or `--drain`, polls continuously. |
| `classify_plan_assets_ai` | `& $Python manage.py help classify_plan_assets_ai` | Model-backed maintenance, not a preview. Requires `--report`, `--endpoint`, `--model`; supports `--project-id`, `--resume`, `--retry-attempts`, `--delay-seconds` and supported credential modes. |
| `compare_plan_modes` | `& $Python manage.py compare_plan_modes 'TARGET_DOCUMENT_UUID' --script-only --limit 3 --output (Join-Path $Reports 'plan-comparison-new')` | Writes a new private comparison directory. Omitting `--script-only` enables configured AI comparison; inspect help before paid use. |
| `isolate_floor_plan_units` | `& $Python manage.py isolate_floor_plan_units 'TARGET_FLOOR_PLAN_UUID' --mode script --report (Join-Path $Reports 'unit-isolation-new.json')` | Creates unit extraction outputs; not a preview. Modes: `script`, `ai`, `both`; optional `--expected-count`, `--vision-profile-id`. `--supersede-source` changes source usability. |
| `separate_all_floor_plan_units` | `& $Python manage.py help separate_all_floor_plan_units` | Bulk layout changes; requires a new `--report`. No preview or project filter is exposed. Do not use for routine connector setup. |
| `rebuild_script_outputs` | `& $Python manage.py rebuild_script_outputs --backup-dir (Join-Path $Reports 'script-rebuild-new')` | Inventory default. `--execute` rebuilds; `--reuse-recorded-scans` explicitly reuses hash-bound clean scans in development. Inspect backup requirements first. |
| `cleanup_unreferenced_images` | `& $Python manage.py cleanup_unreferenced_images` | Preview first. Adding `--execute` deletes listed unreferenced files; inspect the list and backup before considering it. |
| `sync_feature_flags` | `& $Python manage.py sync_feature_flags` | Preview defaults; `--apply` creates missing defaults without overwriting existing rows. |
| `verify_database_transfer` | `& $Python manage.py verify_database_transfer 'TARGET_PRIVATE_FIXTURE.json' --report (Join-Path $Reports 'database-verification.json')` | Compares an authorized fixture against the selected database and writes a report. Not an import/restore. Never use the sibling database's fixture or print fixture contents. |

The three PowerShell launchers are [setup_local_postgres.ps1](setup_local_postgres.ps1),
[run_sample_refresh.ps1](run_sample_refresh.ps1), and
[run_google_routes_all.ps1](run_google_routes_all.ps1); their safe target examples are
above. For crop editing, aliases, active toggles, queue monitoring and per-project
route retry/approval, use the admin UI rather than unsupported shell model updates.

Open `/api/docs/` for the generated API description. Session-authenticated mutations require Django CSRF protection.

## Integration boundaries

MahaRERA, routing, transaction, malware scanning, and extraction providers are intentionally replaceable adapters in `platform_api/integrations.py`. Their default implementations fail closed. Configure approved licensed providers and a malware scanner before enabling document processing or partner-backed results. Uploads remain quarantined while these providers are unavailable.

Local development enables a clearly labelled straight-line commute approximation. It stores bounded, expiring travel estimates and includes its speed/route-factor assumptions and a no-traffic disclaimer in every response. Set `ROUTING_PROVIDER=unavailable` to disable it. Production always defaults to unavailable until an approved routing adapter is configured.

Recommendation submission uses the transactional outbox and returns immediately with operation and recommendation request URLs. Pending messages use bounded exponential retries and move to dead-letter state after five unsuccessful attempts.

## Builder knowledge ingestion

### External project sources

Open **Project details / Media dashboard -> External sources**. This is an explicit,
project-scoped lookup flow, independent of document ingestion and its extraction queue:

1. Search Google Maps by project name plus locality/address; select the correct result.
2. Inspect latitude/longitude, then compare walking/driving route distances in metres
  and durations in minutes to railway and metro candidates within 5, 10 or 20 km.
3. Look up the exact MahaRERA number through the approved enrichment adapter, or open
  the official project search and save a dated project URL and evidence excerpt.
4. **Commit source references** submits an unreviewed `external_sources` review task.
  Discarding or merely searching does not change accepted project facts. Commits are
  version-checked, audited and idempotent for the draft. They do not create verified
  registry snapshots, update project coordinates, publish, or run extraction jobs.

Configuration (server environment, never enter keys into the web form):

- `GOOGLE_MAPS_API_KEY`: a restricted server-side Google Maps Platform key with billing,
  **Places API (New)** and **Routes API** enabled. Set quotas before enabling lookups.
  Restart the Django task after changing its environment. No key is committed or sent
  to the browser. Search uses at most five matches; each station comparison uses two
  Nearby Search requests and up to 30 Compute Routes requests, with four concurrent
  routes and 10-second per-request timeouts. Refresh also makes a Place Details request.
- `PROJECT_ENRICHMENT_BACKEND`: existing importable approved adapter with
  `registration(number)` returning `{sourceUrl, retrievedAt, data}` and an exact
  `data.registrationNumber`. No public MahaRERA API is assumed. Website entry remains
  manual unless such an adapter is configured; login/CAPTCHA is never bypassed.
- `PROJECT_METRO_CATALOGUE_FILE`: absolute path to an operator-curated UTF-8 JSON array.
  Each station requires `name`, `location` (`latitude`, `longitude` as numbers),
  `serviceStatus` (`operational`, `under_construction`, `planned`), an official
  `sourceUrl`, and ISO `asOf` date. Optional fields: `line`, `googlePlaceId` for exact
  reconciliation with Google candidates. Permitted authority hosts are defined by
  `PROJECT_METRO_SOURCE_HOSTS` in settings. Do not populate this from invented
  opening dates or brochure claims. Refresh dated status evidence regularly.

Google previews use an owner/project-bound server cache with a 15-minute idle TTL;
restarts can expire drafts. Only the selected Google place ID is persisted, not its
name, address, coordinates or route content, when submitting a reference. An independent
staff reviewer with project-change permission can explicitly **Apply Google location**
to update coordinates and resolve the task. **Retry and save station directions**
persists route results without accepting coordinates. Both actions are audited and
reject concurrent changes. Configure a private cache and review Google Maps licensing, attribution,
privacy/terms and retention requirements before production use. The screen displays
Google Maps and returned provider attributions, with links to Google Maps directions.

"Nearest" is the shortest returned route among bounded candidates, not a guarantee
of the nearest station across the network. Google station listings are existing
candidates, not verified operational status. An authority record with the same
Google place ID takes precedence. Upcoming candidates use current road/walking
routes to proposed coordinates, never hypothetical metro travel times or promised
access. Missing catalogue, no route, API failure and missing configuration remain
explicitly unavailable. This flow does not alter legacy recommendation commute
approximations or automatically rerun ingestion reports.

#### Connector commands and port notes

This workspace runs at [http://127.0.0.1:8002/](http://127.0.0.1:8002/) using its dedicated PostgreSQL
configuration. Do not use the sibling workspace's port 8000, database, secrets or
reports. No migrations or corpus refresh are required for these connectors.

The four commands below were ported from the experimental connector source, preserving
the project form, layout tools and existing records. Source project decisions and
generated reports were deliberately not imported. The share importer now requires
target `projectId` values and validates every redirect before following it; viewport
coordinates are rejected. Provider calls run outside database transactions.

Run from `reailEstate/reailEstate_common/backend` with this workspace's `.venv` active:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
$env:DATABASE_BACKEND = 'postgresql'
$env:PROJECT_METRO_CATALOGUE_FILE = (Resolve-Path './config/mmr-upcoming-metro.json').Path
python manage.py check
python manage.py help fetch_maharera_project
python manage.py help refresh_google_project_places
python manage.py help import_google_project_place_decisions
python manage.py help enrich_google_project_routes
```

Supply `GOOGLE_MAPS_API_KEY` through your secure environment, never a source file,
task command, report or chat. Enable Places API (New), Routes API, billing, quota and
key restrictions for `places.googleapis.com` and `routes.googleapis.com`. A terminal
variable does not update an already-running Django task; restart it from an environment
that has the key. Both Django tasks set the catalogue path but contain no Google key.

The bundled [MMR candidate catalogue](config/mmr-upcoming-metro.json) is the ported
29-station snapshot dated 2026-09-15, covering lines 4, 4A, 2B, 6 and 9. Its coordinates
and planned statuses were not independently revalidated during the port. Review the
linked authority evidence before operational use; it is not proof of opening dates,
station entrances, operating service or a globally nearest station.

Commands default to reports in workspace `output/reports`, regardless of current
directory. Google commands are preview-only without `--execute`. These examples make
paid requests and must be run deliberately, not as setup validation:

```powershell
python manage.py fetch_maharera_project PR1330002502719
python manage.py refresh_google_project_places --execute --limit 5
python manage.py refresh_google_project_places --execute --resume
```

MahaRERA searches the exact registration, saves its discovered internal-ID mapping,
waits for rendered public content and exports visible text/tables/links. CAPTCHA must
be completed manually in Chromium; `--headless` stops if interaction is required.
It exports evidence only: no automatic verified snapshot or project update.

Places matching requires a unique project-name/locality match, never calls Routes,
and creates an open, unreviewed `external_sources` task. `--limit` caps new requests
in that invocation, including resumed work. A `.stop` file beside the report pauses
work; remove it before resuming. Checkpoints are atomic and report-locked.

For manual share decisions, produce a **new target-workspace** JSON array matching
the current Places report's `no_match` rows with 2-5 candidates, in report order:

```json
[
  {"index": 1, "projectId": "<target-project-uuid>", "action": "select", "url": "https://maps.app.goo.gl/<share-id>"},
  {"index": 2, "projectId": "<target-project-uuid>", "action": "skip"}
]
```

```powershell
python manage.py import_google_project_place_decisions decisions.json --source-report ../../output/reports/google-project-places.json --report ../../output/reports/google-project-place-import.json --execute --batch-size 5
```

Imports accept only HTTPS `maps.app.goo.gl` links resolving through Google Maps,
require actual place coordinates and a unique closest Places candidate within 500 m,
hash source URLs in reports, and never accept project coordinates. Add `--resume`
for subsequent batches. Changing decisions or source input requires a new report.

**Route enrichment accepts coordinates.** Review the target references first. The
reviewer must be active staff with project-change permission and cannot approve their
own submitted evidence. An omitted reviewer is allowed only with one active superuser.
Use an actual target username, not a username from the source guide:

```powershell
python manage.py enrich_google_project_routes --execute --batch-size 1 --radius 10000 --reviewer 'TARGET_REVIEWER'
```

Verify at least one route has a duration before proceeding. Each invocation attempts
at most five provider projects, persists `stationRoutes` in the review task, records
acceptance/audit, checkpoints every project and accounts for missing references as
`skipped_unavailable`. `--resume` skips completed work. `--retry-failed` requires a
unique `--retry-generation` and retries errors/no-duration results once per generation.

After the canary succeeds and the remaining references are reviewed:

```powershell
./run_google_routes_all.ps1 -Reviewer 'TARGET_REVIEWER' -Radius 10000 -BatchSize 5
```

The runner resumes with one retry generation, enforces a 100-batch ceiling, and aborts
after the first batch if it returns no route durations. Its log is directly under
workspace `output/google-project-routes.log`. Saved route display prefers the latest
Google review task, then a same-project/same-reference report in `output/reports`
(legacy backend `reports` is read only when the target report is absent).

Offline validation: VS Code tasks **Test external connectors** and
**Test project form and sidebar** use isolated SQLite. Live Google billing/key access,
route availability and the current MahaRERA website/CAPTCHA require separate validation.

### PostgreSQL and concurrent sample ingestion

This workspace uses one PostgreSQL 17 primary at `127.0.0.1:15432`.
There is no read replica: separate tables alone do not fix SQLite's database-wide
writer lock. Its loopback-only Docker container is `realestate-workspace-publish-postgres`
and persistent volume is `realestate-workspace-publish-postgres-data`. Its randomly
generated password is stored in the git-ignored, Windows-ACL-restricted
`.local/postgres.json`; never commit or display that file. Do not delete the volume.

The database is already provisioned; connector setup must not reset or reingest it.
The setup script retains legacy defaults for the sibling workspace. Never invoke it
here without explicit `-Container realestate-workspace-publish-postgres`,
`-Volume realestate-workspace-publish-postgres-data`, and `-Port 15432` arguments.

Settings load that local connection automatically when the file exists. The startup
task explicitly selects PostgreSQL. `DATABASE_BACKEND=sqlite` is only for backup or
rollback operations; never run workers against different databases simultaneously.
For externally hosted PostgreSQL, configure `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`,
`PGPASSWORD`, and the appropriate `PGSSLMODE` instead. Use a direct connection, not
transaction-mode connection pooling, because job ownership uses session advisory locks.

`refresh_sample_corpus` accepts the original import/process/reprocess/resume options
plus `--workers` (bounded by `INGESTION_MAX_WORKERS`, default 8), repeatable
`--resume-status`, and `--retry-failed`. The Windows launcher is
[run_sample_refresh.ps1](run_sample_refresh.ps1); `-Resume` resumes the existing report,
`-RetryFailed` resumes only failed/incomplete rows, and `-TrustedSamples` explicitly
enables the previously approved local scanner bypass.
Without that switch, configure a real scanner. The sample launcher raises only its
process's upload cap to 512 MiB to include the large plan PDFs.

The coordinator stages sources, then runs one document per spawned process. PyMuPDF
objects are never shared between threads/processes. A global corpus file lock prevents
overlapping local coordinators, and PostgreSQL session advisory locks prevent duplicate
document processing across workers. These locks hold no transaction and release when
the owning connection/process dies. An interrupted `extracting` document can therefore
be retried after the next worker acquires ownership. Model calls use a shared budget
of at most `VISION_MAX_CONCURRENT_REQUESTS` (1–16, default 8) across web and worker
processes connected to the same primary.

Source scanning, PDF rendering, conversion and model calls run outside database
transactions. Fact reconciliation and final result publication use short transactions.
Unfinished runs remain `extracting`, excluded from completed knowledge retrieval and
output downloads. Earlier successful outputs remain available until a new run finishes.
Review candidates can be visible to administrators during processing; nothing is
automatically published. Explicit layout-page decisions override heuristic selection;
manual crop Save creates private drafts (20 per user) without changing layouts.
Only Add selected to project attaches drafts and queues checksum-bound evaluation;
replacement of existing layouts is a separate confirmed, audited user action.
Duplicate projects are joined through reviewed merge records, while admin deletion
retires projects without destroying evidence or history.

Logs contain UTC timestamps, document IDs, stages, rendered/selected pages, model
request attempts, HTTP status, duration, and token totals—not credentials, prompts or
image payloads. Per-document `<report-stem>-progress/<document-id>.json` files receive
a heartbeat every 15 seconds; the coordinator refreshes aggregate states about every
5 seconds. `timingByMediaType` summarizes completed work in the current pass.
`etaSeconds` stays null until every remaining format has at least three timing samples;
it is a rough throughput estimate, not a completion guarantee.

Press Ctrl+C in the coordinator terminal to **drain active documents** without assigning
new ones, or create a `<report-stem>.stop` file beside the report. Wait for state
`paused`; remove the marker before resuming. Do not forcibly kill the terminal for a
routine pause. SDK requests have bounded retries; exhausted/partial failures stay in
the report for an explicit resume. Byte-identical aliases do not consume extra workers.
Combined project reports are written only after all of that project's sources finish
or fail. A failed source is retained as a gap, not counted as successfully extracted.

The pre-migration SQLite backup, export and field/relationship verification report are
under workspace `output/reports/postgres-migration-20260911`. Keep them private: the
fixture contains original account password hashes and evidence data. Rollback requires
stopping all writers and explicitly selecting SQLite; new PostgreSQL work is not copied
back automatically. `verify_database_transfer` compares a dumpdata fixture against the
selected target without exposing the field values in its report.

### Folder-based sample refresh

`import_sample_corpus` accepts `Samples/{domain}/{project-name}/...` and the older
`Samples/{domain}/{project-file}` layout. All supported sources in a project folder,
including nested tower folders, are assigned to the same project. `.ignore` (at any
depth) and videos are excluded and listed explicitly. PDFs, JPEG/PNG, text, Markdown,
CSV, JSON and DOCX use native ingestion; other accepted Office formats require the
converter described below. Original sample files are never moved or modified.

Standalone JPEG/PNG sources in a project folder are treated as candidate pre-cut floor
plans and processed before PDFs and supporting evidence. Once classified as individual
layouts, these source images are returned and displayed ahead of generated unit crops;
the generated crops and full document/page evidence remain available for review.

Use `--import --process --reprocess --username <existing-user>` for a refresh. Existing
evidence is rescanned/re-extracted without deleting previous runs. Byte-identical files
within a project share one extraction but every source path is reported; identical
content in different project directories is ingested separately. Folder membership,
not the first RERA number found in a brochure, determines grouping. Multiple RERA IDs,
tower claims and conflicting facts remain available for review.

`--report` defaults to `sample-corpus.json` inside workspace `output/reports`. Relative
report names resolve there, and absolute paths outside it are rejected. The report is
checkpointed after each source. Adjacent `<report-stem>-projects` reports combine all
source evidence, retain supplementary text chunks, union amenities, and preserve
conflicting values rather than adding possibly duplicated totals. Per-run downloadable
artifacts remain private under `output/media/projects`.

Use `--resume` with the same report, source and vision/scanner configuration after an
interruption; completed sources are skipped and incomplete ones retried. Run only one
synchronous importer/worker against SQLite. The command refuses pending document
outbox work. `--test-scanner` is an explicit local/development-only **malware scanning
bypass**, not a security scan; use it only for trusted samples with approval.

Create an ignored local vision profile from [vision-profiles.example.json](vision-profiles.example.json).
Local profiles are not bundled or loaded automatically and must contain no credentials. Load the JSON into
`FLOOR_PLAN_VISION_PROFILES`, set `FLOOR_PLAN_EXTRACTION_MODE=ai` and
`FLOOR_PLAN_DEFAULT_VISION_PROFILE=azure-samples` in the importing process. Authentication
uses `DefaultAzureCredential`; do not put tokens or API keys in the profile or reports.
An unavailable Azure provider is reported as incomplete, never silently replaced by
local rules. Existing page-selection budgets still apply; ingestion completion does
not assert exhaustive visual coverage or human verification of every page.

### Expanded project reports and local folders

The batch pipeline now writes per-document project reports and layout-metrics JSON/CSV, including parcel fields, RERA evidence, amenities, prices/offers, categorized image URIs, developer and station enrichment status. Full field definitions, format matrix, formulas, and limitations are in [the ingestion/output specification](../../docs/11-document-ingestion-and-project-outputs.md).

New uploads are physically stored in `user/media/todo` under `WORKSPACE_DATA_ROOT` (default workspace root). Successfully processed originals move to `user/media/completed`; parser failures and incomplete visual analysis remain in todo. Human review can remain outstanding after a processing-success move. Existing legacy media stays readable. Directory placement alone does not enqueue files: use the existing upload/complete API.

Images are saved under `output/media/images/{project-id}/{general|floor-plans|layouts|amenities|unclassified}/{workflow-id}`. Report fields contain authenticated URIs rather than inline image data. Project/metric files are under `output/media/projects/{project-id}/{run-id}`. Do not serve these directories as public static files.

Read `GET /api/v1/developer-projects/{projectId}/extraction-output` or open the Project's **Structured extraction outputs** link in admin. Each Ingestion Run exposes download links for `project`, `layout-metrics`, and `layout-metrics-csv`. Reprocess existing documents to generate the new reports; no sample data is changed automatically.

DOC/RTF/ODT/PPT/PPTX/XLS/XLSX uploads require an explicitly enabled LibreOffice-compatible conversion worker: `OFFICE_CONVERTER_ENABLED=true`, `OFFICE_CONVERTER_EXECUTABLE`, and optional `OFFICE_CONVERTER_TIMEOUT_SECONDS` (90). Sandbox the converter without network/privileged access. Otherwise they remain failed in todo with `OFFICE_CONVERTER_REQUIRED`. DOCX has native text/table/image handling; enabling conversion adds paginated extraction. Export Google Docs as DOCX/PDF rather than uploading a `.docs` shortcut.

Set `PROJECT_ENRICHMENT_BACKEND` only to a trusted installed adapter implementing the documented registry/station/developer/sentiment contracts. No built-in live MahaRERA/maps/social integration or sentiment data is claimed. Missing external data is explicitly unavailable and does not prevent a document from completing. Model claims are not external verification.

Builders can upload incomplete or overlapping PDF, PNG, JPEG, plain-text, Markdown, CSV, JSON, and DOCX evidence. The worker performs this bounded flow:

1. Verify object size, checksum, real media signature, and project authorization.
2. Scan the immutable source with ClamAV before parsing. Set `DOCUMENT_SCANNER=clamav`; processing fails closed when the scanner is unavailable.
3. Extract inert page text without following links or executing embedded instructions.
4. Split text into provenance-preserving chunks and create local sparse retrieval vectors.
5. Retrieve field-specific evidence and create typed fact candidates for project identity, location, MahaRERA ID, configurations, carpet area, dimensions, amenities, pricing, and possession.
6. Preserve conflicts and low-confidence values as reviewer tasks; nothing is automatically published.
7. Select up to eight likely floor/layout-plan pages using text and drawing/image signals. Store selection scores/reasons in workflow steps. Render entire selected PDF pages to private lossless PNG screenshots (240 DPI by default, configurable through `FLOOR_PLAN_RENDER_DPI`, bounded to 144–360 DPI and 40 million pixels). Record dimensions, effective DPI, source bounds, and SHA-256. Standalone images are whitespace-cropped instead.
8. Use the selected mode: script mode identifies page-level candidates using local rules and embedded text; AI mode sends saved screenshots—not the unrestricted PDF—to the selected vision model. Validate output, persist provenance, and require human review. Text-only inputs retain local text extraction. AI mode never silently falls back to scripts when its selected provider fails.
9. Classify additional design/amenities images, extract structured project claims from full page text (and saved images in AI mode), write project and metric outputs, and relocate successful sources. AI mode can now make additional general-page calls (maximum 20 images), beyond the floor-plan calls; budget accordingly.

Floor-plan workflows enter `needs_provider` when their selected vision provider is unavailable. The saved screenshots remain available to reviewers with placeholder plans; reprocess the document once the provider is ready. No alternative cloud/model is contacted automatically. Builders and reviewers can inspect ingestion runs, search evidence, inspect floor plans, and view assets through authenticated same-origin endpoints. Structured output includes configuration, carpet area, room dimensions, orientation, features, confidence, and source/model provenance. Page selection is heuristic: scanned brochures can contain false positives and pages beyond the eight-page budget may require a separate document.

### Choose script mode or AI mode

### AI cleanup of false-positive plan candidates

AI plan extraction now explicitly classifies each image as `floor_plan`, `flat_layout`, `mixed_plan`, `facade`, `elevation`, `notation_only`, `non_plan`, or `uncertain`. Only images with visible plan geometry and explicit floor/flat scope can generate plan records. Exterior renderings, notation-only pages and uncertain images produce no new plans. A legitimate plan containing notes/tables is still eligible. Provider failure retains the evidence image but no longer creates placeholder floor-plan rows.

Accepted AI-generated plans have `review_state=gen_with_ai_needs_review`. Their stored names include the AI workflow ID so they cannot overwrite existing script or earlier AI rows; `raw_extraction.sourcePlanName` preserves the model's original label. Human approval is still required. No database schema migration is needed for this string state.

The `classify_plan_assets_ai` management command analyzes existing saved script candidate PNGs, not original PDFs. Required options: `--report` (new local JSON progress path), `--endpoint`, `--model`; `--prompt-api-key` reads credentials using hidden input, or use `FLOOR_PLAN_VISION_API_KEY`. `--project-id` restricts the scope; otherwise all script candidate images are selected. Review the per-image cost before a full run. No script plans, source PDFs, project reports, or current global mode are changed. It creates dedicated image-only ingestion/workflow records and new assets only for accepted plans. This is a classification pass, not a full project/metrics rerun or a scan for missed PDF pages.

Rejected classifications and failed images remain in the progress report; successful results also have database checkpoints. Use `--resume` with the same report and configuration to skip already-completed images. Authentication/provider outages pause the batch instead of retrying every image. The command uses a per-report lock to prevent overlapping resumes. AI input is limited to stored PNGs with matching size/hash from recorded-clean documents; no fresh malware scan is claimed.

Entra-authenticated AI batches reuse one credential and cached access token for the batch lifetime, refresh before expiry, and checkpoint every image. An interrupted run can therefore resume without repeating accepted or rejected results.

In admin, filter Floor plans by **Review state → gen_with_ai_needs_review**, or Floor plan assets by **Generation mode → AI generated — needs review**. Script false positives are retained for comparison and remain visible in the unfiltered list.

Set `FLOOR_PLAN_EXTRACTION_MODE` in the environment of the API and worker processes, then restart both:

| Value | Behavior |
| --- | --- |
| `script` (default) | Uses the built-in `rules-only` profile. No LLM, model endpoint, API key, or Azure authentication is needed. |
| `ai` | Uses `FLOOR_PLAN_DEFAULT_VISION_PROFILE` or an explicitly requested configured vision profile. Existing Ollama/Foundry/cloud adapters are retained. |

Script mode selects floor-plan/flat-layout candidate pages using headings, room labels, and drawing/image signals, saves high-quality PNG pages, and classifies them as `floor`, `flat`, or `unknown`. It records local rule provenance and any unambiguous embedded configuration/carpet-area labels. It does not perform OCR, identify rooms from pixels, infer measurements, or segment multiple flats from one page. Scanned or mixed-layout pages remain uncertain review candidates. A completed script run means identification completed, not that all dimensions were extracted. AI-only `needs_provider` behavior does not apply to script identification; malware scanning is still mandatory.

With script mode selected, omit `visionProfileId` or send `rules-only` on completion/reprocessing. New requests for AI profiles are rejected in script mode. In AI mode, select an AI profile normally; an explicit `rules-only` request is also supported. Profile discovery lists the options for the current mode.

The mode is pinned through each queue item's selected provider (`rules` versus an AI provider). Switching the setting affects new requests and direct runs, **not existing queued requests**: an already-queued AI job can still call its AI provider after a switch to script. Existing saved plans are unchanged until reprocessed. Historical messages without a pinned selection use the current mode. Mode/model provenance is included in workflow results and plan raw extraction.

### Local Ollama and cloud model choices (AI mode)

Configure an administrator-owned model catalog using the JSON object in `FLOOR_PLAN_VISION_PROFILES`; set `FLOOR_PLAN_DEFAULT_VISION_PROFILE` to a catalog key. Each profile is one provider/model combination: add more profiles to offer multiple models from the same provider. [vision-profiles.example.json](vision-profiles.example.json) supplies an example. This file is not loaded automatically: set the environment variable to its JSON content after adapting the configuration, on both the API and worker processes.

| Provider | Endpoint | Authentication |
| --- | --- | --- |
| `ollama` | `http://127.0.0.1:11434/v1` by default | None for local Ollama; loopback only |
| `local_openai` | A configured loopback OpenAI-compatible `/v1` endpoint | Optional server-side key reference |
| `azure_foundry` | Your deployment's HTTPS OpenAI v1 endpoint, e.g. `https://<resource>.openai.azure.com/openai/v1/` | `DefaultAzureCredential` by default; optional `api_key_env` |
| `cloud_openai` | An approved cloud instance's HTTPS `/v1/` endpoint | Required `api_key_env` referring to an environment variable containing its key |

For Ollama, install/start Ollama on the **worker's host**, pull a vision-capable model, and set the profile's `model` to its installed tag. The example uses `qwen2.5vl:7b`; availability and hardware requirements depend on the installed Ollama version/model. The endpoint speaks OpenAI-compatible chat completions and receives base64 PNG images. Text-only models will not work. Loopback means the backend/worker machine, not the developer's browser machine; a cloud worker cannot reach a laptop's Ollama through `localhost`.

For Foundry, deploy a model supporting image input and structured outputs, supply the deployment name as `model`, and enable the profile after setting the endpoint and identity permissions. Use the OpenAI v1 inference endpoint, **not** a Foundry project endpoint. For a custom cloud instance (including a self-hosted vision server), put it behind authenticated HTTPS on port 443 and configure `cloud_openai`; no VM or cloud instance is provisioned by this application. Select `response_format: "json_object"` for compatible servers without strict `json_schema` support. Both modes undergo local JSON-schema validation. Missing/invalid responses fail closed.

Keep secret values out of catalog JSON: `api_key_env` is only an environment variable **name**. The discovery API exposes labels, provider/model names, and local/cloud hosting, never endpoints or credentials. The server owns the catalog; developers select a profile ID, not an arbitrary endpoint or model name. Network redirects are disabled. Restrict cloud egress at the infrastructure layer as well.

The legacy `FLOOR_PLAN_VISION_PROVIDER`, `FLOOR_PLAN_VISION_BASE_URL`, `FLOOR_PLAN_VISION_MODEL`, and `FLOOR_PLAN_VISION_API_KEY` settings remain supported as a single implicit `default` profile when the catalog is empty. An unconfigured server can still accept documents, but vision workflows require configuration and reprocessing before extraction succeeds.

### Developer API and todo batches

All routes below are under `/api/v1/developer-projects/{projectId}` and require project editor/owner access (reviewers may read discovery and queue state).

1. `GET /vision-profiles` lists configured provider/model choices and `defaultProfileId`.
2. Upload through the existing upload-intent and content endpoints. No scan or inference runs during upload.
3. `POST /documents/{documentId}:complete` accepts `{ "visionProfileId": "ollama-local" }` and requires `Idempotency-Key`. It returns `202`, `operationId`, `statusUrl`, `queueStatus: "pending"`, and `visionSelection`. Omitting the selection uses the configured default; an invalid/disabled selection is rejected before enqueueing.
4. `GET /ingestion-queue` returns pending/delayed/dead-letter counts and up to 100 pending/dead-letter items with each selection and retry information.
5. `POST /ingestion-queue` with `{ "limit": 1 }` runs a bounded project-only batch. Default limit is 10, maximum 20. **This manual endpoint is synchronous** and may take minutes for PDFs; use small batches or the timer/CLI for long jobs. It does not change the profiles already saved on the requests.
6. `POST /documents/{documentId}:reprocess` with `{ "visionProfileId": "foundry-cloud", "reasonCode": "TRY_CLOUD_VISION" }` and a new `Idempotency-Key` queues another extraction and returns a new operation. It rejects an already-active request. The admin reprocess route also requires an idempotency key now. Reprocessing updates logical plans; it is not an immutable comparison of model outputs, and requires renewed review.

Queue payloads pin the profile ID, provider, model, and nonsecret configuration hash. Changing the default does not change already-queued choices. Editing/removing/disabling a selected profile blocks the old request instead of silently rerouting it; keep old profiles unchanged and add a new ID for a different configuration. Secret rotation and label changes are allowed. Pre-existing messages without a selection retain their legacy worker-default behavior.

### Ad hoc flat separation

The builder project workspace provides private **Project images**, **Amenities**,
**Floor plans**, and **Layouts** tabs with previews and existing layout price/name
editing. Active organization owners/editors can crop a verified source page into
their private draft cart, explicitly attach drafts and queue evaluation, add source
pages as floor plans or amenities, and re-evaluate individual layouts. These actions
use the same crop, provenance, extraction, retry, and review pipelines as admin;
opening media or saving crop drafts does not invoke AI.

Builder media routes live under `/api/v1/builder/projects/{id}/media`. Every
metadata/image/action route checks the current active organization membership,
including nested document, asset, plan, and draft IDs. Drafts are private to their
creator. Archived projects are read-only; being staff alone does not grant builder
access. Mutations require session CSRF protection and a UUID `requestId`; repeating
an identical request returns its original result, while changing its payload with
the same ID returns a conflict. Attachment, source-page addition, and re-evaluation
require explicit confirmation and may incur configured model costs. Queue creation,
publication invalidation, and the replay record commit together. Builders cannot
approve or publish; changed published projects require independent review again.

Staff administrators can open a clean full-floor record under **Admin → Floor plans** and choose **Separate flats**. The operation supports `script`, `ai`, and `both`; combined mode runs the methods independently so an inconclusive script result does not block AI. AI uses only configured server-side vision profiles. Generated crops remain review proposals, and the source is preserved unless explicit supersession is requested and all selected methods succeed.

Each project also has a **Media dashboard** link in Django admin with project-scoped Amenities, Floor plans, and Layouts tabs. Layout cards expose extracted room sizes, calculated rectangular areas, counts, confidence, features, and provenance. **Report for update** creates a deduplicated high-priority review task; it does not silently mutate or rerun extraction.

Automatic AI/script segmentation creates cropped layouts and preserves the source
floor plan. On a layout or floor-plan card, **Reset crop** opens the full original
page in the existing crop editor, with no rectangle preselected. Draw a replacement
boundary, choose **Save to cart**, then explicitly commit the update from the cart.
Opening or cancelling Reset crop leaves the saved crop and evaluation queue unchanged.
Committing retains the plan ID and alias, preserves the previous image/history, and
queues evaluation for the new crop. Missing historical page images are recovered
from the verified source document; unavailable or invalid sources fail explicitly.

The project admin list has a **Projects per page** selector for 10, 20, 50, or all records. The selected size is retained while moving between list pages, together with active admin filters.

Project records and media dashboards include previous/next project controls. The media dashboard also includes a back-to-project action and a simple carousel of the project's saved images. Local administrators can generate missing script layouts across all eligible projects with `python manage.py separate_all_floor_plan_units --report <new-report.json>`; existing script segmentation is not replaced and ambiguous sources are reported for follow-up.

The equivalent staff-only API is `POST /api/v1/admin/floor-plans/{floorPlanId}:segment-units` with an `Idempotency-Key`. `GET /api/v1/admin/floor-plan-segmentation` discovers safe mode/profile metadata. For local development, `isolate_floor_plan_units` exposes the same service and is rejected unless `DEBUG=true` and `ENVIRONMENT` is `local` or `development`. See [SKILLS.md](SKILLS.md) for portal, API, CLI, and AI-query-skill usage.

For automatic extraction on **PostgreSQL**, run a single persistent dispatcher
with the backend virtual environment and the same data root, database, scanner,
vision profiles, and credentials as the API:

```powershell
$env:DATABASE_BACKEND = 'postgresql'
& $Python manage.py run_extraction_dispatcher --poll-seconds 2
```

Run this alongside Django, not inside an HTTP request. It discovers eligible
extraction jobs across all accounts (including builders), reserves free slots up
to `INGESTION_MAX_WORKERS` (default 8), counts existing manual workers, and keeps
polling after the queue empties. PostgreSQL locks prevent duplicate dispatchers
and concurrent processing of the same message. Each job has a worker heartbeat
and progress on the evaluation jobs page; completed worker records are retained.
The dispatcher honors retry delays, cancelled operations, inactive organizations,
retired/merged projects, and existing dead letters. Model calls retain their
separate `VISION_MAX_CONCURRENT_REQUESTS` limit.

Starting the dispatcher authorizes processing existing and future eligible work,
which may incur configured model costs. **Configure a working malware scanner
before starting it normally**; unconfigured scanning is not bypassed by default, and affected uploads
will exhaust the normal retry budget with `PROVIDER_UNAVAILABLE`. Stop with
Ctrl+C (active jobs finish); restart after worker-code or environment changes.
Use a process supervisor for automatic restart in production. No OS service or
scheduled task is installed automatically.

**Temporary local builder-upload exception:** the local dispatcher task currently
sets `LOCAL_TRUST_BUILDER_UPLOADS=true`, explicitly approved while ClamAV setup is
deferred. This requires `DEBUG=true` and `ENVIRONMENT=local` or `development`.
Only uploads whose uploader still has active owner/editor rights in the project's
active organization qualify. Other uploads retain the configured scanner, and
previously rejected sources are not accepted. Source hash, size, MIME checks,
extraction, layout generation, retries, and independent publication review remain
unchanged. Each trusted document gets a `document.malware_scan_skipped` audit
event with `malwareScanned=false`. The legacy `scan_status=clean` value means
accepted for processing in this mode, **not** that ClamAV scanned the document.
The flag defaults to false and is rejected outside local/development DEBUG mode.

TODO: install/configure ClamAV and its signature updates, remove the local task's
`LOCAL_TRUST_BUILDER_UPLOADS` exception, scan previously trusted documents, and
verify builder uploads with real malware scanning before any production rollout.

Normal local operation uses `run_extraction_dispatcher` through
`manage_services.ps1`; production uses a supervised dispatcher process against
PostgreSQL. `process_outbox --once` remains available only for bounded diagnostics
and deliberate maintenance. Do not schedule overlapping invocations. The legacy
`--event-type all` default still processes both event types, and no OS timer is
installed or enabled automatically.

The media dashboard's **Run evaluations for this project** processes pending extraction
jobs for that project, including child jobs created while processing. It does not
commit drafts or restart completed/dead-letter jobs. Local development launches are
bounded by `INGESTION_MAX_WORKERS`; busy slots leave a durable waiting request and
show a queued warning. Queue workers dispatch waiting requests when capacity frees.
The equivalent command is `python manage.py process_outbox --event-type extraction --project-id <project-uuid> --owner-id <staff-user-id> --drain`.
On the global evaluation jobs page, **Run all my queued evaluations** retains its
user-wide scope. Production uses managed workers rather than browser process launch.

Outbox jobs stop automatic retries after their third failed attempt (`attempts > 2`)
and become **Dead letter**. Explicit retry actions may reset the attempt count.
The evaluation jobs page offers confirmed deletion for unpublished dead-letter
messages to staff with queue-view, project/layout-change, and outbox-delete
permissions. Deletion checks the current dead-letter version and worker ownership;
it removes the queue message, not project assets, operations, or audit history.
Restart existing worker processes after deploying worker-code changes.

Dashboard evaluation actions warn about earlier failures before submitting retries
or starting project work. The in-page dialog identifies affected layouts/documents,
error codes, and recorded attempts; **Cancel** changes nothing and **Continue**
confirms only that action. The server checks a five-minute signed confirmation bound
to the user, project, submitted action, and failure history. Changed failures require
confirmation again. Retained failed operations still warn after a dead-letter message
is deleted. Completed jobs and the three-attempt automatic retry limit are unchanged.

The existing worker uses database transactions spanning document extraction; use PostgreSQL and reassess transaction/worker isolation before multi-worker production scaling. Configure ClamAV independently: selecting a working LLM never bypasses the malware scan. No live model endpoint is contacted just to enumerate the catalog.

### Sample corpus

Use `python manage.py import_sample_corpus <Samples directory>` to produce a JSON catalog of non-ignored PDF samples. The catalog groups material by its top-level locality folder and categorizes it as a brochure, floor plan, elevation, agreement, or general project material. It excludes `Samples/.ignore/`, detects content duplicates by SHA-256, and reports files larger than the 100 MiB upload limit without importing them. Set `MAX_UPLOAD_BYTES` to override this limit for an environment.

For local pipeline testing, add `--import --username <existing user>`. This stages only eligible, non-duplicate PDFs as quarantined evidence under a dedicated sample organization; it does not publish facts. `--process` additionally invokes the configured malware scanner and extraction pipeline. For a local debug-only corpus, add `--test-scanner` to explicitly use a clean test scanner; this option is rejected when `DEBUG` is disabled. Add `--report <path>` to save the JSON catalog.

## Observability

Every API response carries `X-Correlation-ID`; asynchronous messages preserve the same correlation identifier. Set `OTEL_ENABLED=true` to enable Django OpenTelemetry tracing. The default OTLP HTTP endpoint is `http://localhost:4318/v1/traces` and can be changed with `OTEL_EXPORTER_ENDPOINT`. Privileged operational metrics report onboarding completion, extraction outcomes, review backlog, recommendation latency, source freshness, and provider availability without exposing buyer locations, budgets, document bodies, or credentials.

## Security notes

### Local clean script rebuild

The `rebuild_script_outputs` maintenance command inventories all projects with documents and defaults to a read-only preview. It requires a **new** `--backup-dir`; `--execute --reuse-recorded-scans` explicitly opts into local cleanup and checksum-bound reuse of a recorded clean scan (not a fresh malware scan). This operation is restricted to DEBUG + SQLite and requires an exclusive maintenance window with no uploads, edits, or workers. It refuses active outbox work and skips projects containing published/reviewed data, missing sources, or changed hashes.

Before cleanup it backs up SQLite and all referenced sources/derived files, verifies the ZIP, and writes a restore manifest. It rebuilds one project at a time with the rules provider and external enrichment disabled. Original uploads, projects, organizations, user accounts, published data and audit history are preserved. Old unreviewed plans/assets/runs/candidates/conflicts and their extraction review tasks are replaced; old document operations are marked superseded. Superseded generated files are deleted only after the project's replacement commits. A failed project rolls back its database changes and retains its previous outputs; interrupted runs can leave unreferenced files for later reconciliation.

The backup includes `db.sqlite3`, `media.zip`, `media-manifest.json` (original storage root/key, archive member, SHA-256), and a progressively updated `rebuild-report.json`. Keep these private: they contain original evidence and a complete local database. To restore, stop all application processes, restore the database and each archived object to the root/key in the manifest, verify hashes, and only then restart. Do not merge old and new SQLite data ad hoc. Standalone comparison reports and unrelated backup folders are not deleted.

In admin, **Floor plan assets → Image category** can filter floor plans, flat layouts, design, amenities or unclassified candidates. Script identification remains heuristic: cleanup removes historical duplicates, not every possible false positive.

Production configuration must provide exact hosts/origins, HTTPS, a strong secret, private media storage, and least-privilege worker identities. Original evidence should not be served from local storage in production; replace Django storage with a private object-store backend. Reviewer/admin accounts should be protected by the selected identity provider's MFA and conditional-access policies.
## Builder media activity and name confirmation

Organization records expose **Confirm name changes**, defaulting to false, in
Django admin. The builder media response includes `confirmNameChanges`; this
controls only the extra name-save dialog, not authorization, version checks,
review requirements, destructive-action confirmations, or AI-cost confirmations.

Deactivating a floor plan through admin or builder media also persists inactive
state on its derived layouts and audits each changed record. Plan/asset ancestry
is used first; legacy manual page crops are matched only when the document page
has one visible floor-plan source. Reactivating a floor plan does not reactivate
its layouts. No existing inactive plans are backfilled by the setting migration.
