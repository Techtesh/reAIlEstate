resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_task_definition" "application" {
  count = var.deploy_workload ? 1 : 0

  family                   = "${local.name_prefix}-application"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.ecs_task_cpu)
  memory                   = tostring(var.ecs_task_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume {
    name = "workspace"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.workspace.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.workspace.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "gateway"
      image     = "${aws_ecr_repository.gateway.repository_url}:${var.gateway_image_tag}"
      essential = true
      cpu       = 128
      memory    = 512
      portMappings = [{
        name          = "gateway"
        containerPort = 8080
        hostPort      = 8080
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      dependsOn = [
        {
          containerName = "admin"
          condition     = "HEALTHY"
        },
        {
          containerName = "builder"
          condition     = "HEALTHY"
        },
        {
          containerName = "buyer"
          condition     = "HEALTHY"
        },
      ]
      healthCheck = {
        command     = ["CMD-SHELL", "wget --spider --quiet http://127.0.0.1:8080/health/live || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "gateway"
        }
      }
    },
    {
      name      = "admin"
      image     = "${aws_ecr_repository.application.repository_url}:${var.application_image_tag}"
      essential = true
      cpu       = 384
      memory    = 768
      command = [
        "gunicorn",
        "--bind=0.0.0.0:8002",
        "--workers=2",
        "--timeout=180",
        "--access-logfile=-",
        "admin_service.wsgi:application",
      ]
      environment = concat(local.application_environment, [{
        name  = "OTEL_SERVICE_NAME"
        value = "mmr-real-estate-admin"
      }])
      secrets = local.application_secrets
      portMappings = [{
        name          = "admin"
        containerPort = 8002
        hostPort      = 8002
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      mountPoints = [{
        sourceVolume  = "workspace"
        containerPath = "/workspace"
        readOnly      = false
      }]
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import os,urllib.request; host=os.environ['DJANGO_ALLOWED_HOSTS'].split(',')[0]; request=urllib.request.Request('http://127.0.0.1:8002/health/live',headers={'Host':host}); assert urllib.request.urlopen(request,timeout=5).status == 200\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
      linuxParameters = {
        initProcessEnabled = true
      }
      stopTimeout = 60
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "admin"
        }
      }
    },
    {
      name      = "builder"
      image     = "${aws_ecr_repository.application.repository_url}:${var.application_image_tag}"
      essential = true
      cpu       = 384
      memory    = 768
      command = [
        "gunicorn",
        "--bind=0.0.0.0:8003",
        "--workers=2",
        "--timeout=180",
        "--access-logfile=-",
        "builder_service.wsgi:application",
      ]
      environment = concat(local.application_environment, [{
        name  = "OTEL_SERVICE_NAME"
        value = "mmr-real-estate-builder"
      }])
      secrets = local.application_secrets
      portMappings = [{
        name          = "builder"
        containerPort = 8003
        hostPort      = 8003
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      mountPoints = [{
        sourceVolume  = "workspace"
        containerPath = "/workspace"
        readOnly      = false
      }]
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import os,urllib.request; host=os.environ['DJANGO_ALLOWED_HOSTS'].split(',')[0]; request=urllib.request.Request('http://127.0.0.1:8003/health/live',headers={'Host':host}); assert urllib.request.urlopen(request,timeout=5).status == 200\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
      linuxParameters = {
        initProcessEnabled = true
      }
      stopTimeout = 60
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "builder"
        }
      }
    },
    {
      name      = "buyer"
      image     = "${aws_ecr_repository.application.repository_url}:${var.application_image_tag}"
      essential = true
      cpu       = 384
      memory    = 768
      command = [
        "gunicorn",
        "--bind=0.0.0.0:8004",
        "--workers=2",
        "--timeout=180",
        "--access-logfile=-",
        "buyer_service.wsgi:application",
      ]
      environment = concat(local.application_environment, [{
        name  = "OTEL_SERVICE_NAME"
        value = "mmr-real-estate-buyer"
      }])
      secrets = local.application_secrets
      portMappings = [{
        name          = "buyer"
        containerPort = 8004
        hostPort      = 8004
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      mountPoints = [{
        sourceVolume  = "workspace"
        containerPath = "/workspace"
        readOnly      = false
      }]
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import os,urllib.request; host=os.environ['DJANGO_ALLOWED_HOSTS'].split(',')[0]; request=urllib.request.Request('http://127.0.0.1:8004/health/live',headers={'Host':host}); assert urllib.request.urlopen(request,timeout=5).status == 200\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
      linuxParameters = {
        initProcessEnabled = true
      }
      stopTimeout = 60
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "buyer"
        }
      }
    },
    {
      name      = "worker"
      image     = "${aws_ecr_repository.application.repository_url}:${var.application_image_tag}"
      essential = true
      cpu       = 512
      memory    = 1024
      command = [
        "python",
        "adminService/manage.py",
        "run_extraction_dispatcher",
        "--settings=admin_service.settings",
      ]
      environment = concat(local.application_environment, [{
        name  = "OTEL_SERVICE_NAME"
        value = "mmr-real-estate-worker"
      }])
      secrets = local.application_secrets
      mountPoints = [{
        sourceVolume  = "workspace"
        containerPath = "/workspace"
        readOnly      = false
      }]
      linuxParameters = {
        initProcessEnabled = true
      }
      stopTimeout = 60
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    },
    {
      name      = "otel-collector"
      image     = var.otel_collector_image
      essential = false
      cpu       = 256
      memory    = 256
      command   = ["--config=env:AOT_CONFIG_CONTENT"]
      environment = [{
        name  = "AOT_CONFIG_CONTENT"
        value = local.otel_config
      }]
      portMappings = [{
        name          = "otlp-http"
        containerPort = 4318
        hostPort      = 4318
        protocol      = "tcp"
        appProtocol   = "http"
      }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "otel"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = var.application_image_tag != "not-built"
      error_message = "Set application_image_tag to an immutable ECR image tag before enabling deploy_workload."
    }

    precondition {
      condition     = var.gateway_image_tag != "not-built"
      error_message = "Set gateway_image_tag to an immutable ECR image tag before enabling deploy_workload."
    }
  }
}

resource "aws_ecs_service" "application" {
  count = var.deploy_workload ? 1 : 0

  name                              = "${local.name_prefix}-service"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.application[0].arn
  desired_count                     = var.ecs_desired_count
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  health_check_grace_period_seconds = 300
  wait_for_steady_state             = true
  enable_execute_command            = false

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  deployment_maximum_percent         = 200
  deployment_minimum_healthy_percent = 100

  network_configuration {
    assign_public_ip = true
    security_groups  = [aws_security_group.ecs.id]
    subnets          = aws_subnet.public[*].id
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = 8080
  }

  depends_on = [
    aws_efs_mount_target.workspace,
    aws_lb_listener_rule.cloudfront,
    aws_iam_role_policy.execution_secrets,
    aws_iam_role_policy.task,
  ]
}

resource "aws_ecs_task_definition" "migration" {
  count = var.deploy_workload ? 1 : 0

  family                   = "${local.name_prefix}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  volume {
    name = "workspace"

    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.workspace.id
      transit_encryption = "ENABLED"

      authorization_config {
        access_point_id = aws_efs_access_point.workspace.id
        iam             = "ENABLED"
      }
    }
  }

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = "${aws_ecr_repository.application.repository_url}:${var.application_image_tag}"
      essential = true
      command = [
        "python",
        "adminService/manage.py",
        "migrate",
        "--noinput",
        "--settings=admin_service.settings",
      ]
      environment = [
        for item in local.application_environment : {
          name  = item.name
          value = item.name == "OTEL_ENABLED" ? "false" : item.value
        }
      ]
      secrets = local.application_secrets
      mountPoints = [{
        sourceVolume  = "workspace"
        containerPath = "/workspace"
        readOnly      = false
      }]
      linuxParameters = {
        initProcessEnabled = true
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.application.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "migration"
        }
      }
    },
  ])

  lifecycle {
    precondition {
      condition     = var.application_image_tag != "not-built"
      error_message = "Set application_image_tag to an immutable ECR image tag before enabling deploy_workload."
    }
  }
}
