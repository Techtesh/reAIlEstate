resource "azurerm_user_assigned_identity" "workload" {
  name                = local.resource_names.managed_identity
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_key_vault" "main" {
  name                          = local.resource_names.key_vault
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  tenant_id                     = var.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = var.key_vault_purge_protection_enabled
  soft_delete_retention_days    = 7
  public_network_access_enabled = true
  tags                          = local.tags

  network_acls {
    bypass         = "AzureServices"
    default_action = "Allow"
  }
}

resource "azurerm_role_assignment" "deployment_key_vault_administrator" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Administrator"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "workload_key_vault_secrets_user" {
  scope                            = azurerm_key_vault.main.id
  role_definition_name             = "Key Vault Secrets User"
  principal_id                     = azurerm_user_assigned_identity.workload.principal_id
  skip_service_principal_aad_check = true
}

resource "random_password" "django_secret_key" {
  length  = 64
  special = false
}

resource "random_password" "postgresql_administrator" {
  length           = 32
  special          = true
  override_special = "_%@-"
}

resource "azurerm_key_vault_secret" "django_secret_key" {
  name         = "django-secret-key"
  value        = random_password.django_secret_key.result
  key_vault_id = azurerm_key_vault.main.id
  content_type = "Django signing secret"
  tags         = local.tags

  depends_on = [azurerm_role_assignment.deployment_key_vault_administrator]
}

resource "azurerm_key_vault_secret" "postgresql_password" {
  name         = "postgresql-password"
  value        = random_password.postgresql_administrator.result
  key_vault_id = azurerm_key_vault.main.id
  content_type = "PostgreSQL administrator password"
  tags         = local.tags

  depends_on = [azurerm_role_assignment.deployment_key_vault_administrator]
}

resource "azurerm_role_assignment" "azure_openai_user" {
  count = var.azure_openai_resource_id == "" ? 0 : 1

  scope                            = var.azure_openai_resource_id
  role_definition_name             = "Cognitive Services OpenAI User"
  principal_id                     = azurerm_user_assigned_identity.workload.principal_id
  skip_service_principal_aad_check = true
}
