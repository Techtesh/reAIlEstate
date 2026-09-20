# Deployment Plan

## Status

Cloud planning deferred by user

## Scope

- Add `.deployments/azure` and `.deployments/aws`.
- Use the initial topology: gateway -> one server running the services -> PostgreSQL database.
- Keep ClamAV endpoint configuration disabled by default.

## Confirmed Requirements

- Classification: proof of concept/demo.
- Scale: small, up to approximately 25 concurrent users.
- Budget: fit within available free or education-tier allowances.
- Data residency: India.
- Azure subscription: `088476fe-27ee-42b2-a867-268aab19dce3`.
- Azure region: Central India.
- Infrastructure recipe: Terraform for cross-cloud portability.
- Source layout: `adminService`, `builderService`, `buyerService`, and
  `reailEstate_common`.
- Compatibility: preserve the `platform_api` Django app label, table names, and
  migration history.

## Deferred Decisions

- Azure resource group.
- AWS account and region.
- Shared media persistence for the single-server phase.
- Provider-specific gateway, compute, and managed PostgreSQL resources.
- Azure quota validation and final education-tier availability checks.

## Execution

The source restructure is implemented independently of cloud infrastructure.
No `.deployments` infrastructure will be generated and no cloud deployment will
run until the deferred provider details are supplied and a completed plan is
approved.
