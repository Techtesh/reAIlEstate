variable "aws_region" {
  description = "Primary AWS region."
  type        = string
  default     = "ap-south-1"
}

variable "expected_aws_account_id" {
  description = "Twelve-digit AWS account ID required for live plans and applies. Leave empty only for offline validation."
  type        = string
  default     = ""

  validation {
    condition     = var.expected_aws_account_id == "" || can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "expected_aws_account_id must be empty or a twelve-digit AWS account ID."
  }
}

variable "workload_name" {
  description = "Short lowercase name used in AWS resource names."
  type        = string
  default     = "workspace"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,17}$", var.workload_name))
    error_message = "workload_name must be 2-18 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "environment_name" {
  description = "Logical deployment environment."
  type        = string
  default     = "poc"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,11}$", var.environment_name))
    error_message = "environment_name must be 2-12 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "vpc_cidr" {
  description = "CIDR range for the POC VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "deploy_workload" {
  description = "Create the ECS task definitions and service after images have been pushed."
  type        = bool
  default     = false
}

variable "application_image_repository" {
  description = "ECR repository name for the shared Django application image."
  type        = string
  default     = "workspace-app"
}

variable "gateway_image_repository" {
  description = "ECR repository name for the Nginx gateway image."
  type        = string
  default     = "workspace-gateway"
}

variable "application_image_tag" {
  description = "Immutable application image tag."
  type        = string
  default     = "not-built"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", var.application_image_tag))
    error_message = "application_image_tag must be a valid OCI image tag."
  }
}

variable "gateway_image_tag" {
  description = "Immutable gateway image tag."
  type        = string
  default     = "not-built"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", var.gateway_image_tag))
    error_message = "gateway_image_tag must be a valid OCI image tag."
  }
}

variable "otel_collector_image" {
  description = "Pinned AWS Distro for OpenTelemetry Collector image."
  type        = string
  default     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.43.3"
}

variable "ecs_task_cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 2048
}

variable "ecs_task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 4096
}

variable "ecs_desired_count" {
  description = "POC task count. Keep at one because the worker is a singleton."
  type        = number
  default     = 1

  validation {
    condition     = var.ecs_desired_count == 1
    error_message = "ecs_desired_count must remain 1 until the worker supports multiple active replicas."
  }
}

variable "postgresql_engine_version" {
  description = "RDS PostgreSQL major version."
  type        = string
  default     = "16"
}

variable "postgresql_instance_class" {
  description = "RDS PostgreSQL instance class; validate availability before deployment."
  type        = string
  default     = "db.t4g.micro"
}

variable "postgresql_allocated_storage_gb" {
  description = "Initial RDS gp3 storage in GiB."
  type        = number
  default     = 20

  validation {
    condition     = var.postgresql_allocated_storage_gb >= 20
    error_message = "postgresql_allocated_storage_gb must be at least 20 GiB."
  }
}

variable "postgresql_max_allocated_storage_gb" {
  description = "RDS storage autoscaling ceiling in GiB."
  type        = number
  default     = 100
}

variable "postgresql_backup_retention_days" {
  description = "RDS automated backup retention."
  type        = number
  default     = 7

  validation {
    condition     = var.postgresql_backup_retention_days >= 0 && var.postgresql_backup_retention_days <= 35
    error_message = "postgresql_backup_retention_days must be between 0 and 35."
  }
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention."
  type        = number
  default     = 30
}

variable "cloudfront_price_class" {
  description = "CloudFront edge-location price class."
  type        = string
  default     = "PriceClass_200"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200", "PriceClass_All"], var.cloudfront_price_class)
    error_message = "cloudfront_price_class must be PriceClass_100, PriceClass_200, or PriceClass_All."
  }
}

variable "cloudfront_aliases" {
  description = "Optional custom DNS aliases for CloudFront."
  type        = list(string)
  default     = []
}

variable "cloudfront_acm_certificate_arn" {
  description = "Optional ACM certificate ARN from us-east-1 for CloudFront aliases."
  type        = string
  default     = ""
}

variable "enable_deletion_protection" {
  description = "Protect ALB and RDS from deletion. Enable for production."
  type        = bool
  default     = false
}

variable "floor_plan_extraction_mode" {
  description = "Extraction mode. Keep script until an approved AI profile is configured."
  type        = string
  default     = "script"

  validation {
    condition     = contains(["script", "ai"], var.floor_plan_extraction_mode)
    error_message = "floor_plan_extraction_mode must be script or ai."
  }
}

variable "floor_plan_vision_profiles_file" {
  description = "Path to an operator-provisioned vision-profiles.json inside the application container."
  type        = string
  default     = "/workspace/vision-profiles.json"
}

variable "clamav_enabled" {
  description = "Enable only after a reachable scanner endpoint has been deployed and validated."
  type        = bool
  default     = false
}

variable "clamav_endpoint" {
  description = "ClamAV TCP endpoint retained for a future scanner deployment."
  type        = string
  default     = "tcp://clamav:3310"
}

variable "tags" {
  description = "Additional tags merged with required workload tags."
  type        = map(string)
  default     = {}
}

check "cloudfront_certificate_configuration" {
  assert {
    condition = (
      (length(var.cloudfront_aliases) == 0 && var.cloudfront_acm_certificate_arn == "") ||
      (length(var.cloudfront_aliases) > 0 && var.cloudfront_acm_certificate_arn != "")
    )
    error_message = "cloudfront_aliases and cloudfront_acm_certificate_arn must either both be configured or both be empty."
  }
}
