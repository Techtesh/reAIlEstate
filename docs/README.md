# Project Documentation Index

This documentation set turns the initial Mumbai new-construction real-estate idea into an implementation-ready product and engineering baseline. It uses the supplied Jupyter HTML-serving document specifically for its frontend security lessons: keep the main application CSP strict, do not expose signed storage URLs, and isolate any future interactive untrusted document behind an authenticated `iframe src` endpoint rather than `srcDoc`.

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
- The recommended implementation is React + TypeScript and ASP.NET Core, but stable contracts and boundaries matter more than framework choice.
- MVP can be a modular monolith plus asynchronous workers.
- MahaRERA and transaction data are accessed only through approved/licensed mechanisms.
- Carpet area is the canonical area comparison.
- Project claims do not publish automatically when confidence is low or evidence conflicts.
- The product does not book homes, certify title, underwrite loans, or guarantee prices/returns.

## Decisions required before implementation

1. Confirm data licenses and technical access for MahaRERA, maps/routing, and IndexTap/Index2.
2. Select identity/session architecture and organization verification process.
3. Approve privacy classification, consent, retention/deletion, and data residency.
4. Approve supported document formats/limits and extraction provider.
5. Finalize fact taxonomy, micro-market methodology, hard constraints, and scoring weights.
6. Confirm whether the published catalog is public or authentication-gated.
7. Set measurable extraction quality and launch thresholds.

## Scope control

Changes to authentication, document rendering, outbound URL fetching, provider origins, CSP/sandbox permissions, project publication rules, or personal-data use are security/privacy architecture changes and require explicit review. Do not weaken controls merely to make generated or third-party content render.
