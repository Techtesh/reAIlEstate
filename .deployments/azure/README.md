# Azure infrastructure reference

This is an alternative infrastructure reference, not an implemented Azure release
pipeline. Application releases currently use [EC2 GitHub Actions](../aws/ec2/README.md).
Dated validation statements in the [Azure plan](deployment-plan.md) are historical
and do not establish current resource state or authorize cloud commands. Keep
target-specific configuration outside tracked files and follow the repository's
explicit command-permission rule for agent-run AWS/Azure CLI commands.

`terraform/` contains a parameterized Container Apps, PostgreSQL and Azure Files
alternative to the active EC2 service. Supply tenant/subscription IDs through ignored
configuration or environment variables; no real account identity is a default.
`gateway/` contains routing/telemetry templates. Legacy `scripts/` remain for
separately requested Azure infrastructure work; they are not application release
entrypoints and must not be run by an agent without command-specific permission.

The currently supported application release path is the
[GitHub Actions EC2 workflow](../aws/ec2/README.md). This change does not introduce
or claim a live Azure deployment. A future Azure Actions workflow would need its own
authentication, protected state, migration procedure and target-specific validation.
Do not reuse Terraform state or credentials between providers.
