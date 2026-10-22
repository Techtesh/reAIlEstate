---
type: "query"
date: "2026-10-22T08:02:37.851561+00:00"
question: "tell me how the login flow works for all 3 portals"
contributor: "graphify"
outcome: "useful"
source_nodes: ["SessionLoginView", "BuilderLogin"]
---

# Q: tell me how the login flow works for all 3 portals

## Answer

Graph vocabulary: SessionLoginView, BuilderLogin. Buyer GET auth/login initializes CSRF, POST authenticates any active Django account and creates a session; auth/me restores it. Builder uses auth/builder-login with an extra active owner/admin/editor organization membership check, then loads organizations and checks scope per API request. Admin uses standard Django admin login requiring active staff and per-model/action permissions. All share Django users and default cookie/session configuration; 1800-second sliding session expiry. Same-host local ports can share the session because cookies are not port-scoped. No public signup in these portal flows. Verified against current views, builder_access, service routes, frontend and locally installed Django admin.

## Outcome

- Signal: useful

## Source Nodes

- SessionLoginView
- BuilderLogin