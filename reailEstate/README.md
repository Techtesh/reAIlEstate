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
`reailEstate_common/backend/vision-profiles.json`. Its `profiles` array is the ordered
fallback chain. All backend entrypoints read this file at startup; restart after edits.
Use `-VisionProfilesFile <path>` or `FLOOR_PLAN_VISION_PROFILES_FILE` for another file.
The local launcher enables AI mode when a file exists and extraction mode is unset.

After changing the local vision profile, reload all services and the worker from
the repository root (the explicit path is optional for the default file):

```powershell
.\reailEstate\manage_services.ps1 -Action restart -SkipBuild -SkipMigrations -VisionProfilesFile .\reailEstate\reailEstate_common\backend\vision-profiles.json
```

Automatic requests try file order. A manually selected profile runs first, then the
others in file order. Provider failures, including unusable output, advance the chain;
valid non-plan results do not. Each provider is tried once per queue attempt. Exhausted
chains report `VISION_ALL_PROVIDERS_FAILED` with safe attempt details.
Waiting jobs resolve current settings at execution, including legacy saved selections.
Retry failed jobs explicitly; restarting does not requeue dead letters.

```json
{
  "profiles": [
    {
      "id": "local-qwen",
      "provider": "ollama",
      "model": "qwen3.8:latest",
      "base_url": "http://127.0.0.1:11434/v1",
      "response_format": "json_schema",
      "timeout_seconds": 1800
    }
  ]
}
```

Add only providers approved to receive images, in priority order. IDs must be unique.
Remove unavailable entries; there is no `enabled` flag. Optional fields are `label`,
`api_key_env` (an environment variable name, never the key), and `timeout_seconds`
(positive and finite; defaults: local 1800, cloud 90 seconds). Azure Foundry supports
Azure identity without a key reference; generic cloud providers require one. Optional
top-level `precheck_profile_id` retains the local-development geometry gate.
Missing files leave AI unavailable; malformed configuration prevents startup. The old
inline/default/provider/model/endpoint selection environment variables are not read.
Full fallback chains can take the sum of their configured timeouts.

### Other flags and environment settings

- [Backend settings and defaults](reailEstate_common/backend/config/settings.py).
- [Configuration groups](MAINTAINERS.md#configuration-groups).
- [Feature-flag registry](reailEstate_common/backend/platform_api/feature_flags/registry.py)
  and [override behavior](reailEstate_common/backend/platform_api/feature_flags/README.md).
  Overrides are stored in the database and managed in admin; `ENVIRONMENT` selects
  their environment. These flags do not select AI providers.
- [Local launcher](configure_runtime.ps1), [Compose](compose.services.yml), and
  [cloud Terraform](../.deployments/) supply runtime environment settings. Provision or
  mount the JSON at `FLOOR_PLAN_VISION_PROFILES_FILE` in cloud deployments; credentials
  remain in the platform's secret or identity mechanism.

Extraction mode, concurrency, scanning, security and other operational flags remain
independent of the provider file.

The Compose layout includes PostgreSQL, migrations, all web services, and the shared
worker. See [`../WORKFLOWS.md`](../WORKFLOWS.md) before using it.

Azure has validated planning artifacts but no authorized apply. AWS remains
offline-validated without an approved account deployment. Neither cloud may be
deployed without new approval.
