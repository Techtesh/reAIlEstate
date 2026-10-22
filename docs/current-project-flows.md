# Current project flowcharts

Checked against the working tree on 22 October 2026. These diagrams replace the earlier chat diagrams. They describe application paths, not proof that external providers or cloud infrastructure are enabled. Small conversion/validation helpers are grouped where noted.

## 1. Login and workspace authorization — current

```mermaid
flowchart TD
    B[Buyer frontend] --> BG[GET auth/login: CSRF bootstrap]
    BG --> BP[POST auth/login: SessionLoginView.post]
    BP --> BA[authenticate and require active account]
    BA --> SESSION[Django login: server-side session]
    D[Builder frontend] --> DG[GET auth/builder-login: CSRF bootstrap]
    DG --> DP[POST auth/builder-login: BuilderLoginView]
    DP --> DA[authenticate and builder_memberships]
    DA --> DR[Active user, membership and organization; eligible effective role]
    DR --> SESSION
    A[Admin] --> AL[Django admin login: active staff]
    AL --> AS[Admin session]
    AS --> AW[admin_project_workspace]
    AW --> PERM[platform_project_editor: change_project permission]
    PERM --> AP[Shared admin workspace]
    AP --> ADD[Creation additionally requires add_project]
    AP --> EDIT[Layout mutations additionally require change_floorplan]
    SESSION --> ME[CurrentUserView: restore account]
    ME --> AUTH[Each protected resource checks authorization]
```

Admin workspace access does not require builder membership. Ordinary builder login still does. Buyer/builder logout uses `SessionLogoutView.post()`; protected API failures trigger private-state cleanup. Admin uses Django's admin logout flow.

References: [login handlers](../reailEstate/reailEstate_common/backend/platform_api/views.py), [builder access](../reailEstate/reailEstate_common/backend/platform_api/builder_access.py), [workspace permissions](../reailEstate/reailEstate_common/backend/platform_api/project_workspace.py), [frontend serving](../reailEstate/reailEstate_common/backend/config/service_frontend.py).

## 2. Project creation and editing — current

```mermaid
flowchart TD
    B[Builder project editor] --> BR[Builder routes: organization membership scope]
    A[Django admin Projects Add / shared workspace] --> AR[Admin routes: platform permissions]
    BR --> CREATE[BuilderProjectsView.post]
    AR --> CREATE
    CREATE --> INPUT[CreateProjectInput.is_valid]
    INPUT --> ORG[Lock authorized active organization]
    ORG --> LOC[resolve_project_location if supplied]
    LOC --> SHARED[DeveloperProjectCreateView.create_project]
    SHARED --> VALID[ProjectCreateSerializer; normalize_maha_rera_id; duplicate check]
    VALID --> SAVE[Create Project with verifying status]
    SAVE --> SNAP[Create fallback VerificationSnapshot: unavailable match]
    SNAP --> AUDIT[Audit project.created]
    AUDIT --> PROP[submit_project_location if supplied; require_new_review]
    PROP --> RETURN[Return project]
    BR --> PATCH[BuilderProjectView.patch]
    AR --> PATCH
    PATCH --> LOCK[locked_project: expectedUpdatedAt]
    LOCK --> CHANGES[Validate changes; reset verification if registration changes]
    CHANGES --> REVIEW[require_new_review; save and audit]
```

The shared creation path replaces the earlier diagram showing a separate inherited Django model-save path. Creation records a verification attempt, not verified registry success. The shared editor cannot directly set publication or verification status.

References: [shared route factory](../reailEstate/reailEstate_common/backend/platform_api/urls_project_workspace.py), [project handlers](../reailEstate/reailEstate_common/backend/platform_api/builder_views.py), [creation routine](../reailEstate/reailEstate_common/backend/platform_api/views.py).

## 3. Location, external sources and publication — current

```mermaid
flowchart TD
    L[Location submission] --> RES[resolve_project_location / direct_location]
    RES --> COORD[coordinates: finite numbers and geographic bounds]
    COORD --> SUB[submit_project_location]
    SUB --> TASK[Unreviewed external_sources task; older proposals superseded]
    C[Shared workspace content actions] --> CONTENT[ProjectContentView.post]
    CONTENT --> SEARCH[GoogleProjectMaps.search / registry_lookup]
    CONTENT --> SOURCES[submit_sources: selected place or validated registry evidence]
    SOURCES --> COMMIT[commit_references: unreviewed task]
    CONTENT --> AMENITY[add_manual_amenities: unreviewed claims; renewed review]
    TASK --> ACCEPT[apply_manual_reference or apply_google_reference]
    COMMIT --> ACCEPT
    ACCEPT --> GATE[Reviewer permissions, second-approver policy and version checks]
    GATE --> APPLY[_save_google_location: accepted coordinates and audit]
    APPLY --> PUB[PublishProjectView.post]
    AMENITY --> PUB
    PUB --> CHECK[Reviewer eligibility; fact-set version; no blocking open tasks;
approved draft facts; verification attempt]
    CHECK --> DONE[Publish facts and project; recompute metrics]
```

Search/lookup results do not become accepted locations automatically. Content mutations have request IDs and fingerprint checks; this is distinct from upload-completion idempotency. Builder location proposals always require a different approver. Other location references follow the configured policy. Registry evidence alone does not establish project-location identity.

References: [builder locations](../reailEstate/reailEstate_common/backend/platform_api/builder_locations.py), [content actions](../reailEstate/reailEstate_common/backend/platform_api/project_workspace_content.py), [external-source review](../reailEstate/reailEstate_common/backend/platform_api/external_sources_admin.py), [publication](../reailEstate/reailEstate_common/backend/platform_api/views.py).

## 4. Document upload — current, shared by builder and admin

```mermaid
sequenceDiagram
    actor User as Builder or authorized admin
    participant UI as Shared project frontend
    participant API as Workspace API handlers
    participant DB as Database
    participant FS as Artifact storage
    User->>UI: Select document
    UI->>UI: Generate upload UUID; calculate SHA-256
    UI->>API: POST documents:upload-intent
    Note over API: UploadIntentView.post: permissions, active project,<br/>filename, MIME, size, digest and duplicate checks
    API->>DB: EvidenceDocument.get_or_create(project, sha256)
    API-->>UI: Document ID and workspace-local uploadAction
    UI->>API: PUT documents/{id}/content
    Note over API: DocumentContentUploadView.put:<br/>verify bytes, digest and signature
    API->>FS: storage.save
    API->>DB: status = uploaded
    API-->>UI: 204
    UI->>API: POST documents/{id}:complete + Idempotency-Key
    API->>DB: Lock document; replay check; quarantine;<br/>_queue_document_processing + response record
    API-->>UI: 202 + Operation reference
    Note over DB: Workers consume the durable event independently
```

Builder prefix: `/api/v1`. Admin prefix: `/api/v1/admin/project-workspace`. The handlers and parser are shared; authorization and frontend route prefix differ. This replaces the earlier statement that admin lacked upload endpoints.

References: [workspace route adapter](../reailEstate/reailEstate_common/backend/platform_api/urls_project_workspace.py), [upload handlers](../reailEstate/reailEstate_common/backend/platform_api/views.py), [shared frontend](../reailEstate/reailEstate_common/frontend/src/BuilderPortal.tsx).

## 5. Upload-completion idempotency — current

```mermaid
flowchart TD
    UI[ProjectDocuments.select: crypto.randomUUID] --> POST[ProjectDocuments.send: POST complete with same key on retry]
    POST --> LOCK[DocumentCompleteView.post: transaction and document row lock]
    LOCK --> AUTH[Check resource access and active project]
    AUTH --> LOOKUP[_idempotent_response: user + key truncated to 128 + document-complete scope]
    LOOKUP --> HIT{Saved response?}
    HIT -->|Yes| REPLAY[Return original response and operation; no new event]
    HIT -->|No| CHECK[Require uploaded state and stored source]
    CHECK --> QUEUE[_queue_document_processing: ExtractionJob, OutboxMessage, Operation]
    QUEUE --> SAVE[_save_idempotency: store status and response body]
    SAVE --> COMMIT[Commit and return 202]
```

Only completion uses this header in the upload sequence. Intent uses project-plus-hash deduplication; PUT uses state and byte-integrity validation. The frontend key is in upload state, not durable across refresh. Upload completion does not compare payload fingerprints. A new intentional parse uses the separate reprocess action and scope.

References: [frontend key and requests](../reailEstate/reailEstate_common/frontend/src/BuilderPortal.tsx), [idempotency helpers](../reailEstate/reailEstate_common/backend/platform_api/views.py), [unique record constraint](../reailEstate/reailEstate_common/backend/platform_api/models.py).

## 6. Shared whole-document parser — current

```mermaid
flowchart TD
    IN[Upload complete / authorized reprocess / admin reanalysis] --> ENQ[enqueue_document: document.processing.requested]
    ENQ --> WORK[process_outbox_message -> _dispatch_outbox_message]
    WORK --> P[process_document: document work lock and progress]
    P --> MAIN[_process_document]
    MAIN --> VALID[Stored-source integrity; detect_media_type; scan_builder_document]
    VALID --> PREP[prepare_document]
    PREP --> TEXT[DocumentTextExtractor.extract]
    TEXT --> ING[ingest_document: chunk_pages + sparse_embedding; save KnowledgeChunks]
    ING --> KNOW[extract_project_knowledge]
    KNOW --> FACT[RagFactExtractor.extract -> retrieve_field_context -> retrieve]
    FACT --> PF[_persist_project_facts: candidates, conflicts and review tasks]
    PF --> AGENT[FloorPlanAgent.run: detailed in chart 7]
    AGENT --> IMAGES[collect_document_images]
    IMAGES --> VIS[visual_project_claims]
    VIS --> OUTPUT[build_project_output -> store_outputs]
    OUTPUT --> MOVE[move_document to completed when visual work is not pending]
    MOVE --> FIN[_finish_document: materialize_project_amenities; update run, job and operation]
```

Builder/admin admission converges here. The chart shows the successful orchestration; failures update processing state and may be retried or held for provider availability. Local trusted-builder mode can skip a real malware scan with an audit record. Operation completion is not publication and can precede completion of separately queued child layout jobs.

References: [workers](../reailEstate/reailEstate_common/backend/platform_api/workers.py), [text/indexing](../reailEstate/reailEstate_common/backend/platform_api/rag.py), [facts](../reailEstate/reailEstate_common/backend/platform_api/business_ingestion.py), [scan policy](../reailEstate/reailEstate_common/backend/platform_api/builder_scan_policy.py).

## 7. PDF to saved floor-plan or layout image — current

```mermaid
flowchart TD
    RUN[FloorPlanAgent.run] --> STEP[_step: persist stage status and results]
    STEP --> DET[_detect]
    DET --> MARK{Explicit DocumentLayoutPage decisions?}
    MARK -->|Yes| SELECT[Use explicitly selected layout pages]
    MARK -->|No| AUTO[FloorPlanPageSelector.select: score text and graphics]
    SELECT --> CROP[_crop_and_store]
    AUTO --> CROP
    CROP --> EX[FloorPlanImageExtractor.extract_and_store]
    EX --> PDF[_from_pdf -> _render_pdf_crop: selected full-page PNGs]
    PDF --> ASSET[Save file + FloorPlanAsset + source provenance]
    ASSET --> INTERP[_extract]
    INTERP --> RULES[_identify_without_ai: conservative page evidence]
    INTERP --> AI[provider.analyze_classified_plan / analyze_layout]
    RULES --> CHECK[_validate -> _reconcile -> _persist -> _review_gate]
    AI --> CHECK
    CHECK --> FLAT[Individual-layout page: no whole-floor split needed]
    CHECK --> FLOOR[Whole-floor plan: optional segmentation, chart 8]
    MANUAL[Shared media tools: select source page / draw crop] --> DRAFT[stage source pages or crops in cart]
    DRAFT --> ATTACH[Attach drafts: save selected assets and optionally queue evaluation]
    ATTACH --> ASSET
```

Script identification is not script unit segmentation. A single-flat page can proceed directly to layout analysis. Manual cart attachment is an explicit operation, not a side effect of previewing or saving a draft.

References: [agent](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_agent.py), [page selector](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_pages.py), [image extractor](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_extractor.py), [crop cart](../reailEstate/reailEstate_common/backend/platform_api/crop_cart.py).

## 8. Whole-floor image to individual layouts — current

```mermaid
flowchart TD
    SOURCE[Saved whole-floor source] --> AUTO[_queue_automatic_layouts: feature admission]
    AUTO --> QUEUE[queue_floor_plan_layouts: units event]
    QUEUE --> WORK[evaluate_floor_plan_layouts]
    WORK --> AI[provider.analyze_full_floor -> validate_regions]
    SOURCE --> ADHOC[separate_floor_plan_units: user-triggered]
    ADHOC --> SCRIPT[script: segment_units_script]
    ADHOC --> SKILL[ai: FloorPlanFlatSeparationAISkill.invoke]
    ADHOC --> BOTH[both: run script and AI independently]
    SKILL --> AI
    BOTH --> SCRIPT
    BOTH --> AI
    SOURCE --> MANUAL[ManualFloorPlanCropsAdminView / manual crop tools]
    MANUAL --> REGIONS[User-defined regions; staged or direct authorized path]
    SCRIPT --> SAVE[replace_unit_segments: crop_region; save PNGs and child plans]
    AI --> SAVE
    REGIONS --> MSAVE[Manual crop persistence with provenance]
    SAVE --> EVAL[queue_layout_evaluation when selected/configured]
    MSAVE --> EVAL
    EVAL --> CROP[evaluate_layout_crop -> provider.analyze_layout]
    CROP --> VALIDATE[_vision_candidates -> _validate -> _reconcile]
    VALIDATE --> RESULT[Save configuration, dimensions, areas and review state]
```

Automatic jobs and ad hoc separation have distinct orchestration. Both mode retains separate method results; it is not consensus merging. Segmentation finds boundaries; evaluation interprets the crop. Source images remain retained, and optional superseding is a separate guarded choice.

References: [automatic jobs](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_layout_jobs.py), [ad hoc modes](../reailEstate/reailEstate_common/backend/platform_api/floor_plan_segmentation_service.py), [unit persistence](../reailEstate/reailEstate_common/backend/platform_api/unit_segmentation.py), [evaluation](../reailEstate/reailEstate_common/backend/platform_api/layout_reanalysis.py), [builder crop path](../reailEstate/reailEstate_common/backend/platform_api/builder_media.py).

## 9. Media files and parent relationships — current

```mermaid
flowchart LR
    ORIGINAL[Original source document: user/media/todo] --> COMPLETE[Verified source relocation: user/media/completed]
    ORIGINAL --> PAGE[New rendered page PNG: output/media/images]
    PAGE --> CHILD[New flat crop PNG: output/media/images/.../layouts]
    CHILD --> ASSET[FloorPlanAsset: checksum, size, dimensions and bounds]
    ASSET --> PLAN[FloorPlan: interpreted data and asset reference]
    CHILD -. sourceAssetId and sourcePlanId .-> PAGE
    PAGE -. document and page reference .-> ORIGINAL
    PLAN --> API[Authorized media endpoints]
    OUTPUT[Project JSON and layout metrics JSON/CSV] --> DOWNLOAD[Authorized output-download endpoints]
    COVER[Project cover picture] --> DEFAULT[Django default storage path]
```

`RoutedStorage` routes `user/media/` and `output/media/` keys under `WORKSPACE_DATA_ROOT`; other keys use Django default storage. Cover pictures also use default storage, so describing that storage as only historical is incomplete. New files plus provenance are the current design; rendering a crop directly from original PDF coordinates remains a proposed quality improvement.

References: [storage routing](../reailEstate/reailEstate_common/backend/platform_api/artifact_storage.py), [asset records](../reailEstate/reailEstate_common/backend/platform_api/models.py), [project output files](../reailEstate/reailEstate_common/backend/platform_api/project_outputs.py), [media routes](../reailEstate/reailEstate_common/backend/platform_api/urls_media.py).

## 10. Queues, workers and failures — current

```mermaid
flowchart TD
    API[API or admin action] --> TX[Commit business state + OutboxMessage + Operation where applicable]
    TX --> PENDING[Database queue]
    PENDING --> SCHED[run_automatic_queue: singleton scheduler; polling]
    SCHED --> RES[reserve_ready_jobs: capacity, due time, lifecycle and cancellation]
    RES --> POOL[ThreadPoolExecutor -> run_reserved_job]
    POOL --> PROCESS[process_outbox_message: message lock; completion/inbox checks]
    PROCESS --> DISPATCH[_dispatch_outbox_message]
    DISPATCH --> DOC[Document handler]
    DISPATCH --> MEDIA[Page / units / crop handlers]
    DISPATCH --> OTHER[Recommendation / enrichment handlers]
    DOC --> DONE[Persist results; InboxMessage; mark message processed]
    MEDIA --> DONE
    OTHER --> DONE
    DISPATCH --> ERR[_record_outbox_failure]
    ERR --> RETRY[Retryable and budget remains: available_after backoff]
    RETRY --> PENDING
    ERR --> DEAD[Terminal or exhausted: dead-letter and operation failure]
    DEAD --> ADMIN[Authorized explicit requeue/replay]
    ADMIN --> PENDING
    DOC --> CHILD[Child OutboxMessages]
    CHILD --> PENDING
```

Worker heartbeats and progress are tracked separately; model calls have a shared concurrency budget. Default worker capacity is eight, capped at sixteen. Normal failure budget is three attempts; busy document locks defer without consuming an attempt. Durable rows and locks do not guarantee exactly-once external side effects. `published_at` currently acts as processing completion, not a separate broker-publish acknowledgement.

References: [automatic queue](../reailEstate/reailEstate_common/backend/platform_api/automatic_queue.py), [processing/retries](../reailEstate/reailEstate_common/backend/platform_api/workers.py), [locks/model slots](../reailEstate/reailEstate_common/backend/platform_api/ingestion_runtime.py), [monitoring](../reailEstate/reailEstate_common/backend/platform_api/queue_monitor.py).

## 11. Retrieval and OCR — current versus proposed

```mermaid
flowchart TD
    PDF[Current source] --> TEXT[DocumentTextExtractor.extract: native text]
    TEXT --> CHUNK[chunk_pages -> sparse_embedding -> KnowledgeChunk]
    CHUNK --> RET[retrieve_field_context -> retrieve: cosine ranking]
    RET --> FACT[RagFactExtractor: regex and amenity matching]
    FACT --> REVIEW[Fact candidates and review]
    FUTURE[Proposed layout text pipeline] -.-> NATIVE[Try positioned PDF text within layout bounds]
    NATIVE -. insufficient or image-only .-> OCR[Existing OCR engine on a sharp source render]
    NATIVE -.-> NORMAL[Normalize labels, units and dimensions]
    OCR -.-> NORMAL
    NORMAL -.-> ASSOC[Associate text with rooms using spatial context]
    ASSOC -.-> FALLBACK[Validate; uncertain cases to vision or human review]
    CHUNK -. proposed upgrade .-> HYBRID[Semantic + keyword retrieval]
    HYBRID -.-> MODEL[Schema-bound evidence-grounded model extraction]
    MODEL -.-> REVIEW
```

Solid arrows show the existing text-fact path. Dotted arrows show proposals, not implemented OCR or generative RAG. The current floor-plan vision path remains in chart 7/8. RAG can retrieve relevant evidence; it does not itself render pixels or reliably establish unit boundaries.

References: [current retrieval](../reailEstate/reailEstate_common/backend/platform_api/rag.py), [current fact extractor](../reailEstate/reailEstate_common/backend/platform_api/business_ingestion.py), [proposals](../todo.md).

## 12. Broker migration — proposed only

```mermaid
flowchart LR
    API[API transaction] --> DB[Business data + transactional outbox]
    DB --> RELAY[New outbox relay]
    RELAY --> BROKER[Kafka or Azure Service Bus]
    BROKER --> CONSUMER[Independent consumer processes]
    CONSUMER --> CHECK[Inbox/deduplication, current-state and source-version checks]
    CHECK --> HANDLER[Existing domain processing handlers]
    HANDLER --> RESULTS[Persist results and Operation state]
    RESULTS --> ACK[Broker acknowledgement / offset commit]
    HANDLER --> CHILD[Child events in transactional outbox]
    CHILD --> RELAY
    STORAGE[Shared/object media storage] --> HANDLER
```

Keep stable event IDs, per-document coordination and application idempotency. Separate relay dispatch status from consumer completion. Coordinate retry ownership, and renew Service Bus locks or manage Kafka polling/offsets for long jobs. Do not send PDF/image bytes in messages. This diagram is a proposed migration, not the current deployment.

## Corrections to earlier diagrams

- Admin project creation now goes through the shared workspace and shared creation routine.
- Admin document uploads now use the shared three-step upload handlers under an admin prefix.
- Admin does not need builder membership for its authorized workspace.
- Shared content APIs now support source and amenity claim submission; review remains independent.
- Manual source-page/crop tools stage drafts before explicit attachment in the shared cart flow.
- Script page identification and script unit segmentation are distinct operations.
- Stored derived images retain provenance; original-PDF high-resolution crop regeneration is still proposed.
- Current text retrieval is not generative RAG, and dedicated OCR is proposed.
- Current queue is database-backed; the broker diagram remains future work.
