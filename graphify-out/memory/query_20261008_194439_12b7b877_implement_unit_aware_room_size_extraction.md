---
type: "query"
date: "2026-10-08T19:44:39.526619+00:00"
question: "Implement unit-aware room-size extraction"
contributor: "graphify"
outcome: "useful"
source_nodes: ["OpenAIFloorPlanVisionProvider", "FloorPlanAgent", "evaluate_layout_crop()"]
---

# Q: Implement unit-aware room-size extraction

## Answer

Expanded via graph vocabulary: room dimensions meters metric parse normalize layout vision source. Native measurement pairs are normalized in realestate_common/room_dimensions.py, metadata is carried by FloorPlanCandidate through both persistence paths, and dimensionWarning is separate from boundary qualityWarning. Existing saved layouts are not backfilled.

## Outcome

- Signal: useful

## Source Nodes

- OpenAIFloorPlanVisionProvider
- FloorPlanAgent
- evaluate_layout_crop()