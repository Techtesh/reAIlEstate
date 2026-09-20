# Azure POC deployment

This directory maps the perforated monolith to one Azure Container Apps scaling
unit:

```text
Managed HTTPS ingress
  -> Nginx gateway
     -> admin service
     -> builder service
     -> buyer service
     -> PostgreSQL Flexible Server (private)
     -> shared Azure Files workspace
  + extraction worker
  + OpenTelemetry Collector -> Application Insights
```

The source of truth and approval boundary are in
[`../../.azure/deployment-plan.md`](../../.azure/deployment-plan.md).

## Safety status

The files are generated for offline review only. Do not apply them until all of
the following are true:

1. Azure CLI is authenticated to tenant
   `d68b2372-3268-46b9-8329-75273fb49a72`.
2. Subscription `088476fe-27ee-42b2-a867-268aab19dce3` is visible and selected.
3. Subscription and management-group policies have been reviewed.
4. Central India quota and capacity have been checked for Container Apps and
   PostgreSQL Flexible Server.
5. The PostgreSQL version and burstable SKU are available.
6. A protected Terraform state backend has been selected.
7. `terraform plan` has been reviewed.

The scripts reject a mismatched tenant or subscription and require explicit
execution switches for mutating operations.

## Files

- [`terraform/`](./terraform/) provisions the Azure foundation and, only when
  `deploy_workload=true`, the Container App and migration job.
- [`gateway/`](./gateway/) contains the path-routing Nginx image and the
  OpenTelemetry Collector configuration.
- [`scripts/`](./scripts/) contains guarded PowerShell workflows.

## Prerequisites

- Azure CLI with the Container Apps extension.
- Terraform 1.6 or newer.
- Permission to create the planned resources and role assignments.
- An existing Azure Storage backend for Terraform state.

No credentials belong in `.tfvars`, backend files, scripts, or source control.
Terraform state contains generated passwords and the Azure Files account key,
so local state is for syntax validation only and must not be used for a live
deployment.

## Offline validation

From the repository root:

```powershell
.\.deployments\azure\scripts\Initialize-AzureDeployment.ps1
```

This runs formatting checks, initializes providers without a backend, and runs
`terraform validate`. It does not sign in or contact the target subscription.

## Future deployment sequence

These steps are intentionally not authorized by the current offline-only
approval.

1. Copy `terraform.tfvars.example` to an ignored `deployment.auto.tfvars` and
   review every value.
2. Create or select a secure Azure Storage Terraform backend and copy
   `backend.hcl.example` to an ignored `backend.hcl`.
3. Authenticate explicitly:

   ```powershell
   az login --tenant d68b2372-3268-46b9-8329-75273fb49a72
   az account set --subscription 088476fe-27ee-42b2-a867-268aab19dce3
   ```

4. Complete policy, resource-provider, quota, and regional-capacity checks.
5. Plan and apply foundations with `deploy_workload=false`.
6. Build immutable application and gateway image tags in ACR.
7. Set `deploy_workload=true` and set both image tags in the ignored tfvars
   file.
8. Run and review a second Terraform plan before applying the workload.
9. Start the migration job and wait for it to succeed.
10. Run the smoke-test script and review logs and traces.

## POC security and reliability boundaries

- PostgreSQL is private and requires TLS, but password authentication is kept
  for compatibility with the current Django settings. The generated password is
  stored in Key Vault. Production should migrate to managed-identity/Entra
  authentication.
- The Azure Files mount requires a storage account key in Container Apps
  environment storage. That key is held in sensitive Terraform state.
- The storage account and Key Vault use public service endpoints for the POC;
  anonymous blob access is disabled and secrets require RBAC. Production should
  add private endpoints after validating Container Apps storage integration.
- One replica avoids duplicate durable dispatchers but is not highly available.
- PostgreSQL high availability and geo-redundant backups are disabled.
- Key Vault purge protection is configurable and off by default to permit POC
  teardown.
- ClamAV remains disabled until a scanner endpoint is deployed and tested.
- AI extraction defaults to script mode. The existing Azure OpenAI endpoint is
  cross-tenant and is not reused automatically.

## Routing

The gateway sends:

- `/admin/` and admin/review/operations APIs to the admin service.
- `/builder`, builder APIs, and ingestion APIs to the builder service.
- All remaining pages and APIs to the buyer service.
- `/static/` to immutable static files assembled from the application image.

The three web services are not directly exposed by Container Apps ingress.
