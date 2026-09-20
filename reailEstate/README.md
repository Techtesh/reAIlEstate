# Real Estate Services

This repository uses a perforated-monolith layout: three independently runnable
service entrypoints share one Django domain, one React source tree, one PostgreSQL
schema, and one media store.

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

Build the buyer and builder bundles:

```powershell
.\build_service_frontends.ps1
```

Start a service in a separate terminal:

```powershell
.\run_service.ps1 -Service admin
.\run_service.ps1 -Service builder
.\run_service.ps1 -Service buyer
```

Start the shared background worker in one additional terminal. Document ingestion,
page analysis, crop evaluation, and floor splitting remain queued until this process
claims them:

```powershell
.\run_worker.ps1
```

For local AI extraction, copy
`reailEstate_common/backend/vision-profiles.example.json` to the ignored
`reailEstate_common/backend/vision-profiles.local.json` and configure one enabled
profile. The service launcher loads that file automatically. When the file contains
one profile, it becomes the default and AI extraction is enabled. For multiple
profiles, set `FLOOR_PLAN_DEFAULT_VISION_PROFILE` before starting the service. Use
`-VisionProfilesFile <path>` to load a different local profile file.

The default ports are 8002, 8003, and 8004. The Compose layout includes the shared
background worker. For the single-host container layout, set `POSTGRES_PASSWORD` and
`DJANGO_SECRET_KEY`, then use
`docker compose -f compose.services.yml up --build`.

Cloud-specific `.deployments` infrastructure is intentionally deferred until an
Azure subscription/region and AWS account/region are selected.
