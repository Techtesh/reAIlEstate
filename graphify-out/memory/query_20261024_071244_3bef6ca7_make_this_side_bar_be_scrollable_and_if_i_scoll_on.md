---
type: "query"
date: "2026-10-24T07:12:44.046427+00:00"
question: "make this side bar be scrollable and if i scoll on other part of the page this should not be affected and when i scroll on this component rest of the page shouldnt be affected"
contributor: "graphify"
outcome: "useful"
source_nodes: ["BuilderPortal()", "BuilderPortal.tsx"]
---

# Q: make this side bar be scrollable and if i scoll on other part of the page this should not be affected and when i scroll on this component rest of the page shouldnt be affected

## Answer

Expanded from original query via graph vocabulary: [sidebar, scroll, scrollbar, overflow, panel, project, projects, navigation, workspace]. BuilderPortal.tsx renders the organization project sidebar and BuilderPortal.css owns its layout. Added a viewport-bounded sticky desktop scroller with overscroll containment and stable scrollbar spacing; the single-column layout resets stickiness while retaining an independently scrollable 70vh panel.

## Outcome

- Signal: useful

## Source Nodes

- BuilderPortal()
- BuilderPortal.tsx