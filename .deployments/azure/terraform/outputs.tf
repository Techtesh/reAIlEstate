output "resource_group_name" {
  description = "Resource group containing the POC."
  value       = azurerm_resource_group.main.name
}

output "container_registry_name" {
  description = "Registry used by the application and gateway builds."
  value       = azurerm_container_registry.main.name
}

output "container_registry_login_server" {
  description = "Registry login server."
  value       = azurerm_container_registry.main.login_server
}

output "container_app_name" {
  description = "Container App name when deploy_workload is enabled."
  value       = var.deploy_workload ? azurerm_container_app.platform[0].name : null
}

output "application_url" {
  description = "Public HTTPS endpoint when deploy_workload is enabled."
  value       = var.deploy_workload ? "https://${azurerm_container_app.platform[0].latest_revision_fqdn}" : null
}

output "admin_url" {
  description = "Django admin URL when deploy_workload is enabled."
  value       = var.deploy_workload ? "https://${azurerm_container_app.platform[0].latest_revision_fqdn}/admin/" : null
}

output "builder_url" {
  description = "Builder portal URL when deploy_workload is enabled."
  value       = var.deploy_workload ? "https://${azurerm_container_app.platform[0].latest_revision_fqdn}/builder" : null
}

output "migration_job_name" {
  description = "Manual migration job name when deploy_workload is enabled."
  value       = var.deploy_workload ? azurerm_container_app_job.migrate[0].name : null
}

output "postgresql_server_fqdn" {
  description = "Private PostgreSQL server FQDN."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "key_vault_name" {
  description = "Key Vault containing generated runtime secrets."
  value       = azurerm_key_vault.main.name
}
