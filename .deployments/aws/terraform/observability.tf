resource "aws_cloudwatch_log_group" "application" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_xray_sampling_rule" "application" {
  rule_name      = "${local.name_prefix}-default"
  priority       = 1000
  version        = 1
  reservoir_size = 1
  fixed_rate     = 0.10
  url_path       = "*"
  host           = "*"
  http_method    = "*"
  service_type   = "*"
  service_name   = "*"
  resource_arn   = "*"
}
