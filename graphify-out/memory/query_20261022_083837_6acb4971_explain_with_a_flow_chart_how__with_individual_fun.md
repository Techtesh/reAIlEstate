---
type: "query"
date: "2026-10-22T08:38:37.860309+00:00"
question: "explain with a flow chart how (with individual function calls) how a builder/admin can (1)add a project (2)upload a document (3)how a document is parsed into component floor plans and layouts (3b) can we replace parts of this flow with a rag based flow (3c) how are media files handelled"
contributor: "graphify"
outcome: "useful"
source_nodes: ["DeveloperProjectCreateView", "UploadIntentView", "DocumentCompleteView", "FloorPlanAgent", "RagFactExtractor", "RoutedStorage"]
---

# Q: explain with a flow chart how (with individual function calls) how a builder/admin can (1)add a project (2)upload a document (3)how a document is parsed into component floor plans and layouts (3b) can we replace parts of this flow with a rag based flow (3c) how are media files handelled

## Answer

Builder ProjectEditor.save -> BuilderProjectsView.post -> DeveloperProjectCreateView.create_project -> serializer.save and VerificationSnapshot. Admin ProjectAdmin uses inherited Django model-form save, not builder create_project. Admin API route set lacks UploadIntentView, DocumentContentUploadView, DocumentCompleteView; builder has them. Upload ProjectDocuments.send -> SHA256 -> intent POST -> content PUT verifies size/hash/signature -> complete POST with idempotency -> quarantine, ExtractionJob, Operation, enqueue_document OutboxMessage. automatic_queue run_reserved_job -> process_outbox_message -> _dispatch_outbox_message -> process_document -> _process_document validates, scan_builder_document, prepare_document, DocumentTextExtractor.extract, ingest_document, extract_project_knowledge. Existing text retrieval uses chunk_pages 280 words/40 overlap, hashed 384-dimensional sparse_embedding and cosine retrieve; RagFactExtractor regex/amenity extraction plus candidate review, no generative fact model. FloorPlanAgent.run -> _detect -> _crop_and_store -> _extract -> _validate -> _reconcile -> _persist -> _review_gate. AI configured floor records may fan out via feature-gated _queue_automatic_layouts -> queue_floor_plan_layouts -> evaluate_floor_plan_layouts -> provider.analyze_full_floor -> validate_regions -> replace_unit_segments -> crop_region -> queue_layout_evaluation -> evaluate_layout_crop -> provider.analyze_layout. Script identification retains page-level evidence, not inferred unit segmentation. RoutedStorage stores original user/media/todo or completed and output/media files under WORKSPACE_DATA_ROOT, legacy paths default storage. collect_document_images categories and project/layout JSON CSV artifacts. Media delivered through authorized views, review/publication separate. Proposed RAG enhancement is semantic/hybrid retrieval plus schema-bound evidence extraction and OCR/multimodal page indexing; retain rendering, segmentation, validation, authorization and review.

## Outcome

- Signal: useful

## Source Nodes

- DeveloperProjectCreateView
- UploadIntentView
- DocumentCompleteView
- FloorPlanAgent
- RagFactExtractor
- RoutedStorage