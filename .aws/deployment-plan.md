# AWS deployment reference

The supported application release procedure is
[GitHub Actions deployment](../.deployments/aws/ec2/README.md).
Target identities and host addresses are intentionally not recorded in the repository.
Use GitHub environment secrets and ignored server configuration for the selected host.

The alternative ECS/Fargate Terraform templates are not the active EC2 stack and
must not be applied to existing console-created resources without a separate plan.
Agents need explicit permission for each explained AWS CLI command. This document
is not an authorization to authenticate, provision, resize, migrate or deploy.
