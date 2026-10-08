---
type: "query"
date: "2026-10-08T11:28:15.385505+00:00"
question: "Why do selected configurations not restrict recommendation measurements, and why do catalogue filters still use HomeConfiguration?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["ProjectViewSet", "HomeConfiguration", "ScoringConfiguration"]
---

# Q: Why do selected configurations not restrict recommendation measurements, and why do catalogue filters still use HomeConfiguration?

## Answer

Expanded from graph vocabulary via configuration home filter layout scoring snapshot validation upload. The graph located ProjectViewSet, HomeConfiguration, ScoringConfiguration and services.py. Source verification shows a partial migration: cards and scoring derive configurations from approved layouts, but catalogue configuration and carpet area filters still join HomeConfiguration. Recommendation hard checks independently inspect all configurations and the space score uses their maximum area, rather than restricting measurements to selected home types. These two issues remain outside the explicitly authorized fixes for snapshots, validation and upload formats.

## Outcome

- Signal: useful

## Source Nodes

- ProjectViewSet
- HomeConfiguration
- ScoringConfiguration