# AWS deployment

Use [the GitHub Actions deployment guide](ec2/README.md) for the current EC2 service.
It uses existing PostgreSQL/NFS storage and server-side runtime credentials. GitHub
environment secrets supply the target host and pinned SSH credentials. No AWS CLI,
resource IDs or AWS access keys are needed by the release workflow.

`terraform/` and `gateway/` retain the alternative ECS/Fargate infrastructure design.
They are not applied by the EC2 workflow and must not be pointed at existing resources
without a separate reviewed infrastructure plan. `scripts/` retains legacy provider
infrastructure helpers and offline EC2 configuration validation; only the latter is
used by the active release workflow. Cloud CLI operations require explicit command
permission under the repository's AGENTS.md rules.
