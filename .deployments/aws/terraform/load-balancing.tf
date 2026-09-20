resource "random_password" "origin_header" {
  length  = 40
  special = false
}

resource "aws_lb" "gateway" {
  name                       = "${local.name_prefix}-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.load_balancer.id]
  subnets                    = aws_subnet.public[*].id
  enable_deletion_protection = var.enable_deletion_protection
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "gateway" {
  name        = "${local.name_prefix}-gateway"
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id

  deregistration_delay = 30

  health_check {
    enabled             = true
    path                = "/health/ready"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 5
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.gateway.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "cloudfront" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_header.result]
    }
  }
}

resource "aws_cloudfront_cache_policy" "disabled" {
  name        = "${local.name_prefix}-caching-disabled"
  comment     = "Disable caching for the dynamic Django application."
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }

    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true
  }
}

resource "aws_cloudfront_origin_request_policy" "application" {
  name    = "${local.name_prefix}-all-viewer"
  comment = "Forward viewer request data required by Django."

  cookies_config {
    cookie_behavior = "all"
  }

  headers_config {
    header_behavior = "allViewer"
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name = "${local.name_prefix}-security"

  security_headers_config {
    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "SAMEORIGIN"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
      preload                    = true
    }
  }
}

resource "aws_cloudfront_distribution" "gateway" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "${local.name_prefix} HTTPS gateway"
  price_class     = var.cloudfront_price_class
  aliases         = var.cloudfront_aliases

  origin {
    domain_name = aws_lb.gateway.dns_name
    origin_id   = "application-load-balancer"

    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_header.result
    }

    custom_header {
      name  = "X-Forwarded-Client-Proto"
      value = "https"
    }

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "application-load-balancer"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id   = aws_cloudfront_origin_request_policy.application.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.cloudfront_acm_certificate_arn == ""
    acm_certificate_arn            = var.cloudfront_acm_certificate_arn == "" ? null : var.cloudfront_acm_certificate_arn
    ssl_support_method             = var.cloudfront_acm_certificate_arn == "" ? null : "sni-only"
    minimum_protocol_version       = var.cloudfront_acm_certificate_arn == "" ? null : "TLSv1.2_2021"
  }

  depends_on = [aws_lb_listener_rule.cloudfront]
}
