---
type: "query"
date: "2026-10-14T13:17:02.707510+00:00"
question: "here i would like another layer of pre check get qwen 3.8 from http://127.0.0.1:11435 a local ai box set specifically for dev"
contributor: "graphify"
outcome: "useful"
source_nodes: ["OpenAIFloorPlanVisionProvider", "get_floor_plan_vision_provider"]
---

# Q: here i would like another layer of pre check get qwen 3.8 from http://127.0.0.1:11435 a local ai box set specifically for dev

## Answer

Expanded from original query via graph vocab: [evaluate, evaluation, provider, vision, layout, check, validation, model]. Added a development-only PrecheckedFloorPlanVisionProvider around the existing OpenAIFloorPlanVisionProvider. The qwen3.8:latest profile at 127.0.0.1:11435 classifies plan geometry first; rejected or uncertain images skip the primary extraction call, accepted images continue to the pinned primary provider. The verdict is cached for the current image.

## Outcome

- Signal: useful

## Source Nodes

- OpenAIFloorPlanVisionProvider
- get_floor_plan_vision_provider