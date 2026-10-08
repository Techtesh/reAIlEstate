---
type: "query"
date: "2026-10-08T17:29:16.374350+00:00"
question: "please check why its failing"
contributor: "graphify"
outcome: "useful"
source_nodes: ["OpenAIFloorPlanVisionProvider", "evaluate_layout_crop()"]
---

# Q: please check why its failing

## Answer

Expanded graph vocabulary: validate response schema crop evaluation. Isolated read-only local diagnostic replay of current-profile job 1e1cbe42-3819-4f0d-96c1-ae8beeafbf83 using checksum-verified same image: one choice finish_reason stop, 626 completion tokens, 1932 content chars, valid JSON, zero schema errors; result pageType non_plan with four floorPlans and visualEvidence. analyze_classified_plan rejects with InvalidProviderResponse: Non-plan classification included plan records. Visual inspection of asset confirms a whole page of four 3D apartment renders despite single-layout job A Wing Unit 01, whose existing qualityWarning says did not find exactly one dwelling. Root issues are contradictory model classification and incorrect single-layout input. Failure storage retains generic exception type only. No job, layout, configuration, or production code changed; isolated inference result not saved. Original model response not retained, so replay demonstrates reproducible path, not recovered original output.

## Outcome

- Signal: useful

## Source Nodes

- OpenAIFloorPlanVisionProvider
- evaluate_layout_crop()