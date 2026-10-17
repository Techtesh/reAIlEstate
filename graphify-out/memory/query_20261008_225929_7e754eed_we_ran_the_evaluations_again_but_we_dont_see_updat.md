---
type: "query"
date: "2026-10-08T22:59:29.564627+00:00"
question: "We ran the evaluations again but we dont see updated data"
contributor: "graphify"
outcome: "useful"
source_nodes: ["evaluate_layout_crop()", "queue_layout_evaluation()", "evaluate_floor_plan_layouts()"]
---

# Q: We ran the evaluations again but we dont see updated data

## Answer

Expanded via graph vocabulary: layout evaluation extraction persist status. Read-only DB/log audit: most recently changed operation 3c342db3 is Adityaraj Avenue full-floor page 4, kind floor_plan_units_evaluation, failed InvalidProviderResponse after three worker runs. Source features/layout crops commit only after analyze_full_floor passes validation, so no fresh room-size jobs or data were saved. That outbox record was deleted during inspection, but failed Operation remains. Wing B Unit 04 stored dimensions/updated_at remain the earlier metric-as-feet extraction; its crop job is completed and unchanged. Admin Queue extraction invokes retry only, not force, and queue_layout_evaluation reuses completed matching jobs; builder evaluate and batch force can create fresh attempts for completed jobs. Current logs/operation retain generic InvalidProviderResponse, not exact response-validation reason. No model inference, reruns or application changes performed.

## Outcome

- Signal: useful

## Source Nodes

- evaluate_layout_crop()
- queue_layout_evaluation()
- evaluate_floor_plan_layouts()