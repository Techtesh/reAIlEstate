resource "aws_efs_file_system" "workspace" {
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "bursting"

  lifecycle_policy {
    transition_to_ia = "AFTER_30_DAYS"
  }

  tags = {
    Name = "${local.name_prefix}-workspace"
  }
}

resource "aws_efs_backup_policy" "workspace" {
  file_system_id = aws_efs_file_system.workspace.id

  backup_policy {
    status = "ENABLED"
  }
}

resource "aws_efs_access_point" "workspace" {
  file_system_id = aws_efs_file_system.workspace.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/workspace"

    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "0770"
    }
  }

  tags = {
    Name = "${local.name_prefix}-workspace"
  }
}

resource "aws_efs_mount_target" "workspace" {
  count = 2

  file_system_id  = aws_efs_file_system.workspace.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}
