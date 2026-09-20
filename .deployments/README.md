# Deployment providers

This directory keeps cloud-specific infrastructure outside the application
packages while preserving one runtime contract.

## Runtime contract

Every provider implementation must supply:

- One HTTPS gateway.
- Admin, builder, buyer, and extraction-worker processes from the shared
  application image.
- One PostgreSQL database.
- One persistent filesystem mounted at `/workspace`.
- The environment variables documented in
  [`reailEstate/compose.services.yml`](../reailEstate/compose.services.yml).
- A one-shot migration command owned by the admin service.
- Health checks for `/health/live` and `/health/ready`.
- Secret injection without committing credentials.
- `CLAMAV_ENABLED=false` unless a reachable scanner has been deployed and
  validated.

## Providers

- [`azure/`](./azure/) contains the approved POC Terraform and operational
  scripts.
- [`aws/`](./aws/) contains offline-only AWS Terraform and operational scripts.

Provider directories must not share Terraform state, provider credentials, or
generated secret files.
