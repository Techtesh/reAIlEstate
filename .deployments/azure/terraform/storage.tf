resource "azurerm_storage_account" "workspace" {
  name                            = local.resource_names.storage_account
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  access_tier                     = "Hot"
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = true
  public_network_access_enabled   = true
  local_user_enabled              = false
  tags                            = local.tags
}

resource "azurerm_storage_share" "workspace" {
  name               = local.resource_names.file_share
  storage_account_id = azurerm_storage_account.workspace.id
  access_tier        = "TransactionOptimized"
  enabled_protocol   = "SMB"
  quota              = var.file_share_quota_gb
}

resource "azurerm_container_app_environment_storage" "workspace" {
  name                         = "workspace"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.workspace.name
  share_name                   = azurerm_storage_share.workspace.name
  access_key                   = azurerm_storage_account.workspace.primary_access_key
  access_mode                  = "ReadWrite"
}
