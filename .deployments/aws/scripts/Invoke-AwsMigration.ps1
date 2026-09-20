[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ExpectedAccountId,

    [string] $Region = "ap-south-1",

    [string] $TerraformDirectory = (Join-Path $PSScriptRoot "..\terraform"),

    [switch] $Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

if (-not $Execute) {
    Write-Host "No migration was started. Re-run with -Execute after the Workload plan has been applied."
    return
}

Assert-ExternalCommand -Name "aws"
Assert-ExternalCommand -Name "terraform"
Assert-AwsContext -ExpectedAccountId $ExpectedAccountId -Region $Region

$terraformPath = (Resolve-Path $TerraformDirectory).Path
$clusterName = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "ecs_cluster_name"
$taskDefinition = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "migration_task_definition_arn"
$securityGroup = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "ecs_security_group_id"
$subnets = & terraform "-chdir=$terraformPath" output -json ecs_public_subnet_ids | ConvertFrom-Json
Assert-LastExitCode -Operation "Reading ECS subnet outputs"

if ([string]::IsNullOrWhiteSpace($taskDefinition)) {
    throw "The migration task definition is not deployed. Apply the Workload stage first."
}

$networkConfiguration = @{
    awsvpcConfiguration = @{
        subnets        = @($subnets)
        securityGroups = @($securityGroup)
        assignPublicIp = "ENABLED"
    }
} | ConvertTo-Json -Depth 4 -Compress

$runResult = & aws ecs run-task `
    --region $Region `
    --cluster $clusterName `
    --task-definition $taskDefinition `
    --launch-type FARGATE `
    --count 1 `
    --network-configuration $networkConfiguration `
    --output json | ConvertFrom-Json
Assert-LastExitCode -Operation "Starting the ECS migration task"

if (@($runResult.failures).Count -gt 0 -or @($runResult.tasks).Count -ne 1) {
    throw "AWS did not start exactly one migration task: $($runResult.failures | ConvertTo-Json -Compress)"
}

$taskArn = $runResult.tasks[0].taskArn
& aws ecs wait tasks-stopped --region $Region --cluster $clusterName --tasks $taskArn
Assert-LastExitCode -Operation "Waiting for the ECS migration task"

$result = & aws ecs describe-tasks `
    --region $Region `
    --cluster $clusterName `
    --tasks $taskArn `
    --query "tasks[0].{stopCode:stopCode,stoppedReason:stoppedReason,container:containers[?name=='migrate']|[0]}" `
    --output json | ConvertFrom-Json
Assert-LastExitCode -Operation "Reading the ECS migration result"

if ($result.container.exitCode -ne 0) {
    throw "Migration task failed with exit code $($result.container.exitCode): $($result.stoppedReason)"
}

Write-Host "Database migration completed successfully in task $taskArn."
