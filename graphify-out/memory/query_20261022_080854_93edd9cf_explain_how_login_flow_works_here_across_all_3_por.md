---
type: "query"
date: "2026-10-22T08:08:54.292261+00:00"
question: "Explain how login flow works here across all 3 portals"
contributor: "graphify"
outcome: "useful"
source_nodes: ["SessionLoginView", "BuilderLoginView", "builder_memberships", "BuilderPortal"]
---

# Q: Explain how login flow works here across all 3 portals

## Answer

Expanded graph vocabulary: login session buyer builder admin csrf logout membership. Buyer uses SessionLoginView GET for CSRF bootstrap then POST username/password; active accounts are accepted. BuilderLoginView inherits it and requires builder_memberships: active user and organization and owner/admin/editor role (or valid same-organization named-role base). Both use Django session login, auth/me restoration and POST logout. Admin is mounted through admin.site.urls by admin_service, using Django staff authentication and model permissions. Shared settings use SessionAuthentication, HttpOnly SameSite Lax session cookies, default 1800-second rolling expiry and secure cookies outside DEBUG. Separate role URLconfs expose different endpoint sets. Frontend handles 401/403 by clearing private state. Common frontend has combined-workspace mode; buyer production build disables builder switch. Verified against source and session/service-boundary tests; tests read, not run.

## Outcome

- Signal: useful

## Source Nodes

- SessionLoginView
- BuilderLoginView
- builder_memberships
- BuilderPortal