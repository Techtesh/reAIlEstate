locals {
  compact_name = replace(var.workload_name, "-", "")
  suffix       = random_string.resource_suffix.result

  resource_names = {
    container_app             = "ca-${var.workload_name}-${local.suffix}"
    container_app_environment = "cae-${var.workload_name}-${local.suffix}"
    container_registry        = substr("acr${local.compact_name}${local.suffix}", 0, 50)
    database                  = "realestate"
    file_share                = "workspace"
    key_vault                 = substr("kv-${var.workload_name}-${local.suffix}", 0, 24)
    log_analytics             = "log-${var.workload_name}-${local.suffix}"
    managed_identity          = "id-${var.workload_name}-${local.suffix}"
    migration_job             = "job-${var.workload_name}-migrate-${local.suffix}"
    postgres_server           = substr("psql-${var.workload_name}-${local.suffix}", 0, 63)
    storage_account           = substr("st${local.compact_name}${local.suffix}", 0, 24)
    virtual_network           = "vnet-${var.workload_name}-${local.suffix}"
  }

  required_tags = {
    environment = var.environment_name
    managed-by  = "terraform"
    workload    = "workspace"
  }
  tags = merge(local.required_tags, var.tags)

  container_app_fqdn = "${local.resource_names.container_app}.${azurerm_container_app_environment.main.default_domain}"

  common_environment = {
    AZURE_CLIENT_ID                          = azurerm_user_assigned_identity.workload.client_id
    CLAMAV_ENABLED                           = tostring(var.clamav_enabled)
    CLAMAV_ENDPOINT                          = var.clamav_endpoint
    CORS_ALLOWED_ORIGINS                     = "https://${local.container_app_fqdn}"
    CSRF_TRUSTED_ORIGINS                     = "https://${local.container_app_fqdn}"
    DATABASE_BACKEND                         = "postgresql"
    DJANGO_ALLOWED_HOSTS                     = local.container_app_fqdn
    DJANGO_DEBUG                             = "false"
    ENVIRONMENT                              = var.environment_name
    FLOOR_PLAN_EXTRACTION_MODE               = var.floor_plan_extraction_mode
    FLOOR_PLAN_VISION_PROFILES_FILE          = var.floor_plan_vision_profiles_file
    LOCAL_TRUST_BUILDER_UPLOADS              = "false"
    MEDIA_ROOT                               = "/workspace/legacy-media"
    OTEL_ENABLED                             = "true"
    OTEL_EXPORTER_ENDPOINT                   = "http://localhost:4318/v1/traces"
    PGDATABASE                               = azurerm_postgresql_flexible_server_database.application.name
    PGHOST                                   = azurerm_postgresql_flexible_server.main.fqdn
    PGPORT                                   = "5432"
    PGSSLMODE                                = "require"
    PGUSER                                   = azurerm_postgresql_flexible_server.main.administrator_login
    PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = "true"
    PROJECT_PUBLIC_PREVIEW_ENABLED           = "false"
    USE_X_FORWARDED_PROTO                    = "true"
    WORKSPACE_DATA_ROOT                      = "/workspace"
  }

  migration_environment = merge(local.common_environment, {
    OTEL_ENABLED = "false"
  })

  otel_collector_config = file("${path.module}/../gateway/otel-collector.yaml")
}
