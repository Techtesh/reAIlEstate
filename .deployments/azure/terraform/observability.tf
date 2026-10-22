resource "azurerm_log_analytics_workspace" "main" {
  name                = local.resource_names.log_analytics
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                                 = "appi-${var.workload_name}-${local.suffix}"
  location                             = azurerm_resource_group.main.location
  resource_group_name                  = azurerm_resource_group.main.name
  workspace_id                         = azurerm_log_analytics_workspace.main.id
  application_type                     = "web"
  daily_data_cap_in_gb                 = var.application_insights_daily_cap_gb
  daily_data_cap_notifications_enabled = true
  sampling_percentage                  = var.application_insights_sampling_percentage
  internet_ingestion_enabled           = true
  internet_query_enabled               = true
  local_authentication_enabled         = true
  tags                                 = local.tags
}

resource "azurerm_key_vault_secret" "application_insights_connection_string" {
  name         = "application-insights-connection-string"
  value        = azurerm_application_insights.main.connection_string
  key_vault_id = azurerm_key_vault.main.id
  content_type = "Application Insights connection string"
  tags         = local.tags

  depends_on = [azurerm_role_assignment.deployment_key_vault_administrator]
}
