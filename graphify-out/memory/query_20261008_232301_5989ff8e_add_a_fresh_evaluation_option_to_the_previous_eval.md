---
type: "query"
date: "2026-10-08T23:23:01.851155+00:00"
question: "Add a fresh evaluation option to the previous evaluation failed warning"
contributor: "graphify"
outcome: "useful"
source_nodes: ["evaluation_retry_warning", "queue_layout_evaluation", "queue_floor_plan_layouts", "queue_single_layout"]
---

# Q: Add a fresh evaluation option to the previous evaluation failed warning

## Answer

Added a separately signed fresh choice to the AJAX dialog and no-JS form. Crop and full-floor queues create new current-AI-profile attempts after terminal failure/completion, retaining previous jobs and results and reusing active/backoff/cancelled work. Project action queues only latest failed current-asset layouts/floors, including orphan Operations after DLQ deletion. Tests cover confirmation binding/expiry, scoping, profile/source admission, history and deduplication; mocked browser verifies both choices, unsupported-option hiding and recovery. No live AI evaluations or backfill performed; services reloaded only after idle checks, with logs archived.

## Outcome

- Signal: useful

## Source Nodes

- evaluation_retry_warning
- queue_layout_evaluation
- queue_floor_plan_layouts
- queue_single_layout