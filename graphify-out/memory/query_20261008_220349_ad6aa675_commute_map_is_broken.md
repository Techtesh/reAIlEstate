---
type: "query"
date: "2026-10-08T22:03:49.722084+00:00"
question: "Commute map is broken"
contributor: "graphify"
outcome: "useful"
source_nodes: ["ProjectMap()"]
---

# Q: Commute map is broken

## Answer

Expanded via graph vocabulary: commute map leaflet layer browse. ProjectMap renders transit overlays independently of its tile layer. On the live buyer page all sampled Esri World_Imagery images had naturalWidth zero and a direct tile request timed out; switching to Commute loaded 24 OpenStreetMap tiles. Added an explicit one-way street-map fallback on satellite tile errors with provider-correct attribution, a fallback notice and manual imagery retry. No data, providers, dependencies or CSP permissions changed.

## Outcome

- Signal: useful

## Source Nodes

- ProjectMap()