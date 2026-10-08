---
type: "query"
date: "2026-10-08T17:23:02.354260+00:00"
question: "why are ai jobs still failing"
contributor: "graphify"
outcome: "useful"
source_nodes: ["_profile_for_selection()", "OpenAIFloorPlanVisionProvider"]
---

# Q: why are ai jobs still failing

## Answer

Expanded graph vocabulary: worker vision queue failure. Runtime read-only diagnosis: crop evaluation jobs pin ollama-gemma3 gemma3:4b configurationHash 99f25c1bc833fdc43185770b088973722e12ee450f675086369225471ea82da7 matching json_object, whereas current worker runtime config hashes to 93abc4a460235a50d1501f86048bc8460872f05f5a0f7bdd755d259f8a44c860 with json_schema. _profile_for_selection rejects the stale pinned selection before model calls with ProviderUnavailable. Ollama reachable with gemma3:4b installed. Earlier InvalidProviderResponse is a separate recorded failure; model outputs not replayed.

## Outcome

- Signal: useful

## Source Nodes

- _profile_for_selection()
- OpenAIFloorPlanVisionProvider