---
type: "query"
date: "2026-10-08T17:33:05.184743+00:00"
question: "check for A Wing Unit 03"
contributor: "graphify"
outcome: "useful"
source_nodes: ["OpenAIFloorPlanVisionProvider", "evaluate_layout_crop()", "FloorPlanAgent"]
---

# Q: check for A Wing Unit 03

## Answer

Expanded vocabulary: crop evaluation validate response. Checked Empire manual Unit03 plan 3c78428a-56d4-42e5-a0a3-11cc8fd36570, job017bce5a-c153-40b4-8270-cd55079f0ead. Actual saved job fails PROVIDER_UNAVAILABLE before inference because original json_object profile hash99f25 differs current json_schema93abc. Visual inspection confirms manual asset is one 2D apartment, unlike separate legacy AI Unit03 using full page of4renders. Isolated diagnostic with current profile, no DB writes: finish stop, valid JSON, schema errors none, pageType floor_plan with1 floorPlans. FloorPlanAgent._vision_candidates collapses floor_plan results to floor-scoped candidate, leaving0flat candidates and causing MANUAL_CROP_NOT_SINGLE_LAYOUT. Need correct flat_layout vs floor_plan classification semantics for single-unit crop; not a proved multi-unit boundary issue. No code, jobs, or images changed.

## Outcome

- Signal: useful

## Source Nodes

- OpenAIFloorPlanVisionProvider
- evaluate_layout_crop()
- FloorPlanAgent