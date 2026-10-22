# Document ingestion and project outputs

## 1. Scope and implemented flow

This document extends the original ingestion requirements and describes the Django implementation. Both `script` and `ai` modes are retained. Local identification and typed exports work without an AI endpoint. Registry validation, real journey times, and social sentiment require approved external data access; unavailable data is explicitly marked, never manufactured.

1. An authorized project owner/editor requests an upload intent, uploads bytes, then calls completion with an idempotency key and optional profile ID.
2. The API verifies the declared size, SHA-256 and real file signature. The source is stored privately in the todo bucket. Completion creates the durable outbox job; it does **not** execute inference in the upload request.
3. A user starts a project-only bounded batch, or a scheduled worker processes queued documents.
4. The worker rechecks the stored bytes/hash, scans for malware, and optionally converts supported Office formats into a temporary PDF. The original file is retained unchanged.
5. Extract inert text with source-page provenance. Retain paragraphs/newlines for structured label extraction; chunk copies support existing evidence search.
6. Select floor-plan and flat-layout candidates from text/graphics. Render selected pages at high resolution. Identify additional design/amenities pages. Persist private PNGs and expose only authenticated asset URIs in report fields.
7. Script mode identifies page candidates and unambiguous embedded labels. AI mode analyzes the saved plan images and then up to 20 saved images for general project claims using fixed JSON schemas. Every model claim retains a supporting quotation, source page, asset ID and model provenance.
8. Build the structured project report; compare eligible parcel totals with approved registry evidence. Invoke configured transit/developer/sentiment adapters. No adapter means `unavailable`, not verification or a zero value.
9. Write versioned project JSON and layout-metrics JSON/CSV. Create a human review task. No data is auto-published.
10. On successful processing, move the original to completed. Visual-provider failures retain the source in todo and produce an `awaiting_visual_analysis` report where possible. Parser/converter failures and malware rejection do not move the file to completed.

`completed` is a processing/storage state, **not** approval, proof of title, completion of human review, or successful external enrichment. Missing external data does not prevent otherwise successful document processing.

## 2. Formats and conversion boundary

| Format | Handler |
| --- | --- |
| PDF | PyMuPDF text, drawing/image signals, selected-page rendering; encrypted PDFs are rejected |
| PNG/JPEG | Image validation and private PNG derivative; AI mode can read visible text/plan details |
| TXT/Markdown/CSV/JSON | UTF-8 inert text; explicit labels/values are extracted, not executed |
| DOCX | Native paragraphs/tables and embedded image handling; optional conversion gives paginated visual analysis |
| DOC, RTF, ODT, PPT/PPTX, XLS/XLSX | Validated signature/container, then configured Office-to-PDF conversion |
| Google Docs | Export to DOCX/PDF first; `.docs` is not a supported portable document format |

Legacy Office handling requires `OFFICE_CONVERTER_ENABLED=true` and an approved `OFFICE_CONVERTER_EXECUTABLE` (default `soffice`, LibreOffice-compatible CLI). Without it these jobs fail with `OFFICE_CONVERTER_REQUIRED`, remain in todo, and can be reprocessed after setup. Nothing installs or runs an Office converter automatically. Native DOCX text/table processing remains available with conversion disabled.

The converter runs only after malware scanning, with a throwaway user profile, highest macro-security setting, bounded timeout and output size. Run it in an OS/container sandbox with no outbound network, low privilege and resource limits before accepting untrusted Office documents in production. Temporary profiles and macro settings are not a substitute for a sandbox. Conversion page numbers refer to the generated PDF, recorded in `sourceConversion.pageNumberBasis`.

Upload limits remain 100 MiB by default. ZIP containers are bounded to 10,000 entries / 50 MiB expanded bytes / 100:1 compression and reject detected VBA/script parts. PDF extraction defaults to 500 pages (`MAX_DOCUMENT_PAGES`, maximum 2000). Encrypted or unsupported content is not silently treated as successful extraction.

## 3. Private file layout and lifecycle

Paths are relative to `WORKSPACE_DATA_ROOT`, defaulting to the workspace root:

- `user/media/todo/{project-id}/...` — new uploaded sources, opaque collision-resistant names.
- `user/media/completed/{project-id}/{document-id}/...` — successfully processed immutable originals.
- `output/media/images/{project-id}/general/{workflow-id}/...png` — candidate design/elevation images.
- `output/media/images/{project-id}/floor-plans/{workflow-id}/...png` — whole-floor/tower plans.
- `output/media/images/{project-id}/layouts/{workflow-id}/...png` — flat/unit layout images.
- `output/media/images/{project-id}/amenities/{workflow-id}/...png` — candidate amenities images.
- `output/media/images/{project-id}/unclassified/{workflow-id}/...png` — uncertain or mixed page candidates.
- `output/media/projects/{project-id}/{ingestion-run-id}/project.json` — document-scoped project output.
- `output/media/projects/{project-id}/{ingestion-run-id}/layout-metrics.json` and `layout-metrics.csv` — computed/unknown metrics.

Images are real files; the database/report stores metadata and **URI references**, not image bytes or base64. Full selected PDF pages are preserved and are not automatically segmented during ingestion. An authorized administrator may explicitly invoke script, AI, or independent combined flat separation afterward. Generated crops are stored below `output/media/images/{project-id}/layouts/{source-asset-id}/{method}/`, retain source/bounds/method provenance, and require review. Color is only one script signal; ambiguous segmentation fails closed or may be compared with AI. The full-floor source remains evidence unless explicit supersession is requested and every selected method succeeds. Images are accessed through `/api/v1/media/floor-plan-assets/{assetId}`; no public static route is added for user/output media. Old files under the previous backend media root remain readable through the storage router. Existing records are not bulk-migrated or reprocessed by a code update.

Relocation copies and verifies bytes, saves the new DB key, and deletes the original copy only after the database transaction commits. Rollback/crash may leave an unreferenced copy for an administrator to reconcile, but should not lose the current source. Reprocessing moves completed sources back to todo as part of enqueueing. Files dropped into a directory alone are **not** jobs: the API's authorization, hashes and outbox record are required.

PostgreSQL is required. Workers use PostgreSQL session advisory locks for ownership and short row-locking transactions for state changes; use a direct or session-pooled database connection for concurrent processing.

## 4. Structured project contract

Outputs use `schemaVersion: project-output-1.0`, identify project/document/run and keep `reviewState: unreviewed`.

Ordinary fields have `value`, `status` (`extracted`, `unknown`, `conflict`) and `candidates`. Each candidate retains `documentId`, `pageNumber`, `evidenceText`, `source`, and review status. Visual candidates also identify the asset/provider/model. Different values produce `conflict` and a null selected value rather than selecting one silently. Submitted project names are kept separately as `submittedName`; they are not misrepresented as document-extracted names.

| Requirement | Stored output |
| --- | --- |
| Name | `name` |
| Location | `location` |
| RERA registration | `rera.registrations[]` and per-page evidence; multiple phases are preserved |
| Official project link | `projectUrl` only from an approved matching registry result; otherwise null plus generic registry homepage |
| Total land holding | `parcelDetails.landHoldingSqFt` normalized from acres/hectares/square metres/square feet supported labels |
| Towers, residential units, parking | `totalTowers`, `totalResidentialUnits`, `totalParkings`; explicit project totals, not sums of marketing numbers |
| Units/lifts per tower | `towerUnitsAndLifts[]` distinguishes `residentialUnits`, `unitsPerFloor`, and `lifts`; no multiplication from unspecified floor counts |
| Effective parcel | `landHoldingSqFt / totalResidentialUnits`, in sqft/residential unit; null if ambiguous, missing, or zero denominator |
| Metro/local rail | `nearestMetro`, `nearestLocalStation`, separate `documentClaim`, provider status, walk/drive minutes and Google Maps journey URIs |
| Design/floor/layout images | `images.general`, `images.floor-plans`, `images.layouts` URI lists, with `imageEvidence` |
| Amenities | `amenities.nature`, `.social`, `.unclassified`; taxonomy categorization is provisional |
| Offered price | `offeredPriceInr` with quoted evidence and currency normalization |
| Offers | `offers`; extracted offer wording, not a claim that it is currently valid |
| Developer record | `developer.name` and `.mahareraRecord` |
| Social sentiment | `developer.socialSentiment`, separate from the authoritative registry record |

Script extraction recognizes explicit labels, not arbitrary prose perfectly. Image-only pages have unknown general facts in script mode. Page classification is heuristic and bounded: eight plan pages plus up to `DOCUMENT_EXTRA_IMAGE_LIMIT` (default 12) additional design/amenities pages; AI general-page analysis is capped at 20 images. An omitted page is not evidence that a feature is absent.

## 5. External enrichment contract

`PROJECT_ENRICHMENT_BACKEND` is an administrator-specified Python import path to an adapter class with these methods:

- `registration(number)` — registry record for **exactly** that registration.
- `stations(location)` — nearest metro/local station and sourced typical travel times.
- `developer(registration_numbers)` — official developer track record.
- `sentiment(developer_name)` — authorized, aggregated social sentiment.

Each method returns null for unavailable data or an object with `sourceUrl` (HTTPS), `retrievedAt` (ISO date-time), and `data` (JSON object). Responses are size/JSON validated. Registry/developer sources must use an official MahaRERA host. Adapter failures produce safe unavailable status; document URLs are never fetched by the extractor. These are extension contracts, **not a bundled working MahaRERA/maps/social scraper**. Configure an approved adapter with licensed access; no browser cookies, scraping bypass, or invented provider response is supplied.

Registration `data` uses `registrationNumber`, optional `projectUrl`, and parcel keys with the same canonical names/units as the report. A mismatched registration is rejected. Only one unambiguous registration plus a known document value can receive `matched` or `mismatch`; multiple registration phases are marked `scope_review_required`, and totals are not summed. Tower claims remain explicitly unverified until individually reviewed.

Station `data` contains `metro` / `local` objects with `name`, `isNearestVerified`, `walkingMinutes`, `drivingMinutes`. Times must be finite nonnegative numbers. Map links are generated from supplied location/station names with encoded walking/driving parameters; they are **links**, not live route API results. A brochure's nearest-station claim does not prove nearest status or usual duration. No straight-line approximation is labeled a measured journey.

Sentiment `data` must contain a positive `sampleCount`, `windowStart` and `windowEnd` date-times, plus adapter-defined aggregate labels/scores and source methodology. Missing coverage yields unavailable. Retain aggregate evidence and uncertainty; do not turn opinions into verified developer facts or collect unnecessary personal identifiers.

## 6. Layout metrics and sample CSV mapping

The reference CSV combines raw room measurements, comparison indicators and custom scores. The export preserves the recognizable metric names but does not invent their missing formulas:

| Metric | Calculation / boundary |
| --- | --- |
| `rooms[].areaSqFt` | Visible/validated `lengthFt × widthFt`; rectangular approximation |
| `roomRectangleSumSqFt` | Sum of available rectangles; **not** RERA carpet area and may not describe complete/non-overlapping geometry |
| `Master_Bedroom_sqft_adj`, `Bedroom2_sqft_adj` | Area only when room name explicitly identifies the room; currently no adjustment model is applied, documented warning |
| `Density` | Effective parcel, sqft/residential unit (the sample convention, not people per acre) |
| `towerLiftMetrics[].unitsPerLift` | Per-tower total residential units / lifts, when both exist and lifts > 0 |
| `Metro_walk_min`, `Local_walk_min` | Sourced provider walk times only |
| `PSF`, `Locn_PSF` | Null until a price is bound to the exact layout and licensed local market data is supplied |
| `Unused_Space` | Null without complete validated room/circulation geometry; not carpet area minus a partial room sum |
| `time_dep`, `Score_Total` | Null until definitions and weights are specified |

Every plan row identifies source plan/page/model and review state. Rows without room dimensions have `status: pending_dimensions`. Script mode is intentionally not a visual room measurement engine. CSV text is formula-escaped for spreadsheet safety. JSON retains nested room and tower metrics.

## 7. API and admin access

- Existing upload, complete, reprocess, queue and profile endpoints remain in place.
- `GET /api/v1/developer-projects/{projectId}/extraction-output` returns the latest generated nonfailed report **per document** (up to 100 documents), plus artifact URIs. Document/phase boundaries are preserved; this is not an automatic merge into published project facts.
- `GET /api/v1/ingestion-runs/{runId}/outputs/project` downloads project JSON.
- `GET /api/v1/ingestion-runs/{runId}/outputs/layout-metrics` downloads metric JSON.
- `GET /api/v1/ingestion-runs/{runId}/outputs/layout-metrics-csv` downloads metric CSV.
- Owners/editors and reviewers can read; unrelated accounts cannot. Failed runs are not exposed as successful outputs. Private no-store responses do not expose filesystem keys.
- Document listing adds `storageBucket`: `todo`, `completed`, or `legacy`.
- In Django admin, Project has a **Structured extraction outputs** link; Ingestion Run has **Download output files**. Earlier records show no outputs until reprocessed.

## 8. Verification and remaining setup

Regression coverage is in `test_project_outputs.py`, along with the earlier queue/mode/vision tests. It covers field extraction, conflicting counts, units, source status, URI access control, conversion-required errors, and commit-time file relocation. Tests must run against temporary workspace roots, never real sample output folders.

Required operational setup: approved malware scanner, sandboxed Office converter for legacy types, vision deployment/credentials for AI mode, optional enrichment adapter and licenses, scheduled worker and filesystem ACLs/retention. This update does not install a timer, provision a model, auto-process stored documents, approve facts, or provide external credentials. Production review must cover native document parsing, conversion sandbox, external adapter egress, retention of versioned artifacts and serialized worker operations.