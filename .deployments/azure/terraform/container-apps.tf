resource "azurerm_container_app_environment" "main" {
  name                               = local.resource_names.container_app_environment
  location                           = azurerm_resource_group.main.location
  resource_group_name                = azurerm_resource_group.main.name
  infrastructure_subnet_id           = azurerm_subnet.container_apps.id
  infrastructure_resource_group_name = "me-${var.workload_name}-${local.suffix}"
  internal_load_balancer_enabled     = false
  public_network_access              = "Enabled"
  logs_destination                   = "log-analytics"
  log_analytics_workspace_id         = azurerm_log_analytics_workspace.main.id
  zone_redundancy_enabled            = false
  tags                               = local.tags

  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_app" "platform" {
  count = var.deploy_workload ? 1 : 0

  name                         = local.resource_names.container_app
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload.id
  }

  secret {
    name                = "django-secret-key"
    identity            = azurerm_user_assigned_identity.workload.id
    key_vault_secret_id = azurerm_key_vault_secret.django_secret_key.versionless_id
  }

  secret {
    name                = "postgresql-password"
    identity            = azurerm_user_assigned_identity.workload.id
    key_vault_secret_id = azurerm_key_vault_secret.postgresql_password.versionless_id
  }

  secret {
    name                = "appinsights-connection"
    identity            = azurerm_user_assigned_identity.workload.id
    key_vault_secret_id = azurerm_key_vault_secret.application_insights_connection_string.versionless_id
  }

  ingress {
    allow_insecure_connections = false
    external_enabled           = true
    target_port                = 8080
    transport                  = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas                     = 1
    max_replicas                     = 1
    termination_grace_period_seconds = 60

    volume {
      name         = "workspace"
      storage_name = azurerm_container_app_environment_storage.workspace.name
      storage_type = "AzureFile"
    }

    container {
      name   = "gateway"
      image  = "${azurerm_container_registry.main.login_server}/${var.gateway_image_repository}:${var.gateway_image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      startup_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health/live"
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 30
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health/ready"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 6
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8080
        path                    = "/health/live"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }

    container {
      name    = "admin"
      image   = "${azurerm_container_registry.main.login_server}/${var.application_image_repository}:${var.application_image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["gunicorn"]
      args = [
        "--bind=0.0.0.0:8002",
        "--workers=2",
        "--timeout=180",
        "--access-logfile=-",
        "admin_service.wsgi:application",
      ]

      dynamic "env" {
        for_each = merge(local.common_environment, { OTEL_SERVICE_NAME = "mmr-real-estate-admin" })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name        = "PGPASSWORD"
        secret_name = "postgresql-password"
      }

      volume_mounts {
        name = "workspace"
        path = "/workspace"
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8002
        path                    = "/health/live"
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 30

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8002
        path                    = "/health/ready"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 6

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8002
        path                    = "/health/live"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }
    }

    container {
      name    = "builder"
      image   = "${azurerm_container_registry.main.login_server}/${var.application_image_repository}:${var.application_image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["gunicorn"]
      args = [
        "--bind=0.0.0.0:8003",
        "--workers=2",
        "--timeout=180",
        "--access-logfile=-",
        "builder_service.wsgi:application",
      ]

      dynamic "env" {
        for_each = merge(local.common_environment, { OTEL_SERVICE_NAME = "mmr-real-estate-builder" })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name        = "PGPASSWORD"
        secret_name = "postgresql-password"
      }

      volume_mounts {
        name = "workspace"
        path = "/workspace"
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8003
        path                    = "/health/live"
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 30

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8003
        path                    = "/health/ready"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 6

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8003
        path                    = "/health/live"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }
    }

    container {
      name    = "buyer"
      image   = "${azurerm_container_registry.main.login_server}/${var.application_image_repository}:${var.application_image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["gunicorn"]
      args = [
        "--bind=0.0.0.0:8004",
        "--workers=2",
        "--timeout=180",
        "--access-logfile=-",
        "buyer_service.wsgi:application",
      ]

      dynamic "env" {
        for_each = merge(local.common_environment, { OTEL_SERVICE_NAME = "mmr-real-estate-buyer" })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name        = "PGPASSWORD"
        secret_name = "postgresql-password"
      }

      volume_mounts {
        name = "workspace"
        path = "/workspace"
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 8004
        path                    = "/health/live"
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 30

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 8004
        path                    = "/health/ready"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 6

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 8004
        path                    = "/health/live"
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3

        header {
          name  = "Host"
          value = local.container_app_fqdn
        }
      }
    }

    container {
      name    = "worker"
      image   = "${azurerm_container_registry.main.login_server}/${var.application_image_repository}:${var.application_image_tag}"
      cpu     = 0.75
      memory  = "1.5Gi"
      command = ["python"]
      args = [
        "adminService/manage.py",
        "run_extraction_dispatcher",
        "--settings=admin_service.settings",
      ]

      dynamic "env" {
        for_each = merge(local.common_environment, { OTEL_SERVICE_NAME = "mmr-real-estate-worker" })
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name        = "PGPASSWORD"
        secret_name = "postgresql-password"
      }

      volume_mounts {
        name = "workspace"
        path = "/workspace"
      }
    }

    container {
      name    = "otel-collector"
      image   = var.otel_collector_image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["/otelcol-contrib"]
      args    = ["--config=env:OTELCOL_CONFIG"]

      env {
        name        = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        secret_name = "appinsights-connection"
      }

      env {
        name  = "OTELCOL_CONFIG"
        value = local.otel_collector_config
      }

      startup_probe {
        transport               = "TCP"
        port                    = 4318
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 30
      }

      liveness_probe {
        transport               = "TCP"
        port                    = 4318
        initial_delay           = 10
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.application_image_tag != "not-built"
      error_message = "Set application_image_tag to an immutable built image tag before enabling deploy_workload."
    }

    precondition {
      condition     = var.gateway_image_tag != "not-built"
      error_message = "Set gateway_image_tag to an immutable built image tag before enabling deploy_workload."
    }
  }

  depends_on = [
    azurerm_container_app_environment_storage.workspace,
    azurerm_role_assignment.workload_acr_pull,
    azurerm_role_assignment.workload_key_vault_secrets_user,
  ]
}

resource "azurerm_container_app_job" "migrate" {
  count = var.deploy_workload ? 1 : 0

  name                         = local.resource_names.migration_job
  location                     = azurerm_resource_group.main.location
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  workload_profile_name        = "Consumption"
  replica_timeout_in_seconds   = 1800
  replica_retry_limit          = 1
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.workload.id
  }

  secret {
    name                = "django-secret-key"
    identity            = azurerm_user_assigned_identity.workload.id
    key_vault_secret_id = azurerm_key_vault_secret.django_secret_key.versionless_id
  }

  secret {
    name                = "postgresql-password"
    identity            = azurerm_user_assigned_identity.workload.id
    key_vault_secret_id = azurerm_key_vault_secret.postgresql_password.versionless_id
  }

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  template {
    volume {
      name         = "workspace"
      storage_name = azurerm_container_app_environment_storage.workspace.name
      storage_type = "AzureFile"
    }

    container {
      name    = "migrate"
      image   = "${azurerm_container_registry.main.login_server}/${var.application_image_repository}:${var.application_image_tag}"
      cpu     = 0.5
      memory  = "1Gi"
      command = ["python"]
      args = [
        "adminService/manage.py",
        "migrate",
        "--noinput",
        "--settings=admin_service.settings",
      ]

      dynamic "env" {
        for_each = local.migration_environment
        content {
          name  = env.key
          value = env.value
        }
      }

      env {
        name        = "DJANGO_SECRET_KEY"
        secret_name = "django-secret-key"
      }

      env {
        name        = "PGPASSWORD"
        secret_name = "postgresql-password"
      }

      volume_mounts {
        name = "workspace"
        path = "/workspace"
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.application_image_tag != "not-built"
      error_message = "Set application_image_tag to an immutable built image tag before enabling deploy_workload."
    }
  }

  depends_on = [
    azurerm_container_app_environment_storage.workspace,
    azurerm_role_assignment.workload_acr_pull,
    azurerm_role_assignment.workload_key_vault_secrets_user,
  ]
}
