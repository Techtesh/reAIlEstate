# Project implementation guidance

The project is implemented; do not scaffold a second application. Use the
[workflow catalogue](../../WORKFLOWS.md), [service overview](../README.md) and
[maintainer guide](../MAINTAINERS.md) for current commands and ownership.

Admin (8002), Builder (8003) and Buyer (8004) share Django models/migrations and
PostgreSQL. Buyer, Builder and the custom Admin console use one React/Vite source
tree. Use an isolated PostgreSQL database for tests; SQLite fallback is removed.
Retain existing migrations, evidence and independent review gates.

## Project instructions

- Build a Django REST Framework backend and React/Vite TypeScript frontend.
- Follow the product specifications in ../../docs, with carpet area as the canonical area measure.
- Use opaque UUID identifiers, object-level authorization, optimistic concurrency, and RFC 9457-style errors.
- Developers cannot approve or publish their own evidence; reviewer/admin roles publish projects.
- Never expose tokens, signed URLs, exact private locations, budgets, or document contents in logs or browser global state.
- Render user and developer content as text; do not use unsafe HTML rendering.
- Maintain accessible, responsive UI behavior targeting WCAG 2.2 AA.
- Keep backend and frontend independently runnable and tested.
- Admin workspace permissions are distinct from Builder organization membership.
- Release through the reviewed [EC2 Actions workflow](../../.github/workflows/aws-ec2-release.yml); it checks pending migrations, keeps processing off, and defaults demo visibility to true. Do not infer deployment success from local checks.
- Follow the repository deployment rules: keep real endpoints/IDs/secrets out of tracked files and obtain explicit command-specific permission before any agent-run AWS/Azure CLI command.
