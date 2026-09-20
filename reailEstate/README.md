# Real Estate Services

This repository uses a perforated-monolith layout: three independently runnable
service entrypoints share one Django domain, one React source tree, one PostgreSQL
schema, and one media store.

The canonical operating and product-flow catalogue is
[`../WORKFLOWS.md`](../WORKFLOWS.md). Use it for setup, start/stop/status,
role workflows, workers, maintenance, testing, and deployment safety.

| Project | Responsibility |
|---------|----------------|
| `adminService` | Administration, review, publishing, operations, and schema migrations |
| `builderService` | Builder authentication, project ingestion, layouts, and media editing |
| `buyerService` | Published catalogue, buyer profiles, recommendations, and the public UI |
| `reailEstate_common` | Shared Django domain, migrations, processing library, and React source |

The `platform_api` Django app remains in
`reailEstate_common/backend/platform_api`. Its app label, database table names,
and migration history are intentionally unchanged.

## Local services

From the repository root, use the lifecycle controller:

```powershell
.\reailEstate\manage_services.ps1 -Action start
.\reailEstate\manage_services.ps1 -Action status
.\reailEstate\manage_services.ps1 -Action stop
```

Startup builds both frontend bundles, applies pending migrations through the admin
service, and starts admin, builder, buyer, and the shared worker. The resulting
interfaces are:

- Admin: `http://127.0.0.1:8002/admin/`
- Builder: `http://127.0.0.1:8003/`
- Buyer: `http://127.0.0.1:8004/`

For foreground debugging, use `run_service.ps1` and `run_worker.ps1` directly.
For buyer hot reload, start only the buyer API and use
`npm --prefix .\reailEstate\reailEstate_common\frontend run dev:buyer`.

For local AI extraction, copy
`reailEstate_common/backend/vision-profiles.example.json` to the ignored
`reailEstate_common/backend/vision-profiles.local.json` and configure one enabled
profile. The service launcher loads that file automatically. When the file contains
one profile, it becomes the default and AI extraction is enabled. For multiple
profiles, set `FLOOR_PLAN_DEFAULT_VISION_PROFILE` before starting the service. Use
`-VisionProfilesFile <path>` to load a different local profile file.

The Compose layout includes PostgreSQL, migrations, all web services, and the shared
worker. See [`../WORKFLOWS.md`](../WORKFLOWS.md) before using it.

Azure has validated planning artifacts but no authorized apply. AWS remains
offline-validated without an approved account deployment. Neither cloud may be
deployed without new approval.
