---
type: "query"
date: "2026-10-08T23:39:58.940307+00:00"
question: "the extractions are not upto par, lets switch back to azure openAI instance"
contributor: "graphify"
outcome: "useful"
source_nodes: ["OpenAIFloorPlanVisionProvider", "_configured_profiles", "resolve_ai_vision_selection"]
---

# Q: the extractions are not upto par, lets switch back to azure openAI instance

## Answer

Expanded graph vocabulary: azure vision profile default runtime selection provider. Active ignored local profile contains only Ollama Gemma 3. Historical sample report records azure-samples provider azure_foundry model gpt-4.1; example registry records hjetwani-vision-wus2 Azure OpenAI endpoint. Reconstructed profile did not match historical configuration hash, so exact prior registry is not verified. DefaultAzureCredential authentication probe failed: no configured environment credentials, no cached account, Azure CLI not on PATH, no Az.Accounts or azd available. Kept active configuration and jobs unchanged; need authenticated Azure access or a locally configured API key and confirmed endpoint/deployment before activation. Workers were idle; no inference requests or service reloads were performed.

## Outcome

- Signal: useful

## Source Nodes

- OpenAIFloorPlanVisionProvider
- _configured_profiles
- resolve_ai_vision_selection