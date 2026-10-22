---
type: "query"
date: "2026-10-14T13:18:21.925138+00:00"
question: "here i would like another layer of pre check get qwen 3.8 from http://127.0.0.1:11435 a local ai box set specifically for dev"
contributor: "graphify"
outcome: "corrected"
correction: "Uncertain pre-check verdicts must continue to the primary model; only definitive rejections skip extraction."
source_nodes: ["OpenAIFloorPlanVisionProvider", "PrecheckedFloorPlanVisionProvider", "get_floor_plan_vision_provider"]
---

# Q: here i would like another layer of pre check get qwen 3.8 from http://127.0.0.1:11435 a local ai box set specifically for dev

## Answer

Expanded from original query via graph vocab: [evaluate, evaluation, provider, vision, layout, check, validation, model]. Added a development-only PrecheckedFloorPlanVisionProvider around OpenAIFloorPlanVisionProvider. qwen3.8:latest at 127.0.0.1:11435 classifies plan geometry first. Only definitive non-plan or incompatible-layout verdicts skip the primary extraction call; uncertain verdicts fail open to the pinned primary model. The verdict is cached for the current image.

## Outcome

- Signal: corrected
- Correction: Uncertain pre-check verdicts must continue to the primary model; only definitive rejections skip extraction.

## Source Nodes

- OpenAIFloorPlanVisionProvider
- PrecheckedFloorPlanVisionProvider
- get_floor_plan_vision_provider