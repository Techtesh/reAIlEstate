variable "subscription_id" {
  description = "Azure subscription that owns the deployment."
  type        = string
  default     = "088476fe-27ee-42b2-a867-268aab19dce3"

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.subscription_id))
    error_message = "subscription_id must be a UUID."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant that owns the target subscription."
  type        = string
  default     = "d68b2372-3268-46b9-8329-75273fb49a72"

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.tenant_id))
    error_message = "tenant_id must be a UUID."
  }
}

variable "location" {
  description = "Primary Azure region."
  type        = string
  default     = "Central India"
}

variable "resource_group_name" {
  description = "Resource group created for the POC."
  type        = string
  default     = "rg-workspace-poc-centralindia"
}

variable "workload_name" {
  description = "Short lowercase prefix used in resource names."
  type        = string
  default     = "workspace-poc"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.workload_name))
    error_message = "workload_name must be 2-16 lowercase letters, digits, or hyphens and start with a letter."
  }
}

variable "environment_name" {
  description = "Logical environment label."
  type        = string
  default     = "poc"
}

variable "deploy_workload" {
  description = "Create the Container App and migration job after images have been built."
  type        = bool
  default     = false
}

variable "application_image_repository" {
  description = "Repository name for the shared Django application image."
  type        = string
  default     = "workspace-app"
}

variable "gateway_image_repository" {
  description = "Repository name for the Nginx gateway image."
  type        = string
  default     = "workspace-gateway"
}

variable "application_image_tag" {
  description = "Immutable application image tag. Set before deploy_workload is enabled."
  type        = string
  default     = "not-built"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", var.application_image_tag))
    error_message = "application_image_tag must be a valid OCI image tag."
  }
}

variable "gateway_image_tag" {
  description = "Immutable gateway image tag. Set before deploy_workload is enabled."
  type        = string
  default     = "not-built"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", var.gateway_image_tag))
    error_message = "gateway_image_tag must be a valid OCI image tag."
  }
}

variable "otel_collector_image" {
  description = "Pinned OpenTelemetry Collector Contrib image."
  type        = string
  default     = "otel/opentelemetry-collector-contrib:0.140.0"
}

variable "postgresql_version" {
  description = "PostgreSQL major version supported in the target region."
  type        = string
  default     = "16"

  validation {
    condition     = contains(["15", "16", "17"], var.postgresql_version)
    error_message = "postgresql_version must be 15, 16, or 17."
  }
}

variable "postgresql_sku_name" {
  description = "Flexible Server SKU; validate regional availability before apply."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgresql_storage_mb" {
  description = "Flexible Server storage in MiB."
  type        = number
  default     = 32768
}

variable "postgresql_backup_retention_days" {
  description = "Flexible Server backup retention."
  type        = number
  default     = 7

  validation {
    condition     = var.postgresql_backup_retention_days >= 7 && var.postgresql_backup_retention_days <= 35
    error_message = "postgresql_backup_retention_days must be between 7 and 35."
  }
}

variable "file_share_quota_gb" {
  description = "Maximum capacity of the shared media/workspace file share."
  type        = number
  default     = 10

  validation {
    condition     = var.file_share_quota_gb >= 1 && var.file_share_quota_gb <= 5120
    error_message = "file_share_quota_gb must be between 1 and 5120."
  }
}

variable "log_retention_days" {
  description = "Log Analytics retention."
  type        = number
  default     = 30
}

variable "application_insights_daily_cap_gb" {
  description = "Daily Application Insights ingestion cap."
  type        = number
  default     = 1
}

variable "application_insights_sampling_percentage" {
  description = "Percentage of Application Insights telemetry retained."
  type        = number
  default     = 25

  validation {
    condition     = var.application_insights_sampling_percentage > 0 && var.application_insights_sampling_percentage <= 100
    error_message = "application_insights_sampling_percentage must be greater than 0 and at most 100."
  }
}

variable "key_vault_purge_protection_enabled" {
  description = "Enable for production. Disabled by default so the POC can be torn down."
  type        = bool
  default     = false
}

variable "floor_plan_extraction_mode" {
  description = "Extraction mode. Keep script until an approved Azure AI profile is configured."
  type        = string
  default     = "script"

  validation {
    condition     = contains(["script", "ai"], var.floor_plan_extraction_mode)
    error_message = "floor_plan_extraction_mode must be script or ai."
  }
}

variable "floor_plan_vision_profiles_json" {
  description = "Non-secret JSON profile registry for AI extraction endpoints and model deployments."
  type        = string
  default     = "{}"

  validation {
    condition     = can(jsondecode(var.floor_plan_vision_profiles_json))
    error_message = "floor_plan_vision_profiles_json must contain valid JSON."
  }
}

variable "floor_plan_default_vision_profile" {
  description = "Default profile ID when AI extraction is enabled."
  type        = string
  default     = ""
}

variable "azure_openai_resource_id" {
  description = "Optional same-tenant Azure OpenAI resource ID to grant the workload identity access to."
  type        = string
  default     = ""
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
  description = "Additional tags merged with the required workload tags."
  type        = map(string)
  default     = {}
}
