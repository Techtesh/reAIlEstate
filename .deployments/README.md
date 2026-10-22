# Deployment

Deploy the current service through [GitHub Actions](aws/ec2/README.md).
The workflow builds and checks a release, transfers its checked artifact over SSH,
and starts the services in private-preview or public-HTTPS mode.

Deployment-specific hosts, keys, IDs and account details belong in GitHub environment
secrets/variables or ignored server configuration. Agents must explain each AWS/Azure
CLI command and obtain explicit user permission before running it.

The application contract remains three web services, one shared PostgreSQL database,
a private persistent workspace, independent migration ownership and health endpoints.
[Runtime settings](../reailEstate/compose.services.yml) document service configuration.

AWS ECS and Azure Container Apps Terraform directories are alternative infrastructure
reference templates, not the active application deployment pipeline. They do not
adopt existing console-created resources automatically. Legacy provider tools remain
for separately requested infrastructure work; they are not release entrypoints.
