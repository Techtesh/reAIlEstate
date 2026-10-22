provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

check "expected_aws_account" {
  assert {
    condition = (
      var.expected_aws_account_id == "" ||
      data.aws_caller_identity.current.account_id == var.expected_aws_account_id
    )
    error_message = "The authenticated AWS account does not match expected_aws_account_id."
  }
}
