# Deploy the service with GitHub Actions

The supported application release path is **Actions -> Deploy EC2 service**
(`.github/workflows/aws-ec2-release.yml`). It builds Linux images from the selected
Git commit, checks all three services against disposable PostgreSQL, validates the
gateway, packages a checksummed artifact, and deploys that artifact over SSH.
It runs no AWS/Azure CLI commands and never provisions infrastructure or applies
migrations. The same workflow supports private preview and public HTTPS.

## 1. Prepare the host once

Use an existing Linux x86-64 host with at least 4 GiB RAM, adequate image/archive
storage, Docker Engine, Compose >=2.30, Python 3, curl and OpenSSH. The runner must
be able to reach its SSH port. Keep SSH restricted to your trusted runner network;
use a private/self-hosted runner when a hosted runner cannot reach that network.
Do not open SSH to the internet just to make a workflow pass.

Mount the existing persistent NFS workspace before Docker starts. Configure its
actual filesystem identity outside the repository. Verify the workspace contains
`user/media`, `output/media`, and `legacy-media`; keep ownership compatible with
container UID/GID 1000. Add the mount to the host's filesystem table and make
Docker depend on that mount. Preserve existing database and evidence data.

In the deployment user's `~/ec2` directory, place these protected runtime files:

- `.env`, based on `.env.example`: set `WORKSPACE_PATH` and `DOMAIN`.
  Keep assignments literal; values are read as data, never sourced as shell code.
  Actions selects both application image tags from the checked commit.
- `app.env`, based on `app.env.example`: set the PostgreSQL connection, verified
  database TLS, strong Django secret, allowed hosts and origins, and service settings.
- `global-bundle.pem`: the trusted database CA bundle used by `PGSSLMODE=verify-full`.

Restrict the directory to its owner and both environment files to mode 0600.
Never commit or upload these files as GitHub artifacts. Database credentials stay
on the server; Actions needs SSH deployment credentials only. Initial provisioning,
credential retrieval and cloud changes are outside this release workflow. An agent
must explain and obtain explicit permission before running any `aws` or `az` command.

For public HTTPS, configure your domain's DNS, public 80/443 access and matching
Django allowed hosts/origins. Keep app ports and gateway 8080 private. For preview,
set `DOMAIN=localhost`; no public domain or HTTPS listener is started.

## 2. Configure GitHub

Commit and push the reviewed changes, including this workflow. GitHub requires a
workflow with `workflow_dispatch` on the default branch before manual dispatch is
available. Select the intended application branch/ref when running it.

Create a GitHub environment, for example `production`, under repository Settings
-> Environments. Configure branch restrictions and reviewers appropriate for your
repository. Add these **environment secrets**:

| Secret | Value |
| --- | --- |
| `EC2_HOST` | Target SSH hostname or public IP |
| `SSH_PRIVATE_KEY` | Dedicated deployment user's private key |
| `SSH_KNOWN_HOSTS` | Verified OpenSSH known-host entries matching the host and port |

Do not generate trust dynamically with `ssh-keyscan` in the workflow. Verify the
host key through an independent trusted channel before adding `SSH_KNOWN_HOSTS`.
For a non-default port, the known-host entry uses `[host]:port`.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SSH_USER` | `ec2-user` | Existing server deployment user |
| `SSH_PORT` | `22` | Target SSH port |
| `DEPLOY_RUNNER` | `ubuntu-latest` | Linux runner label with SSH access to the target |

The deployment user needs Docker access and write access to its release/config
folders. This grants substantial host access; use a dedicated key and restrict who
can run deployments. No AWS keys, tenant IDs, resource IDs or personal paths belong
in workflow source. Runner SSH files are removed after success or failure.

## 3. Run a deployment

1. Open Actions -> **Deploy EC2 service** -> **Run workflow**.
2. Choose the reviewed branch/ref and configured GitHub environment.
3. Choose `preview` (default) or `https`.
4. Leave **demo** checked for the demo launch. This enables
   PROJECT_PUBLIC_PREVIEW_ENABLED=true in the release's runtime configuration for
   **all viewers**, including anonymous visitors on your public HTTPS domain.
   Clean source PDFs can supply project cover images; eligible unreviewed gallery
   images and draft layout details are visible. Scan, integrity and hidden/removed
   record checks still apply. Missing files or unprocessed uploads still need repair.
   Approval records are unchanged. Uncheck **demo** for approved-media-only visibility;
   the workflow explicitly writes false, overriding an older runtime value.
5. Run the workflow and inspect both the build and deploy jobs.

Demo visibility is independent of the transport mode. When your domain is ready,
choose **https** with **demo** checked to retain the same demo content. Source
credentials/config remain unchanged; the visibility choice applies to that release.

The build job creates and uploads `aws-ec2-release`; the deploy job downloads the
artifact from that same run, verifies its SHA-256, transfers it, and invokes the
versioned target-side helper. No local image builds, manual image transfers, local
PowerShell deployment wrapper or interactive cloud login are needed for releases.
GitHub and the host serialize deployments. Separate environments using the same
host still share the target-side lock. Re-running a commit creates a fresh release
directory; it does not overwrite an earlier release's config.

Before replacing containers, deployment checks the persistent mount, media folders,
RAM, runtime config, archive checksum and pending migrations. Pending migrations
stop deployment without applying schema changes. Arrange a verified database
snapshot and separately reviewed migration procedure before trying that release
again. The workflow never flushes, reimports or replaces the database.

After startup, readiness, Buyer, Builder login and Admin login must pass through the
local gateway. HTTPS mode also checks public database readiness. Only successful
releases update `~/realestate-current`. The worker is kept off; new uploads queue
until processing/scanning is separately configured.

## 4. Open the result

In HTTPS mode, open your configured domain and its `/builder/login` and `/admin/`
paths. In preview mode, forward your local port to the server's loopback gateway
using your normal SSH client. Example placeholders only:

```text
ssh -N -o StrictHostKeyChecking=yes -i <key-path> -L 127.0.0.1:18080:127.0.0.1:8080 <ssh-user>@<host>
```

Then open `http://localhost:18080/`, `/builder/login`, or `/admin/`. The preview
keeps DEBUG off, disables HTTPS redirect only in its override, and trusts only the
localhost tunnel origins. Existing application credentials are unchanged. Public
HTTP login is not enabled. Keep the SSH tunnel open while reviewing.

## Failure and rollback

Read the failed Actions step before retrying. Failed startup can partially replace
running containers; the workflow does not promise automatic rollback. To roll back,
run the workflow for the previous reviewed commit and select the same environment
and mode. Each run rebuilds dependencies; inspect and retest rollback artifacts.
Do not downgrade code across incompatible database migrations. Never restore an old
database over newer writes or delete volumes as a rollback shortcut.

## Local checks and retained helpers

`Test-Ec2Configuration.ps1`, `test-package.sh`, and the load-scenario self-check run
offline and do not authenticate to clouds. `package.sh` and `deploy-release.sh`
are the build/target helpers used by Actions. The former `start.sh` and local
`Deploy-Ec2Release.ps1` orchestration are superseded by the workflow/helper.

Application/gateway builds and smoke tests must be checked in Actions before a
release is called successful. This documentation does not claim that a workflow
has been pushed or dispatched. Historical deployment-specific machine/account
records are intentionally excluded from tracked files.
