# Project Documentation Index

This documentation set describes the current MMR Homefinder implementation and its product/design requirements. The application uses Django REST Framework, PostgreSQL, and React/TypeScript. Requirements and proposed architecture are not proof that a feature is implemented or that a release has passed validation.

## Current implementation and operations

- [Operating workflows](../WORKFLOWS.md): setup, local services, roles, processing, tests and releases.
- [Service overview](../reailEstate/README.md) and [maintainer guide](../reailEstate/MAINTAINERS.md): service ownership, implementation paths and dated verification evidence.
- [Current project flows](current-project-flows.md): upload, extraction, crop, review and queue behavior.
- [Admin UX migration](admin-ux-migration-plan.md): custom React administration, follow-up changes and remaining acceptance checks.
- [Backend reference](../reailEstate/reailEstate_common/backend/README.md) and [frontend reference](../reailEstate/reailEstate_common/frontend/README.md): commands and UI/API behavior.
- [EC2 releases](../.deployments/aws/ec2/README.md) and [SSH authentication](../.deployments/aws/ec2/authentication-guide.md): the supported GitHub Actions release path.
- [Cloud-native review](cloud-native-review.md): proposed production improvements; not the current deployed topology or a live cloud inventory.

## Documents

1. [Product requirements and feature requests](01-product-requirements.md) — personas, scope, P0/P1 features, acceptance criteria, quality attributes, and open decisions.
2. [High-level design](02-high-level-design.md) — architecture, modules, deployment topology, trust boundaries, resilience, and key decisions.
3. [Low-level design](03-low-level-design.md) — code layout, aggregates, frontend state, matching formulas, ingestion pipeline, adapters, errors, and authorization.
4. [User flows](04-user-flows.md) — buyer, developer, reviewer, verification, transactions, privacy, theme, and operational flows.
5. [API contracts](05-api-contracts.md) — REST conventions, endpoints, payload examples, media access, errors, and security headers.
6. [Data model and governance](06-data-model.md) — ER model, tables, provenance, classifications, retention, constraints, and recovery.
7. [Security, privacy, and threat model](07-security-privacy-threat-model.md) — threats, mitigations, upload/SSRF/XSS controls, frontend isolation, and launch gates.
8. [Delivery, testing, and rollout](08-delivery-test-rollout.md) — phases, definition of done, test matrix, SLOs, launch gates, rollback, and runbooks.
9. [Frontend and UX specification](09-frontend-ux-specification.md) — information architecture, wireframes, controls, UI states, accessibility, responsive behavior, and safe content presentation.
10. [MVP backlog and traceability](10-mvp-backlog-and-traceability.md) — vertical slices, prioritized user stories, requirement mapping, blockers, and first sprint.
11. [Document ingestion and project outputs](11-document-ingestion-and-project-outputs.md) — expanded upload formats, todo/completed batches, typed project reports, categorized image URIs, external enrichment contracts, and layout metrics.

## Recommended reading order

- Product/UX: 01 → 04 → 09 → 10
- Engineering: 01 → 02 → 03 → 05 → 06 → 07 → 08
- Security/privacy review: 02 → 06 → 07 → 09

## Current assumptions

- Initial market is Mumbai/MMR and inventory is new or under construction.
- The implementation is React + TypeScript with Django REST Framework and PostgreSQL; the earlier ASP.NET proposal is superseded.
- Admin, Builder and Buyer are separate entrypoints over a shared domain/schema. Local development runs a persistent worker; the EC2 release workflow keeps processing off.
- MahaRERA and transaction data are accessed only through approved/licensed mechanisms.
- Carpet area is the canonical area comparison.
- Project claims do not publish automatically when confidence is low or evidence conflicts.
- The product does not book homes, certify title, underwrite loans, or guarantee prices/returns.

## Remaining product and production decisions

1. Confirm data licenses and technical access for MahaRERA, maps/routing, and IndexTap/Index2.
2. Approve production identity/MFA integration and organization verification policy; current authentication uses Django sessions and permissions.
3. Approve privacy classification, consent, retention/deletion, and data residency.
4. Approve supported document formats/limits and extraction provider.
5. Finalize fact taxonomy, micro-market methodology, hard constraints, and scoring weights.
6. Review public demo exposure: published listings are public, and the EC2 workflow's default `demo=true` also enables eligible unreviewed previews for all viewers.
7. Set measurable extraction quality and launch thresholds.

## Scope control

### Tracked graphify output and local merges

Keep changes in every `graphify-out/` directory in commits alongside the source
updates. Do not ignore or untrack these files. The root [Git attributes](../.gitattributes)
assign them the `graphify-generated` merge driver.

Enable the driver once in each clone, from the repository root:

```sh
git config --local merge.graphify-generated.name "Keep current generated graph; refresh after merge"
git config --local merge.graphify-generated.driver true
```

When both branches edit an existing generated file, this driver keeps the current
branch's version instead of producing a content conflict in the local merge
resolver. One-sided updates still merge normally; other project files keep Git's
normal conflict behavior. This deliberately does not combine graph JSON or reports.
After merging source changes, run `graphify update .`, review the generated changes,
and commit the refreshed graph. Refresh nested graphs from their own scan roots
when they are maintained separately.

Git attributes are tracked, but driver configuration is local and must be repeated
in new clones. GitHub's web merge resolver does not run this custom driver; resolve
the merge locally and push it. Delete/modify, rename and other structural conflicts
can still require an explicit decision. During rebase, Git's current side is the
branch being rebased onto; refresh the graph after the rebase rather than assuming
the retained output describes all replayed source changes.

Changes to authentication, document rendering, outbound URL fetching, provider origins, CSP/sandbox permissions, project publication rules, or personal-data use are security/privacy architecture changes and require explicit review. Do not weaken controls merely to make generated or third-party content render.
