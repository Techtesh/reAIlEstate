# AWS POC deployment

This directory maps the perforated monolith to one ECS Fargate task and keeps
AWS state, credentials, and lifecycle independent from the Azure deployment.

```text
CloudFront managed HTTPS
  -> internet-facing ALB restricted to CloudFront origin addresses
     -> one ECS Fargate task
        +-- Nginx gateway :8080
        +-- Admin Django service :8002
        +-- Builder Django service :8003
        +-- Buyer Django service :8004
        +-- Extraction/recommendation worker
        +-- AWS Distro for OpenTelemetry Collector
             |
             +---- encrypted EFS workspace
             +---- private RDS PostgreSQL
             +---- CloudWatch Logs and AWS X-Ray
```

The source of truth and approval boundary are in
[`../../.aws/deployment-plan.md`](../../.aws/deployment-plan.md).

## Safety status

These files are for offline generation and validation only. Do not run a live
plan or apply until:

1. The AWS account ID and credentials have been confirmed.
2. Service Control Policies, IAM permissions, quotas, and `ap-south-1`
   capacity have been validated.
3. A protected, versioned S3 Terraform state bucket has been selected.
4. The expected monthly cost has been reviewed.
5. A live Terraform plan has been reviewed and explicitly approved.

No AWS resources were created while generating these files.

## Files

- [`terraform/`](./terraform/) provisions networking, CloudFront, ALB, ECS,
  ECR, RDS PostgreSQL, EFS, Secrets Manager, CloudWatch, and X-Ray.
- [`gateway/`](./gateway/) contains the AWS Nginx gateway image.
- [`scripts/`](./scripts/) contains guarded PowerShell workflows.

## Offline validation

From the repository root:

```powershell
.\.deployments\aws\scripts\Initialize-AwsDeployment.ps1
```

This runs Terraform formatting, initializes providers without a state backend,
and runs `terraform validate`. It does not require AWS credentials and does not
create resources.

## Future deployment sequence

1. Install AWS CLI v2 and authenticate to the intended account.
2. Validate IAM permissions, account-level controls, regional service
   availability, Fargate quotas, RDS capacity, and EFS availability.
3. Create or select a versioned and encrypted S3 Terraform backend.
4. Copy `terraform.tfvars.example` to an ignored `deployment.auto.tfvars`,
   replace the account placeholder, and review all values.
5. Plan and apply foundations with `deploy_workload=false`.
6. Build and push immutable application and gateway images to ECR.
7. Set `deploy_workload=true` and set both image tags.
8. Plan and apply the ECS workload.
9. Run the migration task and wait for exit code zero.
10. Run the smoke-test script and verify live IAM permissions.

The scripts require explicit execution switches and exact AWS account approval
for mutating operations.

## POC constraints

- One always-running Fargate task preserves the singleton worker model.
- Fargate tasks use public subnets and public IPs for outbound package/service
  access, but their security group accepts inbound traffic only from the ALB.
- The ALB accepts traffic only from the AWS-managed CloudFront origin prefix
  list and requires a generated origin-verification header.
- RDS uses isolated subnets and has no public endpoint.
- EFS is encrypted, uses an access point, requires TLS and IAM authorization,
  and has automatic backups enabled.
- RDS Multi-AZ and deletion protection are disabled by default for this POC.
- CloudFront provides the default `cloudfront.net` HTTPS endpoint. A custom
  domain requires an ACM certificate issued in `us-east-1`.
- ClamAV and AI extraction remain disabled until compatible endpoints are
  separately deployed and validated.
