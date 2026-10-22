output "aws_account_id" {
  description = "AWS account used by Terraform."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "AWS region used by Terraform."
  value       = var.aws_region
}

output "application_repository_url" {
  description = "ECR repository URL for the application image."
  value       = aws_ecr_repository.application.repository_url
}

output "gateway_repository_url" {
  description = "ECR repository URL for the gateway image."
  value       = aws_ecr_repository.gateway.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster that owns the application service and migration tasks."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name when deploy_workload is enabled."
  value       = var.deploy_workload ? aws_ecs_service.application[0].name : null
}

output "migration_task_definition_arn" {
  description = "Migration task definition when deploy_workload is enabled."
  value       = var.deploy_workload ? aws_ecs_task_definition.migration[0].arn : null
}

output "ecs_public_subnet_ids" {
  description = "Subnets used by Fargate tasks."
  value       = aws_subnet.public[*].id
}

output "ecs_security_group_id" {
  description = "Security group used by Fargate tasks."
  value       = aws_security_group.ecs.id
}

output "application_url" {
  description = "Public CloudFront HTTPS endpoint."
  value       = local.application_url
}

output "admin_url" {
  description = "Django admin URL."
  value       = "${local.application_url}/admin/"
}

output "builder_url" {
  description = "Builder portal URL."
  value       = "${local.application_url}/builder"
}

output "rds_endpoint" {
  description = "Private PostgreSQL endpoint."
  value       = aws_db_instance.application.address
}

output "efs_file_system_id" {
  description = "Shared workspace EFS file system."
  value       = aws_efs_file_system.workspace.id
}
