# API Contracts

## 1. Conventions

- Base path: `/api/v1`.
- JSON uses camel case and UTF-8.
- Opaque IDs; UUID/ULID recommended.
- Dates are ISO 8601 UTC; monetary amounts include currency; dimensions include units.
- Collection pagination uses opaque cursors.
- Mutations accept `Idempotency-Key`; versioned resources use `If-Match`/ETag.
- Errors use RFC 9457 problem details with stable application codes and a correlation ID.
- Authentication uses secure server sessions or bearer tokens. Authorization is object-level.
- Never return storage credentials, partner credentials, or unrestricted third-party URLs.

## 2. Buyer requirements

### `POST /api/v1/requirement-profiles`

Creates a draft.

Response `201`:

```json
{
  "id": "req_01...",
  "status": "draft",
  "version": 1,
  "sections": {},
  "completion": { "percent": 0, "invalidSections": [] }
}
```

### `GET /api/v1/requirement-profiles/{profileId}`

Returns an owned/shared profile according to permission. Exact location coordinates may be reduced for shared read-only views.

### `PATCH /api/v1/requirement-profiles/{profileId}`

Requires `If-Match`. Accepts a merge patch over editable draft sections. Returns `409` with latest version metadata when stale.

Example section payload:

```json
{
  "places": [
    {
      "placeId": "place_provider_opaque",
      "label": "BKC",
      "frequencyPerWeek": 5,
      "modes": ["transit", "car"],
      "arrivalWindow": { "start": "09:00", "end": "10:00", "timeZone": "Asia/Kolkata" },
      "maxOneWayMinutes": 60,
      "precision": "locality"
    }
  ],
  "home": {
    "configurations": ["2BHK"],
    "carpetAreaSqFt": { "minimum": 650, "preferred": 800 },
    "bathroomsMinimum": 2
  },
  "budget": {
    "currency": "INR",
    "purchaseMaximum": 30000000,
    "allInMaximum": 33000000,
    "isHardConstraint": true
  }
}
```

### `POST /api/v1/requirement-profiles/{profileId}:submit`

Validates consent and full schema, freezes a version, and starts recommendation computation. Response `202` with operation URL.

## 3. Places and travel

### `GET /api/v1/places:suggest?q={query}&session={opaque}`

Returns safe provider-normalized suggestions. Server calls the maps provider. Do not expose provider keys.

### `POST /api/v1/travel-estimates:preview`

Returns a bounded preview for selected origins/destinations/modes. Rate limited; exact buyer origins are not logged.

## 4. Recommendations

### `POST /api/v1/recommendation-requests`

```json
{ "profileId": "req_01...", "profileVersion": 4 }
```

Response `202`:

```json
{
  "requestId": "rec_01...",
  "status": "computing",
  "statusUrl": "/api/v1/recommendation-requests/rec_01..."
}
```

### `GET /api/v1/recommendation-requests/{requestId}`

Ready response:

```json
{
  "status": "ready",
  "profileVersion": 4,
  "algorithmVersion": "match-1.0",
  "generatedAt": "2026-08-29T12:00:00Z",
  "items": [
    {
      "projectId": "prj_01...",
      "score": 84.2,
      "confidence": 0.91,
      "components": [
        { "code": "COMMUTE", "score": 0.82, "weight": 0.35, "status": "available" },
        { "code": "AMENITIES", "score": 0.76, "weight": 0.15, "status": "available" }
      ],
      "reasonCodes": ["ALL_HARD_CONSTRAINTS_MET", "GOOD_WEIGHTED_COMMUTE"],
      "freshness": { "projectFactsAsOf": "2026-08-20T00:00:00Z" }
    }
  ],
  "excludedSummary": [{ "reasonCode": "OVER_HARD_BUDGET", "count": 12 }]
}
```

## 5. Catalog

### `GET /api/v1/projects`

Facets: locality, configuration, carpet-area range, all-in price range, MahaRERA status, possession period, amenities. Returns published read models by default.

Listing reads are public. Anonymous responses contain listing fields only; drafts,
retired projects, and unpublished configuration revisions are excluded. Publication
is separate from registry verification or location approval. Writes are not supported.

Optional `includeProcessing=true` adds `ingestion` and `needs_review` listing summaries;
omitted or `false` stays published-only, and other values return 400. This option applies
only to the collection, for both guests and authenticated buyers. Private drafts,
retired, archived and merged records remain excluded. Processing summaries contain
ID, submitted name, locality, lifecycle status and update timestamp. They retain the
listing shape but return empty configurations, amenities and registry ID, unavailable
verification, and null verification date. Coordinates are included only when the latest
location reference is resolved and accepted; otherwise they are null. No evidence,
documents, internal address, metrics, reviewer IDs or draft values are serialized.
Locality filtering applies to all summaries; fact-based filters (configuration, area,
price, registry status and amenity) exclude processing entries rather than inspecting
unpublished facts. Pagination preserves the explicit opt-in query.

### `GET /api/v1/projects/{projectId}`

Returns normalized project details, metric definitions, evidence summaries, verification timestamp, and stale/missing flags. Direct evidence access is a separate authorized endpoint.

With `PROJECT_PUBLIC_PREVIEW_ENABLED=false`, anonymous callers receive the same restricted fields as the project list, without
internal evidence, audit metadata, or account data. The richer detail response remains
authenticated. Unpublished detail requests remain 404 even with `includeProcessing=true`;
the opt-in does not enable comparisons, shortlists, ranking or publication.

Temporary exception: `PROJECT_PUBLIC_PREVIEW_ENABLED=true` allows guests to retrieve
published, ingestion and needs-review project details, including unreviewed project
facts and configurations. Internal address metadata and candidate evidence references
are omitted. This flag defaults to true for local debug development and false in
production; only explicit `true`/`false` values are accepted. Disable it and restart
the backend to restore restrictions. Detail responses use `Cache-Control: private, no-store`.

### `GET /api/v1/projects/{projectId}/gallery`

Public read-only, cursor-paginated response: `{ results, next, previous, access }`.
Access is `published`, `review`, `restricted`, or `preview`. Each item contains opaque
`id`, same-origin `url`, `category`, `title`, pixel `width`/`height`, `reviewState`, and
`layouts`. Layouts contain presentation names, configuration, carpet area in square
feet, room/bathroom/balcony counts, orientation, review state and allowlisted room
measurements (`room`, `lengthFt`, `widthFt`, `areaSqFt`). No object keys, raw extraction
or original document payloads are included.

With the preview flag off, public media must belong to active approved layouts of
published projects. Authorized project editors/reviewers can see unreviewed media;
other processing requests return an empty `restricted` gallery, not private data.
With the preview flag on, guests may see the same unreviewed project media. Processing
responses with review/preview access additionally include `project` data. Only clean,
ready PNG/JPEG/WebP assets are eligible; inactive/hidden layouts, retired/merged
projects and unsafe sources stay excluded. Byte access at
`GET /api/v1/media/floor-plan-assets/{assetId}` applies the same guest checks, including
after the flag is disabled. Both responses are `private, no-store`. Existing authorized
admin media access is unchanged. The flag never enables private buyer endpoints,
raw document access, writes, approval, publication, comparisons or ranking.

### `GET /api/v1/map-stations`

Public read-only response: `{ "stations": [...], "lines": [...], "warnings": [...] }`. Each station has
an opaque ID, name, kind (`metro` or `railway`), line, location, source, serviceStatus,
asOf, and optional authority sourceUrl. User-added CSV candidates and accepted saved
references remain `unverified`; only configured authority records supply operational,
planned, or under-construction status. Duplicate candidates are collapsed and conflicting
coordinates withheld. Saved references from unpublished projects are excluded. Responses
contain no reviewer IDs, private project references, or travel routes; reads make no
provider requests and do not publish data or modify the station source files.

Each line segment has an opaque `id`, `name`, `kind`, ordered `stationIds`,
`source: "user_candidate"` and `geometryStatus: "schematic_unverified"`.
Segments follow contiguous same-line/same-kind CSV rows; they are display schematics,
not verified stop order, track geometry, operating service or a routing graph.
Invalid or conflicting candidates and line/kind changes break segments. Consecutive
identical duplicates collapse; a repeated nonconsecutive stop starts a new segment.
Only segments with at least two retained stations are returned. Unknown-line stations
remain unconnected. Shared coordinates or crossing segments never create transfers.
Authority station metadata does not verify a candidate segment's geometry.
Missing or disabled candidate data yields an empty `lines` array.

### `POST /api/v1/project-comparisons`

Accepts 2–4 project IDs and optional profile version. Returns aligned fields and match components without changing the underlying recommendation.

### `POST /api/v1/shortlists/{shortlistId}/projects`

Idempotently adds a project. Share links are created through an explicit endpoint with expiry and can be revoked.

## 6. Developer organizations and projects

### `POST /api/v1/developer-organizations/{organizationId}/projects`

```json
{
  "mahaReraId": "P...",
  "submittedName": "Project name",
  "address": { "locality": "...", "city": "Mumbai", "postalCode": "..." }
}
```

Returns `201` draft or `409` duplicate candidate with safe match metadata.

### `POST /api/v1/developer-projects/{projectId}/documents:upload-intent`

Request includes filename, declared media type, byte length, and SHA-256. Response contains a short-lived single-object upload action, required headers, maximum size, and expiry. It must not grant list/read/delete permissions.

### `POST /api/v1/developer-projects/{projectId}/documents/{documentId}:complete`

Requires `Idempotency-Key`. Accepts optional `visionProfileId` from the project's vision-profile discovery endpoint. Verifies upload state and queues work, without running scanning/extraction inline. Response `202` includes `operationId`, `statusUrl`, `queueStatus: "pending"`, and a nonsecret `visionSelection` snapshot (profile ID, provider, model, configuration hash).

### `GET /api/v1/developer-projects/{projectId}/vision-profiles`

`FLOOR_PLAN_EXTRACTION_MODE=script` (default) returns the built-in `rules-only` profile; `ai` returns configured vision profiles and the configured default. Omitting `visionProfileId` on completion/reprocessing selects the current mode's default. Script mode rejects new AI-profile requests. An explicit `rules-only` selection is accepted in either mode. The saved selection pins execution, so changing the setting does not switch already-queued jobs. Script output identifies reviewable page candidates without LLM calls; it is not room segmentation or complete visual measurement extraction.

Project editors/owners and reviewers only. Returns `defaultProfileId` and `profiles` containing `id`, `label`, `provider`, `model`, and `hosting` (`local` or `cloud`). Providers include local Ollama, local OpenAI-compatible, Microsoft Foundry, and configured cloud OpenAI-compatible instances. Only administrator-configured enabled model profiles are selectable. No endpoints, keys, or secret references are exposed.

### `GET | POST /api/v1/developer-projects/{projectId}/ingestion-queue`

GET returns `pending`, `delayed`, `deadLettered`, and at most 100 pending/dead-letter `items`, including saved selections and safe retry information. Editors/owners and reviewers may read. POST is editor/owner-only, accepts `limit` (integer 1–20, default 10), and synchronously processes a bounded project-only batch. Returns `200` with `requested`, `results`, and the updated queue. Long extraction batches should use the scheduled worker rather than this synchronous HTTP action. Each item uses its saved provider/model; there is no batch-wide override.

### `POST /api/v1/developer-projects/{projectId}/documents/{documentId}:reprocess`

Editors/owners only. Requires `Idempotency-Key` and `reasonCode`; accepts optional `visionProfileId`. Returns `202` with the new operation and pinned selection. An outstanding active request is rejected with `ALREADY_QUEUED`. Default-profile changes do not reroute pending requests; a changed/disabled selected configuration fails closed. This route enqueues only, leaving execution to a manual batch or scheduled worker. The administrative reprocess route uses the same request/idempotency semantics.

### `GET /api/v1/developer-projects/{projectId}/documents`

Returns statuses and safe error codes, not raw parser messages. Includes `storageBucket` (`todo`, `completed`, `legacy`). A completed storage bucket means processing succeeded, not human approval or external verification. Supported formats and converter requirements are defined in [document ingestion and outputs](11-document-ingestion-and-project-outputs.md).

### `GET /api/v1/admin/floor-plan-segmentation`

Staff administrator only. Returns supported modes, expected-count bounds, and enabled AI profile IDs/labels/models/hosting. It never returns endpoints, credential references, or secrets. This catalog is available independently of the default document-ingestion mode because flat separation is an explicit administrative operation.

### `POST /api/v1/admin/floor-plans/{floorPlanId}:segment-units`

Staff administrator only and requires `Idempotency-Key`. Accepts `mode` (`script`, `ai`, or `both`), optional integer `expectedCount` from 1–40, optional configured `visionProfileId`, and optional boolean `supersedeSource` (default false). Browser callers cannot provide provider endpoints, model overrides, or credentials.

Script mode uses deterministic CV; color is supporting evidence rather than a guaranteed flat boundary. AI mode invokes the pinned `floor-plan-flat-separation-ai` query skill. Combined mode runs both independently and may return `partially_completed` with per-method safe error codes. Expected count is a quality gate and never forces invented regions. Successful methods return workflow IDs, plan IDs, authenticated asset URIs, counts, and nonsecret provenance. Crops require human review. Source supersession occurs only when explicitly requested and every selected method completes.

### `GET /api/v1/developer-projects/{projectId}/extraction-output`

Project editors/owners and reviewers only. Returns `projectId`, `scope: "latest_output_per_document"`, `limit: 100`, and `documents[]`. Each document entry includes `documentId`, `ingestionRunId`, `status`, `data` (`project-output-1.0`) and output download URIs. Most recent nonfailed generated outputs are selected independently per document; registration phases and conflicting claims are not implicitly merged. Unknown fields are null with status and evidence, not guessed defaults.

### `GET /api/v1/ingestion-runs/{runId}/outputs/{outputName}`

Allowed output names: `project` (JSON), `layout-metrics` (JSON), `layout-metrics-csv` (CSV). Authenticated project editors/owners and reviewers only. Returns an attachment with private/no-store caching and `nosniff`; unrelated users receive access denied. Failed runs, unknown outputs and missing files are not served as successful data. Paths are taken from the persisted run manifest, never from caller-supplied filesystem paths.

The project JSON includes typed identity/parcel/RERA fields, station claims and sourced routing information, image URI categories, amenities, prices/offers, and developer registry/sentiment records. Detailed nested schemas, validation states and canonical metric units are defined in [document ingestion and outputs](11-document-ingestion-and-project-outputs.md#4-structured-project-contract).

### `POST /api/v1/developer-projects/{projectId}:submit-review`

Requires a verified organization member, a verification attempt, required fields, and accepted processing attestation.

## 7. Evidence and review

### `GET /api/v1/review/tasks?status=open&type=conflict`

Reviewer/admin only; cursor paginated.

### `GET /api/v1/review/tasks/{taskId}`

Returns candidate facts, confidence, provenance, prior decisions, and same-origin evidence references.

### `POST /api/v1/review/tasks/{taskId}:resolve`

```json
{
  "expectedVersion": 3,
  "selectedCandidateId": "candidate_01...",
  "correctedValue": null,
  "reasonCode": "AUTHORITATIVE_SOURCE_CONFIRMED",
  "comment": "Optional non-sensitive reviewer note"
}
```

Developer users cannot call review approval routes for their own content.

### `POST /api/v1/review/projects/{projectId}:publish`

Requires review permission, expected fact-set version, and idempotency key. Publication is atomic.

## 8. Media

### `GET /api/v1/media/{documentId}/pages/{pageNumber}`

Authenticates and authorizes the entity relationship, then streams a safe derived image/PDF representation with `nosniff`, content disposition, range policy, and no-store/private cache policy as appropriate. The route never accepts a caller-provided upstream URL.

Downloads that require original evidence use a distinct audited action. Unsupported active content is never served inline.

## 9. Verification and integrations

External APIs are not browser-facing. Internal adapter results use typed contracts:

```json
{
  "provider": "maharera",
  "providerRecordId": "opaque",
  "retrievedAt": "2026-08-29T12:00:00Z",
  "sourceVersion": "provider-specific",
  "fields": {},
  "snapshotHash": "sha256-base64url"
}
```

Partner status, quotas, and raw errors remain internal. Where licensing requires attribution, the public DTO includes approved attribution text and source timestamp.

## 10. Status and health

- `/health/live`: process liveness only.
- `/health/ready`: critical dependencies with no secret details; restricted externally.
- `/api/v1/operations/{operationId}`: owner-authorized async operation state.
- Admin integration health uses a privileged endpoint and reports categories, never credentials or full payloads.

## 11. Security headers

Normal application HTML keeps a strict nonce/hash-based CSP and does not allow broad inline scripts. APIs return `X-Content-Type-Options: nosniff`, appropriate cache controls, and a restrictive framing policy. Any future isolated interactive document must be explicitly marked for a dedicated CSP rather than allowing controllers to supply arbitrary weaker policies.
