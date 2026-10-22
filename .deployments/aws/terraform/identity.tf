data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name_prefix}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadRuntimeSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.django_secret_key.arn,
      aws_secretsmanager_secret.postgresql_password.arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${local.name_prefix}-read-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-ecs-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid    = "MountWorkspace"
    effect = "Allow"
    actions = [
      "elasticfilesystem:ClientMount",
      "elasticfilesystem:ClientWrite",
    ]
    resources = [aws_efs_file_system.workspace.arn]

    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.workspace.arn]
    }
  }

  statement {
    sid    = "PublishTraces"
    effect = "Allow"
    actions = [
      "xray:PutTelemetryRecords",
      "xray:PutTraceSegments",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${local.name_prefix}-runtime"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
