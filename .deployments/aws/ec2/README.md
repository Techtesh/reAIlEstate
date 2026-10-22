# EC2 deployment using the migrated RDS and EFS

This is the current AWS deployment path. It runs the existing admin, builder,
buyer and optional worker processes on one Linux EC2 host with Docker Compose.
Caddy provides HTTPS and forwards to the existing Nginx routing/static-file image.
No ECS, Fargate, ALB, CloudFront, NAT gateway, or new database is required.

These are deployment instructions and local artifacts, not a live deployment.
Do not run the sibling Terraform foundation: it provisions a separate full stack.
The console-created RDS/EFS resources are not managed by that Terraform state.

## Offline checks

From the repository root run:

    ./.deployments/aws/scripts/Test-Ec2Configuration.ps1

This parses Compose and checks profiles, private listeners, mounts and the load
scenario without AWS access or real traffic. Bash syntax: bash -n .deployments/aws/ec2/start.sh.

## Existing resources

- Account: 938379788005; region: ap-south-1.
- VPC: vpc-08dec4fde809aa0cc.
- RDS: workspace-poc-postgres, PostgreSQL 17.11, database/user realestate.
  Copy the current endpoint from RDS; retrieve the password from Secrets Manager.
- EFS: fs-09fcd4b0bfeecbb04, already mounted at /mnt/realestate.
- App data on EFS: /workspace/user/media (274 files),
  /workspace/output/media (4,216 files), /workspace/legacy-media (4 files).
  These are EFS-root paths, not host-root paths. Counts matched local originals;
  contents and application access still require verification.
- Archive: s3://realestate-migration-local-to-aws-938379788005-ap-south-1-an/migration/realestate-media.tar.gz.
  Keep private. The archive is outside the app directory, at EFS /migration.
- Existing EC2 has default and launch-wizard-1 security groups.
  Identify its current IP and group IDs in the console rather than using old IPs.

## Capacity and budget

1,000 concurrent browsing users is a load-test target, not a capacity guarantee.
A micro instance with 1 GiB RAM is a migration host, not a launch sizing decision.
The web containers' memory limits total 3.4 GiB, excluding the OS/Docker.
Use at least 4 GiB for a web-only test; 8 GiB gives room for the optional 2 GiB
worker and operating system. Start with an x86_64 machine to match the image build,
then select launch CPU/RAM and RDS capacity from measurements. Do not change
instance size or spend credits without reviewing the account's available choices.
RDS micro can also bottleneck; monitor CPU, FreeableMemory, connections,
ReadLatency/WriteLatency, storage and burst-credit metrics.

Keep processing off during launch. Profiles only control startup: to stop an
already running worker, use the explicit stop command below. No anonymous or
authenticated response caching is introduced; personalized data stays private.
A single EC2 host has downtime on failure and deployment. There is no autoscaling.

New-account free-plan credits are finite, not a promise of perpetual free hosting.
Set Billing/Budgets alerts before launch. Review EC2, RDS, EBS, EFS/backup,
Secrets Manager, public IPv4, transfer and any AI costs. EFS currently uses Elastic
throughput in the console; review its throughput/backup charges rather than
assuming the old Terraform Bursting settings describe the live file system.
The EFS archive also takes space while retained. No cleanup is automatic.

## Prerequisites and networking

- Amazon Linux 2023 x86_64, Docker Engine and Docker Compose plugin >= 2.30
  (raw env-file support). Verify with docker version and docker compose version.
  Install from official Docker/Amazon sources; do not run unreviewed install scripts.
- A domain with its A record pointing to EC2's stable public IPv4.
  Remove stale AAAA records if IPv6 is not configured.
- In the EC2 security groups allow TCP 80/443 for visitors and TCP 22 only from
  your administrator IP. Do not expose 8002/8003/8004/8080 or Caddy admin port 2019.
  This deployment assumes Caddy is the direct public edge, without another proxy.
- RDS inbound TCP 5432 from the EC2 group. EFS mount-target inbound TCP 2049 from
  that group, same VPC, available mount target in EC2's AZ. Neither needs public access.
- Use the existing EFS mount, encrypted in transit:
  sudo mount -t efs -o tls fs-09fcd4b0bfeecbb04:/ /mnt/realestate
- Ensure EFS is mounted before Docker starts, including after a reboot.
  Add this entry to /etc/fstab only if an equivalent entry is not already present:

      fs-09fcd4b0bfeecbb04:/ /mnt/realestate efs _netdev,tls 0 0

  Use sudo systemctl edit docker and add:

      [Unit]
      RequiresMountsFor=/mnt/realestate
      After=network-online.target
      Wants=network-online.target

  Run sudo systemctl daemon-reload. Restarting Docker interrupts running containers;
  schedule that change before launch. Test a controlled reboot before opening traffic.
- All app containers run as UID/GID 1000 and bind EFS /workspace to container
  /workspace. Preserve that directory when configuring any later EFS access point.
- Ensure sufficient EBS free space for Docker images, unpacking and logs.
  Check docker system df and df -h; the initial 8 GiB disk may need expansion.
  Build images on your workstation, not on the small server.

## Build and transfer images from local PowerShell

From the repository root; use a new release tag for each build. No ECR or AWS CLI
credentials are needed for this path. The gateway build reuses the existing routing
and changes only the listener to loopback.

~~~powershell
docker build --platform linux/amd64 -t realestate-app:launch-1 -f reailEstate/Dockerfile reailEstate
if ($LASTEXITCODE -ne 0) { throw 'App build failed' }
docker build --platform linux/amd64 --build-arg APPLICATION_IMAGE=realestate-app:launch-1 -t realestate-ec2-gateway:launch-1 -f .deployments/aws/ec2/gateway.Dockerfile .
if ($LASTEXITCODE -ne 0) { throw 'Gateway build failed' }
docker save -o realestate-images.tar realestate-app:launch-1 realestate-ec2-gateway:launch-1
if ($LASTEXITCODE -ne 0) { throw 'Image export failed' }
scp -i "E:\Downloads\realestate-migration-ke.pem" realestate-images.tar ec2-user@CURRENT_EC2_IP:/home/ec2-user/
scp -i "E:\Downloads\realestate-migration-ke.pem" -r .deployments/aws/ec2 ec2-user@CURRENT_EC2_IP:/home/ec2-user/
~~~

Do not transfer .env/app.env from a working deployment or include them in images.
Review the image contents before transfer. Secrets and local media are excluded by
the existing application .dockerignore. Keep image tar files outside source control.
For Caddy, the Compose example follows the maintained 2-alpine tag; record/pin
the pulled digest after testing and use that same image for launch.

## Configure on EC2

~~~bash
docker load -i ~/realestate-images.tar
cd ~/ec2
cp .env.example .env
cp app.env.example app.env
chmod 600 .env app.env
curl --fail -o global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
~~~

Edit .env with your domain and release tags, and app.env with matching allowed
hosts/origins, the exact RDS endpoint/password, and a strong unique Django secret.
Generate a secret with openssl rand -hex 32 and enter it without posting it in chat.
The app.env file uses raw key=value syntax: passwords containing dollar signs,
quotes or hash characters are literal; do not surround values with quotes.
Never source app.env or paste it in shell command history.
.env is sourced by start.sh; use only the simple shell-safe substitutions shown.
The RDS connection uses verify-full with AWS's certificate bundle.

Existing restored rows must be kept: do not import fixtures again, run flush, or
create another PostgreSQL container. Check migration state first:

~~~bash
docker compose config --quiet
docker compose run --rm migrate python adminService/manage.py showmigrations --settings=admin_service.settings
~~~

Before applying pending migrations take an RDS snapshot, inspect the migration plan
(docker compose run --rm migrate python adminService/manage.py migrate --plan
--settings=admin_service.settings), stop all existing writers, then explicitly run:

~~~bash
docker compose run --rm migrate
~~~

Normal startup never applies migrations:

~~~bash
bash start.sh
docker compose ps
curl --fail "https://YOUR_DOMAIN/health/ready"
~~~

Verify buyer pages, builder login, admin login, and open a known PDF/image using
an authorized session. No direct public media alias is configured.
The gateway retains existing immutable static caching. Check logs with
docker compose logs --tail=100. Never print docker compose config without --quiet
in shared logs: it expands secrets. Docker administrators can inspect container
environments; restrict SSH, Docker group access and app.env accordingly.

## Processing and scanner

Start.sh always stops the worker and starts the web services only. Uploaded work
will queue until the worker runs; do not promise processing during launch.

~~~bash
docker compose --profile processing up -d worker
docker compose --profile processing stop worker
~~~

Concurrency is bounded to one ingestion job/vision call. CLAMAV is disabled and the
local trust bypass is disabled: deploy/configure a scanner and validate ingestion
before accepting untrusted builder uploads. AI provider settings are not deployed.
These settings preserve the prior POC boundary, not full ingestion readiness.

## Launch rehearsal

Run the existing smoke test from local PowerShell:

~~~powershell
.\.deployments\aws\scripts\Test-AwsDeployment.ps1 -BaseUrl https://YOUR_DOMAIN
~~~

Install k6 from its official distribution on a separate machine. Test 50 users
first, then 250, then 1000. Never generate load from the app EC2.
For example, from PowerShell:

~~~powershell
$env:BASE_URL = 'https://YOUR_DOMAIN'
$env:USERS = '50'
$env:PATHS = '/'
k6 run .deployments/aws/ec2/load-test.js
~~~

The default checks only HTML, not browser assets or database-heavy API traffic.
Set PATHS to comma-separated real published catalogue/detail/filter URLs observed
in your browser, and add authenticated journeys with separate test accounts before
claiming launch capacity. Do not use production account passwords in this script.
DRF anonymous throttles are per IP and process-local; one load generator may get
429 responses and fail before reaching the intended traffic. Use distributed
generators/test accounts and realistic pacing; do not disable production throttles.
This scenario has 5-15 second think time, not 1000 requests per second.
The test aborts above 1% failures after one minute and requires p95 under 2 seconds;
a static-page pass alone does not qualify the application.

Watch docker stats, free -h, RDS metrics, response codes and disk space throughout.
Tune bounded worker counts and query performance, not just VM size. Raise user
targets only after the prior run passes. RDS, EFS and external services need their
own headroom. Record results, machine sizes and credit burn; downsize after launch
only after measuring steady traffic. No 1000-user test has been run by these artifacts.

## Updates and rollback

Keep previous image tags and the EFS/S3/local backups until verification passes.
Load new immutable images, update both tags, then run bash start.sh in a maintenance
window. To roll back code, restore the previous two tags and run it again.
Database schema changes may prevent code-only rollback; review reversibility before
migrating. Do not restore an old database over new writes without a recovery plan.
Do not use docker compose down -v: it deletes Caddy's certificate volumes.

## GitHub build pipeline and one-command deployment

The repository-root workflow .github/workflows/aws-ec2-release.yml builds on
workflow_dispatch only. It has no AWS permissions or SSH secrets and never contacts
EC2. It checks Compose, builds x86_64 app/gateway images, runs all three Django
system checks, validates Nginx/Caddy, and exports a checksummed artifact retained
for three days. Caddy's tested image is included. GitHub build minutes and artifact
storage count against your plan; this is not guaranteed free.

The workflow must reach the repository default branch before manual dispatch is
available. Select the intended release branch/ref when running it. This workspace
uses hj/develop; do not merge/push unrelated changes. Nothing has been pushed.

### One-time EC2 configuration

Complete the prerequisites above: Docker/Compose, at least 4 GiB RAM for this
configuration, enough EBS disk, EFS mounted before Docker, domain A record,
80/443 ingress and SSH restricted to your current IP. The deployment script refuses
the 1 GiB migration micro.

Copy the templates to EC2 from local PowerShell:

~~~powershell
scp -i "E:\Downloads\realestate-migration-ke.pem" -r .deployments/aws/ec2 ec2-user@CURRENT_EC2_IP:/home/ec2-user/
~~~

On EC2, create configuration only if it does not exist yet:

~~~bash
cd ~/ec2
cp -n .env.example .env
cp -n app.env.example app.env
chmod 600 .env app.env
curl --fail -o global-bundle.pem https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem
~~~

Fill the domain, RDS endpoint/password, unique Django secret and origins as
described above. ~/ec2 remains the configuration source; update it when credentials
rotate. Secrets are copied only on EC2 into release directories protected with
mode 0700. No secret values go through GitHub.

### Build, download and deploy

Use Actions -> Build AWS EC2 release -> Run workflow, or GitHub CLI:

~~~powershell
gh auth login
gh workflow run aws-ec2-release.yml --repo Techtesh/Workspace --ref hj/develop
gh run list --repo Techtesh/Workspace --workflow aws-ec2-release.yml --limit 5
gh run watch RUN_ID --repo Techtesh/Workspace --exit-status
gh run download RUN_ID --repo Techtesh/Workspace -n aws-ec2-release -D output/aws-release
~~~

Replace RUN_ID with the successful run, and FULL_COMMIT_SHA with its exact commit.
The artifact contains the archive and .sha256 sidecar. Only deploy reviewed commits.
From local PowerShell:

~~~powershell
./.deployments/aws/scripts/Deploy-Ec2Release.ps1 -ReleaseArchive "./output/aws-release/realestate-FULL_COMMIT_SHA.tar.gz" -HostName "CURRENT_EC2_IP" -KeyPath "E:\Downloads\realestate-migration-ke.pem"
~~~

First verify the SSH host key interactively using the earlier instructions.
The command refuses unknown/changed host keys. It verifies checksums locally and on
EC2, imports images, checks pending migrations, starts web services only, and checks
internal and public HTTPS readiness. It never provisions AWS or applies migrations.

Releases live at ~/realestate-releases/FULL_COMMIT_SHA. On success,
~/realestate-current points to the active config. Failed deployments can partially
replace running containers; inspect logs and run the previous release's start.sh
for explicit code rollback. There is no automatic rollback. Retrying the same SHA
refuses to overwrite the existing directory. Review the failure and use a new
commit/build, or explicitly start that release after correcting prerequisites.

Pending migrations fail before web replacement. Apply them manually from the new
release directory using the snapshot/writer-stop procedure above, then run start.sh
and check public readiness. Processing remains off.

No AWS CLI is needed for normal releases: the existing PEM and GitHub CLI (or
Actions UI artifact download) suffice. Keep RDS/EFS and the existing S3 backups.
No container build, GitHub run or live deployment has been executed locally.

Additional offline checks:

~~~bash
bash -n .deployments/aws/ec2/package.sh
bash -n .deployments/aws/ec2/deploy-release.sh
bash .deployments/aws/ec2/test-package.sh
~~~
