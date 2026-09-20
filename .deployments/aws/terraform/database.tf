resource "random_password" "postgresql_administrator" {
  length           = 32
  special          = true
  override_special = "_%@-"
}

resource "random_password" "django_secret_key" {
  length  = 64
  special = false
}

resource "aws_secretsmanager_secret" "postgresql_password" {
  name                    = "${local.name_prefix}/postgresql-password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "postgresql_password" {
  secret_id     = aws_secretsmanager_secret.postgresql_password.id
  secret_string = random_password.postgresql_administrator.result
}

resource "aws_secretsmanager_secret" "django_secret_key" {
  name                    = "${local.name_prefix}/django-secret-key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "django_secret_key" {
  secret_id     = aws_secretsmanager_secret.django_secret_key.id
  secret_string = random_password.django_secret_key.result
}

resource "aws_db_instance" "application" {
  identifier = "${local.name_prefix}-postgres"

  engine         = "postgres"
  engine_version = var.postgresql_engine_version
  instance_class = var.postgresql_instance_class

  db_name  = "realestate"
  username = "realestate"
  password = random_password.postgresql_administrator.result
  port     = 5432

  allocated_storage     = var.postgresql_allocated_storage_gb
  max_allocated_storage = var.postgresql_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.application.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period = var.postgresql_backup_retention_days
  copy_tags_to_snapshot   = true
  deletion_protection     = var.enable_deletion_protection
  skip_final_snapshot     = true

  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  performance_insights_enabled    = false
  apply_immediately               = false

  tags = {
    Name = "${local.name_prefix}-postgres"
  }
}
