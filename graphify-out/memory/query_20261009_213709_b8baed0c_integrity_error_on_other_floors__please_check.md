---
type: "query"
date: "2026-10-09T21:37:09.059561+00:00"
question: "Integrity error on other floors, please check"
contributor: "graphify"
outcome: "useful"
source_nodes: ["FloorPlan", "replace_unit_segments"]
---

# Q: Integrity error on other floors, please check

## Answer

Expanded tokens: floor layouts segmentation integrity constraint persist. PostgreSQL logs confirm unique_floor_plan_source collisions for brochure pages 3, 4 and 5 in Adityaraj sahayadri. Qwen repeats the label flat for multiple physical regions. replace_unit_segments constructs plan_name from region.label plus the same source ID and run suffix for every region; it does not include the region index. Distinct regions therefore share document/page/name within a run. Preserve the DB constraint and disambiguate generated storage names per region; do not infer printed flat numbers from the internal index. Diagnosis only; no product code changes or retries authorized in this turn.

## Outcome

- Signal: useful

## Source Nodes

- FloorPlan
- replace_unit_segments