resource "azurerm_postgresql_flexible_server" "main" {
  name                          = local.resource_names.postgres_server
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = var.postgresql_version
  delegated_subnet_id           = azurerm_subnet.postgresql.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgresql.id
  public_network_access_enabled = false
  administrator_login           = "realestate"
  administrator_password        = random_password.postgresql_administrator.result
  sku_name                      = var.postgresql_sku_name
  storage_mb                    = var.postgresql_storage_mb
  auto_grow_enabled             = true
  backup_retention_days         = var.postgresql_backup_retention_days
  geo_redundant_backup_enabled  = false
  tags                          = local.tags

  authentication {
    active_directory_auth_enabled = false
    password_auth_enabled         = true
  }

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgresql]
}

resource "azurerm_postgresql_flexible_server_database" "application" {
  name      = local.resource_names.database
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}
