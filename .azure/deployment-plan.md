# Azure Deployment Plan

## Status

**Validated**

Live validation is complete. Deployment was explicitly deferred after cost
review; do not apply the Terraform plan without a new user confirmation.

## Scope

Prepare Azure deployment artifacts for the current perforated-monolith
architecture:

- `reailEstate/adminService`
- `reailEstate/builderService`
- `reailEstate/buyerService`
- `reailEstate/reailEstate_common`
- Shared PostgreSQL database
- Shared persistent media/workspace storage
- Shared extraction queue worker
- ClamAV endpoint configuration, disabled by default

The first Azure topology intentionally keeps the services in one deployment and
one scaling unit. The application remains separable so that later architecture
can move each responsibility into an independently scaled Container App and
adopt a leader/follower data model.

## Confirmed Deployment Context

| Item | Value |
|---|---|
| Environment classification | POC/demo |
| Expected load | Approximately 25 concurrent users |
| Cost posture | Cost optimized; use free/education allowances where available |
| Data residency | India |
| Azure subscription ID | `088476fe-27ee-42b2-a867-268aab19dce3` |
| Azure tenant ID | `d68b2372-3268-46b9-8329-75273fb49a72` |
| Primary region | Central India |
| Infrastructure as code | Terraform |
| Default resource group | `rg-workspace-poc-centralindia` |
| Topology | Gateway -> co-located services/worker -> PostgreSQL |

Resource names will use a short, configurable workload prefix and a generated
suffix where Azure requires global uniqueness.

## Authentication and Validation Context

The Azure CLI is authenticated to tenant
`d68b2372-3268-46b9-8329-75273fb49a72`, with subscription
`088476fe-27ee-42b2-a867-268aab19dce3` selected.

Live validation confirmed:

- The subscription display name is `Visual Studio Enterprise Subscription`.
- Central India is available for every planned Azure resource type.
- Required resource providers are registered.
- Container Apps quota has sufficient available capacity.
- PostgreSQL 16 and the `Standard_B1ms` burstable SKU are available.
- A dedicated, access-controlled Terraform state backend is available.
- Azure OpenAI remains intentionally disabled because no same-tenant model
  deployment has been approved or validated.

## Selected POC Architecture

Use one Azure Container Apps environment and one externally reachable,
multi-container Container App. This is the closest Azure mapping to the
requested gateway -> server with multiple services -> database topology while
retaining the service boundaries already present in the repository.

```text
Internet
   |
   v
Azure Container Apps managed HTTPS ingress
   |
   v
One Container App replica (one POC scaling unit)
   +-- Nginx gateway :8080
   +-- Admin Django service :8002
   +-- Builder Django service :8003
   +-- Buyer Django service :8004
   +-- Extraction queue worker
   +-- OpenTelemetry Collector
          |
          +---- shared Azure Files mount
          |
          +---- private PostgreSQL Flexible Server
          |
          +---- Application Insights / Log Analytics
```

### Why this topology

- It preserves the current admin, builder, buyer, and worker process boundaries.
- The gateway is the only public container and all application containers share
  the replica network through `localhost`.
- All processes mount the same persistent workspace/media share, preserving the
  existing `RoutedStorage` and filesystem behavior without an immediate Blob
  Storage rewrite.
- One replica avoids duplicate singleton workers and matches the expected POC
  load.
- A later phase can split the containers into separate Container Apps without
  first undoing application-layer service separation.

### Deliberate POC constraints

- Minimum replicas: 1.
- Maximum replicas: 1.
- Zone redundancy and PostgreSQL high availability: disabled.
- PostgreSQL compute: burstable starting SKU, subject to Central India
  availability and quota validation.
- Azure Files: Standard LRS.
- Container registry: Basic.
- This topology is not the final production topology and does not provide
  service-level independent scaling or replica-level high availability.

## Gateway Responsibilities

The Nginx gateway container will:

- Terminate only the in-replica HTTP hop; public TLS is terminated by Azure
  Container Apps managed ingress.
- Proxy `/admin/` and admin-specific API routes to the admin service.
- Proxy `/builder` and builder-specific API routes to the builder service.
- Proxy buyer pages and buyer API routes to the buyer service.
- Serve the builder and buyer Vite assets produced during the gateway image
  build.
- Proxy Django admin and platform static assets to the admin service.
- Preserve forwarding headers and correlation IDs.
- Enforce request-body limits suitable for the existing media ingestion flow.
- Expose a gateway health endpoint used by Container Apps probes.

All three Django services retain their own `/health/` endpoints and receive
container-level startup, readiness, and liveness probes.

## Network Design

- One VNet in Central India.
- One dedicated `/27` subnet delegated to the Container Apps environment.
- One dedicated `/28` subnet delegated to PostgreSQL Flexible Server.
- One private DNS zone for PostgreSQL.
- PostgreSQL public network access disabled.
- Container Apps environment remains externally reachable only through managed
  ingress on the gateway target port.
- Azure Files access is restricted as far as supported by the selected
  Container Apps/Azure Files integration and validated before apply.
- HTTPS only; minimum TLS 1.2 for platform services.

The VNet and subnet sizes are POC allocations but leave enough address space for
the selected managed services. They must be checked against subscription policy
before deployment.

## Data and Secrets

### PostgreSQL

- Azure Database for PostgreSQL Flexible Server.
- PostgreSQL 17 if supported by the selected region/SKU at validation time;
  otherwise use the newest supported version compatible with the application.
- Database name: configurable, default `realestate`.
- Application login: configurable, default `realestate`.
- TLS required with `PGSSLMODE=require`.
- 32 GiB starting storage with 7-day backup retention.
- No public endpoint, high availability, geo-redundant backup, or read replica
  for the POC.
- Django migrations run from an on-demand Azure Container Apps Job owned by the
  admin service.

### Shared media/workspace data

- One StorageV2 account.
- One Azure Files share mounted at `/workspace` in the admin, builder, buyer,
  and worker containers.
- `WORKSPACE_DATA_ROOT=/workspace`.
- `MEDIA_ROOT=/workspace/legacy-media`.
- Secure transfer required and public blob access disabled.

### Secrets

- Generate the Django secret key and PostgreSQL password during deployment.
- Store runtime secrets in Azure Key Vault.
- Use a user-assigned managed identity for Container Apps image pulls and Key
  Vault secret references.
- Do not place secrets in Terraform variable files, scripts, container images,
  source control, or command output.
- Terraform state is sensitive and must use an access-controlled encrypted
  backend before a real deployment.

For a disposable POC, Key Vault purge protection will be configurable and
disabled by default to permit teardown. This is a documented deviation from the
production recommendation to enable purge protection.

## AI Extraction

The currently tested Azure OpenAI endpoint is in a different subscription and a
different tenant. It will not be wired into this deployment by default.

Generated artifacts will support two explicit modes:

1. Default safe mode: `FLOOR_PLAN_EXTRACTION_MODE=script`.
2. Optional Azure Foundry/OpenAI mode: configure a target-tenant endpoint,
   deployment name, and managed-identity access after model availability, quota,
   and data-residency requirements are validated.

If an Azure OpenAI resource is included in Terraform, it will be gated behind an
opt-in variable that defaults to `false`. Its model deployment must not be
enabled until the chosen model is confirmed available with sufficient quota in
an approved region.

## Malware Scanning

- `CLAMAV_ENABLED=false` by default.
- `CLAMAV_ENDPOINT` remains a required, configurable deployment value with
  default placeholder `tcp://clamav:3310`.
- No ClamAV container or managed endpoint is provisioned in the initial POC.
- Enabling scanning later requires deploying or supplying a reachable endpoint
  and validating fail-closed upload behavior before setting
  `CLAMAV_ENABLED=true`.

## Observability

- One Log Analytics workspace.
- One workspace-based Application Insights resource.
- One OpenTelemetry Collector sidecar in the Container App.
- Django services export OTLP traces to
  `http://localhost:4318/v1/traces`.
- Container Apps console and system logs flow to Log Analytics.
- Application Insights connection details are supplied to the collector without
  embedding them in application source.
- Existing correlation IDs and service-specific `OTEL_SERVICE_NAME` values are
  preserved.

## Planned Azure Resources

| Quantity | Resource | Initial configuration |
|---:|---|---|
| 1 | Resource group | Central India; configurable name |
| 1 | Virtual network | POC address space |
| 2 | Subnets | Container Apps `/27`; PostgreSQL `/28` |
| 1 | Private DNS zone | PostgreSQL Flexible Server |
| 1 | Azure Container Registry | Basic |
| 1 | User-assigned managed identity | ACR pull and Key Vault secret access |
| 1 | Key Vault | RBAC mode; soft delete; purge protection configurable |
| 1 | Storage account | Standard LRS, StorageV2 |
| 1 | Azure Files share | Shared `/workspace` mount |
| 1 | Log Analytics workspace | Cost-conscious retention |
| 1 | Application Insights | Workspace based |
| 1 | Container Apps environment | Consumption workload profile, VNet integrated |
| 1 | Multi-container Container App | Gateway, 3 web services, worker, OTel collector |
| 1 | Container Apps Job | Manual/on-demand admin migration job |
| 1 | PostgreSQL Flexible Server | Burstable, 32 GiB, private access, no HA |
| 1 | PostgreSQL database | `realestate` by default |
| 0 by default | Azure OpenAI resource/deployment | Optional; blocked on model/quota validation |

## Planned Repository Artifacts

After this plan is approved for offline generation, create:

```text
.deployments/
  README.md
  azure/
    README.md
    gateway/
      Dockerfile
      nginx.conf
      otel-collector.yaml
    terraform/
      versions.tf
      providers.tf
      variables.tf
      locals.tf
      network.tf
      identity.tf
      observability.tf
      storage.tf
      database.tf
      registry.tf
      container-apps.tf
      outputs.tf
      terraform.tfvars.example
    scripts/
      Initialize-AzureDeployment.ps1
      Build-AzureImages.ps1
      Invoke-AzureMigration.ps1
      Test-AzureDeployment.ps1
  aws/
    README.md
```

The provider-neutral README will explain the common runtime contract. The AWS
directory will reserve the provider boundary and document that AWS IaC is not
part of this Azure setup.

Terraform will support a two-stage workflow:

1. Provision foundational resources with workload deployment disabled.
2. Build and push immutable application/gateway image tags.
3. Enable the workload, run the migration job, and execute smoke tests.

The generated scripts will require explicit subscription and tenant parameters,
verify the active context, and stop rather than silently deploying to a
different subscription.

## Required Pre-Deployment Checks

Before changing this plan to **Ready for Validation**:

1. Authenticate to tenant `d68b2372-3268-46b9-8329-75273fb49a72`.
2. Select subscription `088476fe-27ee-42b2-a867-268aab19dce3`.
3. Confirm the expected subscription display name and current principal.
4. List subscription and management-group policy assignments.
5. Verify required resource providers are registered or can be registered.
6. Check Central India availability, current usage, and quotas for:
   - Container Apps managed environments and consumption vCPU.
   - PostgreSQL Flexible Server burstable compute.
   - Public IP/network resources used by managed ingress.
   - Azure Container Registry.
   - Storage accounts.
   - Optional Azure OpenAI model deployments and token quota.
7. Confirm the chosen PostgreSQL major version and SKU are available.
8. Confirm the Container Apps Azure Files network configuration.
9. Select or create a secure Terraform state backend.
10. Run formatting, Terraform initialization without backend, validation, and
    provider-supported static checks.
11. Run the repository's Azure validation workflow.
12. Review the generated Terraform plan before any apply.

## Validation Checklist

- [x] All validation checks pass
  - [x] Terraform CLI is installed.
  - [x] Azure CLI is installed.
  - [x] Azure CLI is authenticated to the confirmed subscription.
  - [x] Terraform initialization succeeds.
  - [x] `terraform fmt -check` succeeds.
  - [x] `terraform validate` succeeds.
  - [x] `terraform plan` succeeds against the target subscription.
  - [x] Terraform state is accessible and can be listed.
  - [x] Terraform files contain no unresolved Go-style environment templates.
  - [x] No `main.tfvars.json` file requires JSON validation.
  - [x] The application and gateway images build successfully.
  - [x] Required RBAC assignments are present in Terraform.
  - [x] Target-subscription policy, provider, quota, and regional capacity checks pass.

## 7. Validation Proof

Validation completed on 2026-09-20 against the confirmed tenant and
subscription.

| Check | Command or evidence | Result |
|---|---|---|
| Azure context | `az account show` | PASS: `Visual Studio Enterprise Subscription`, target tenant and subscription selected |
| Policy assignments | Azure Policy assignment list at subscription scope | PASS: audit/MFA policies found; the only location restriction blocks West Europe, not Central India |
| Resource providers | `az provider show` | PASS: App, PostgreSQL, Container Registry, Storage, Key Vault, Network, Managed Identity, Operational Insights, Insights, and Quota are registered |
| Container Apps quota | `check-quota.ps1 -ResourceProvider Microsoft.App -Region centralindia` | PASS: 20 managed environments and 50 sandbox cores available |
| Regional availability | Azure provider resource-type location queries | PASS: Container Apps, Container Apps jobs, PostgreSQL Flexible Server, ACR, Storage, and Key Vault are available in Central India |
| PostgreSQL capacity | `az postgres flexible-server list-skus --location centralindia` | PASS: PostgreSQL 16 and `Standard_B1ms` are available in zones 1, 2, and 3 |
| Terraform state | Azure Storage data-plane check and `terraform state list` | PASS: dedicated encrypted Standard LRS backend, Microsoft Entra authentication, shared keys disabled, private container, versioning enabled, and 7-day delete retention |
| Terraform format and syntax | `Initialize-AzureDeployment.ps1 -Stage Validate` | PASS |
| Live Terraform plan | `Initialize-AzureDeployment.ps1 -Stage Foundation -BackendConfigPath .deployments\azure\terraform\backend.hcl` | PASS: 26 create, 0 change, 0 destroy |
| Application image | `docker build` using the configured Microsoft package feed proxy | PASS |
| Django services in image | `manage.py check` for admin, builder, and buyer | PASS: no issues |
| Gateway image | `docker build` and `nginx -t` | PASS |
| Template scan | Go-style environment-template scan | PASS: no unresolved templates |

## Role Assignment Verification

- Status: Verified.
- Workload identity roles:
  - `AcrPull` scoped to the deployment's container registry.
  - `Key Vault Secrets User` scoped to the deployment's Key Vault.
  - Optional `Cognitive Services OpenAI User` is created only when an approved
    same-tenant Azure OpenAI resource ID is configured.
- Deployment identity role:
  - `Key Vault Administrator` scoped to the deployment's Key Vault so Terraform
    can create the generated application secrets.
- Shared Azure Files access uses the Container Apps environment storage account
  key; no unrelated subscription-wide data-plane role is granted.

## Approval Boundary

Completed with explicit user approval:

- Registering the required Azure resource providers.
- Creating the dedicated Terraform state resource group, storage account,
  container, and least-privilege state access role.
- Running authenticated read-only validation and Terraform planning.

Not authorized by the current scope:

- Applying the 26-resource foundation Terraform plan.
- Pushing images to Azure Container Registry.
- Running database migrations against Azure.
- Reusing the existing cross-tenant Azure OpenAI resource.

## Remaining Decisions Before Live Deployment

- Confirm whether a new Azure OpenAI resource is desired and which India region
  and model deployment meet availability, quota, and residency requirements.
- Decide whether Key Vault purge protection should be enabled despite slower
  POC teardown.
- Reconfirm the estimated monthly cost before any future Terraform apply.
