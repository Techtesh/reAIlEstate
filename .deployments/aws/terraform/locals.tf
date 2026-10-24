locals {
  name_prefix = substr("${var.workload_name}-${var.environment_name}", 0, 24)

  tags = merge({
    Environment = var.environment_name
    ManagedBy   = "terraform"
    Workload    = "workspace"
  }, var.tags)

  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)
  application_url    = "https://${aws_cloudfront_distribution.gateway.domain_name}"

  common_environment = {
    AWS_REGION                               = var.aws_region
    CLAMAV_ENABLED                           = tostring(var.clamav_enabled)
    CLAMAV_ENDPOINT                          = var.clamav_endpoint
    CORS_ALLOWED_ORIGINS                     = local.application_url
    CSRF_TRUSTED_ORIGINS                     = local.application_url
    DJANGO_ALLOWED_HOSTS                     = join(",", [aws_cloudfront_distribution.gateway.domain_name, aws_lb.gateway.dns_name, "127.0.0.1", "localhost"])
    DJANGO_DEBUG                             = "false"
    ENVIRONMENT                              = var.environment_name
    FLOOR_PLAN_EXTRACTION_MODE               = var.floor_plan_extraction_mode
    FLOOR_PLAN_VISION_PROFILES_FILE          = var.floor_plan_vision_profiles_file
    LOCAL_TRUST_BUILDER_UPLOADS              = "false"
    MEDIA_ROOT                               = "/workspace/legacy-media"
    OTEL_ENABLED                             = "true"
    OTEL_EXPORTER_ENDPOINT                   = "http://127.0.0.1:4318/v1/traces"
    PGDATABASE                               = aws_db_instance.application.db_name
    PGHOST                                   = aws_db_instance.application.address
    PGPORT                                   = tostring(aws_db_instance.application.port)
    PGSSLMODE                                = "require"
    PGUSER                                   = aws_db_instance.application.username
    PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = "true"
    PROJECT_PUBLIC_PREVIEW_ENABLED           = "false"
    USE_X_FORWARDED_PROTO                    = "true"
    WORKSPACE_DATA_ROOT                      = "/workspace"
  }

  application_environment = [
    for name, value in local.common_environment : {
      name  = name
      value = value
    }
  ]

  application_secrets = [
    {
      name      = "DJANGO_SECRET_KEY"
      valueFrom = aws_secretsmanager_secret.django_secret_key.arn
    },
    {
      name      = "PGPASSWORD"
      valueFrom = aws_secretsmanager_secret.postgresql_password.arn
    },
  ]

  otel_config = <<-YAML
    receivers:
      otlp:
        protocols:
          http:
            endpoint: 0.0.0.0:4318
    processors:
      memory_limiter:
        check_interval: 1s
        limit_mib: 192
      batch:
        timeout: 10s
        send_batch_size: 512
    exporters:
      awsxray:
        region: ${var.aws_region}
    service:
      telemetry:
        logs:
          level: info
      pipelines:
        traces:
          receivers: [otlp]
          processors: [memory_limiter, batch]
          exporters: [awsxray]
  YAML
}
