# MMR Real Estate Backend

Django/DRF implementation of the contracts in the workspace documentation. It includes session authentication, consent, immutable/versioned buyer requirements, developer organizations, secure upload quarantine, extraction/review boundaries, MahaRERA snapshots, evidence-backed publication, catalog search, deterministic recommendations, comparisons, shortlists/shares, notifications, audit records, operations, metrics, and health/OpenAPI endpoints.

## Local setup

1. Activate `.venv`.
2. Install `requirements.txt`.
3. Set `DJANGO_SECRET_KEY` outside local development.
4. Run `python manage.py migrate`.
5. Create an administrative user with `python manage.py createsuperuser`.
6. Run `python manage.py runserver`.
7. In a second process, run `python manage.py process_outbox --event-type recommendations`. Document requests remain in the todo queue until a developer triggers a batch or a separate document timer/worker runs.

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
name, address, coordinates or route content. Saved references can be refreshed on
demand. Configure a private cache and review Google Maps licensing, attribution,
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

### PostgreSQL and concurrent sample ingestion

The local application now uses one PostgreSQL 17 primary at `127.0.0.1:55432`.
There is no read replica: separate tables alone do not fix SQLite's database-wide
writer lock. `setup_local_postgres.ps1` provisions the loopback-only Docker container
`realestate-postgres` and persistent volume `realestate-postgres-data`. Its randomly
generated password is stored in the git-ignored, Windows-ACL-restricted
`.local/postgres.json`; never commit or display that file. Do not delete the volume.

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

The local Azure configuration is in [vision-profiles.local.json](vision-profiles.local.json).
It contains no credentials and is not loaded automatically. Load its JSON into
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

Staff administrators can open a clean full-floor record under **Admin → Floor plans** and choose **Separate flats**. The operation supports `script`, `ai`, and `both`; combined mode runs the methods independently so an inconclusive script result does not block AI. AI uses only configured server-side vision profiles. Generated crops remain review proposals, and the source is preserved unless explicit supersession is requested and all selected methods succeed.

Each project also has a **Media dashboard** link in Django admin with project-scoped Amenities, Floor plans, and Layouts tabs. Layout cards expose extracted room sizes, calculated rectangular areas, counts, confidence, features, and provenance. **Report for update** creates a deduplicated high-priority review task; it does not silently mutate or rerun extraction.

The project admin list has a **Projects per page** selector for 10, 20, 50, or all records. The selected size is retained while moving between list pages, together with active admin filters.

Project records and media dashboards include previous/next project controls. The media dashboard also includes a back-to-project action and a simple carousel of the project's saved images. Local administrators can generate missing script layouts across all eligible projects with `python manage.py separate_all_floor_plan_units --report <new-report.json>`; existing script segmentation is not replaced and ambiguous sources are reported for follow-up.

The equivalent staff-only API is `POST /api/v1/admin/floor-plans/{floorPlanId}:segment-units` with an `Idempotency-Key`. `GET /api/v1/admin/floor-plan-segmentation` discovers safe mode/profile metadata. For local development, `isolate_floor_plan_units` exposes the same service and is rejected unless `DEBUG=true` and `ENVIRONMENT` is `local` or `development`. See [SKILLS.md](SKILLS.md) for portal, API, CLI, and AI-query-skill usage.

For a timer, schedule `python manage.py process_outbox --once --event-type documents --limit 5` with Windows Task Scheduler or cron using the backend virtual environment and backend working directory. Set the same catalog/credential environment variables as the API. Do not overlap scheduled runs; use one worker for SQLite. For periodic processing in a supervised worker, use `python manage.py process_outbox --event-type documents --poll-seconds 300 --limit 5`. The legacy default `--event-type all` still processes both event types. No OS timer is installed or enabled automatically.

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
