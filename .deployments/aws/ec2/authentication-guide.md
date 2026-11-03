# Connect GitHub Actions to EC2

This guide covers deployment authentication for the [Deploy EC2 service workflow](../../../.github/workflows/aws-ec2-release.yml). GitHub initiates an SSH connection to the EC2 host and transfers the release with SCP. The workflow does not call AWS APIs and does not need AWS access keys. Application login credentials are separate and are unchanged by a release.

Buyer, Builder and the custom React Admin console authenticate through their Django service APIs. SSH setup does not grant application roles, Builder membership or review permission; manage those through the authorized application access controls described in [current workflows](../../../WORKFLOWS.md).

For host preparation, runtime configuration, preview access and rollback, use the [release guide](README.md). Deploy application releases through the reviewed GitHub Actions workflow.

A connection failure such as:

```text
ssh: connect to host ... port 22: Connection timed out
scp: Connection closed
```

occurs before authentication. First restore network access, then verify SSH credentials. A timeout alone does not prove that the key is wrong. See [AWS connection troubleshooting](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/TroubleshootingInstancesConnecting.html).

All angle-bracket values below are placeholders. Keep real addresses, host keys and credentials in GitHub environment configuration or protected local files.

## 1. Restore the runner's network access

In the AWS console, open **EC2 → Instances**, select the intended instance, and verify:

1. The instance is running and its status checks pass.
2. GitHub's `EC2_HOST` secret contains its current reachable IPv4 address or hostname with a reachable IPv4 record. The workflow forces IPv4 for SSH/SCP. A public runner cannot directly reach a private VPC address.
3. For a public IPv4 connection, the instance has a public address and its subnet has a route to an internet gateway. A NAT gateway alone does not provide inbound SSH access.
4. Under **Security → Security groups → Inbound rules**, TCP port `SSH_PORT` (default `22`) is allowed from the deployment runner's source address. An existing rule for your laptop's IP does not allow a GitHub-hosted runner.
5. Network ACLs allow the SSH request and return traffic; the host firewall and SSH service also allow the configured port.

AWS documents SSH source restrictions in its [security group rules reference](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/security-group-rules-reference.html).

### Temporary demo access with a GitHub-hosted runner

Use a reviewed ref containing **Show deployment runner SSH source** and SSH connection retries. Re-running an older job uses its original workflow; start a new run to use workflow changes.

1. Open **Actions → Deploy EC2 service → Run workflow**. Choose the reviewed ref, environment, deployment mode and demo visibility as described in step 5. The build job must pass before the deploy job starts. This starts a real deployment once connectivity succeeds.
2. Watch the deploy job's **Show deployment runner SSH source** step for its printed IPv4 address.
3. While that same job is retrying, use the AWS console to add a temporary inbound rule to the instance's attached security group: **TCP / `<SSH_PORT>` / `<RUNNER_IPV4>/32`** (use the SSH preset for port 22). Preserve your operator access rule.
4. After the job succeeds or fails, remove that temporary runner rule.

Do not use **My IP** for this rule: that selects your browser's source address. Do not open SSH to `0.0.0.0/0`. A new job can have a different runner address. If the retries expire, start a new run and use its new address.

### Repeatable deployments

Use a trusted Linux self-hosted runner with a network route to EC2, or a runner setup with controlled fixed egress. Restrict SSH to that source. For a self-hosted runner, set the GitHub environment variable `DEPLOY_RUNNER` to a unique label assigned to that runner. Use a separate trusted runner host, keep it restricted to trusted workflow code, and install the tooling required by the deploy job.

The existing workflow accepts one runner label and prints the source address only for GitHub-hosted runners. A self-hosted runner needs Git, Bash, Docker, curl, OpenSSH (`ssh` and `scp`), and SHA-256 utilities for the deploy job. Its selected label must identify the intended trusted Linux runner. Standard GitHub-hosted runner addresses should not be treated as permanent allowlist entries.

## 2. Prepare an SSH deployment key

Use a dedicated key for deployments. The private key goes into GitHub; its public key goes into the target Linux user's `authorized_keys`. Creating an EC2 key pair in the AWS console does not automatically install it on an already running instance. See [AWS instructions for adding a public key](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/replacing-key-pair.html).

On your trusted workstation, generate a key outside this repository:

```text
ssh-keygen -t ed25519 -C github-actions-deploy -f <PRIVATE_KEY_PATH>
```

The current noninteractive workflow does not unlock passphrase-protected keys. For this workflow, leave the passphrase empty and protect the resulting private key and GitHub environment access.

Using an existing trusted administrative connection, sign in as the intended deployment user. Run these Linux commands as that user, replacing the public-key placeholder with the complete single-line contents of the generated `.pub` file:

```sh
mkdir -p ~/.ssh
chmod 700 ~/.ssh
printf '%s\n' '<PUBLIC_KEY_LINE>' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Append the key; preserve existing operator keys. Make sure the files belong to the deployment user. The workflow defaults to `ec2-user`; Ubuntu images commonly use `ubuntu`. This user needs Docker access and write access to its home directory and release/configuration folders. The workflow uploads into that user's home directory and reads protected runtime configuration from `~/ec2`.

Complete the host setup in the [release guide](README.md) before dispatching: Linux x86-64, at least 4 GiB RAM, sufficient disk space, Docker Engine, Compose >=2.30, Python 3, curl, OpenSSH and the persistent NFS workspace. Place `.env`, `app.env` and `global-bundle.pem` in `~/ec2`; restrict the directory to its owner and both environment files to mode 0600. Start from the [Compose environment example](.env.example) and [application environment example](app.env.example), replacing example values. Database credentials and the Django secret remain on the host; they are not deployment secrets in GitHub.

If using an existing EC2 key instead, use the matching original private key. AWS cannot return a previously downloaded private key.

## 3. Verify the EC2 host key

Host keys identify the server; deployment keys identify the client. Through an independently trusted connection or console session, read the server's public host key:

```sh
cat /etc/ssh/ssh_host_ed25519_key.pub
```

Build the `SSH_KNOWN_HOSTS` value using its key type and base64 key data:

```text
<EC2_HOST> ssh-ed25519 <VERIFIED_HOST_PUBLIC_KEY_BASE64>
```

For a nonstandard port:

```text
[<EC2_HOST>]:<SSH_PORT> ssh-ed25519 <VERIFIED_HOST_PUBLIC_KEY_BASE64>
```

The hostname/address must match `EC2_HOST` exactly. Do not paste a fingerprint or a private host key. Do not disable host-key checking or trust an unverified `ssh-keyscan` result.

## 4. Add the GitHub environment secrets

Commit and push the reviewed workflow changes. GitHub requires a workflow with `workflow_dispatch` on the default branch before manual dispatch is available; choose the intended reviewed ref when running it.

Open the repository's **Settings → Environments**, create or select `production` (or your chosen environment), and configure deployment reviewers and allowed branches as appropriate. Add these values under **Environment secrets**:

| Secret | Contents |
| --- | --- |
| `EC2_HOST` | Reachable IPv4 address or hostname, without `https://`, username, port, path or whitespace |
| `SSH_PRIVATE_KEY` | Complete private key, including BEGIN/END lines and actual line breaks; not the `.pub` file or a filename |
| `SSH_KNOWN_HOSTS` | Verified OpenSSH host entry from step 3 |

Under **Environment variables**, add overrides only when needed:

| Variable | Default | When to change it |
| --- | --- | --- |
| `SSH_USER` | `ec2-user` | The target Linux user is different |
| `SSH_PORT` | `22` | SSH listens on another port |
| `DEPLOY_RUNNER` | `ubuntu-latest` | Use a trusted reachable runner label |

Select this same environment when dispatching the workflow. `SSH_USER` must match `^[a-z_][a-z0-9_-]*$`; `SSH_PORT` must be an integer from 1 to 65535. These exact names are already wired into the workflow. GitHub explains environment secret configuration in [Using secrets in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

The workflow uses noninteractive SSH with strict host-key checking. It removes its temporary private-key and known-host files after success or failure. Removing the temporary security-group rule remains an operator task.

## 5. Run and interpret the result

Start a new workflow run on the reviewed ref and select:

| Input | Default | Effect |
| --- | --- | --- |
| `environment` | `production` | Selects the GitHub environment containing deployment secrets and variables |
| `mode` | `preview` | `preview` exposes the gateway on host loopback for an SSH tunnel; `https` starts public HTTPS and requires DNS, ports 80/443 and matching runtime hosts/origins |
| `demo` | `true` | Writes `PROJECT_PUBLIC_PREVIEW_ENABLED=true` in that release's runtime configuration, exposing eligible clean unreviewed media and draft details to all viewers, including anonymous HTTPS visitors |

Demo visibility is independent of transport mode and does not approve records. Uncheck **demo** for approved-media-only visibility; the workflow explicitly writes `false`, overriding an older runtime value. Processing remains off in both modes, so new uploads queue until processing/scanning is separately configured.

Confirm that both jobs pass:

1. **Build:** validates workflow/configuration and shell scripts, builds Linux images, checks all three services against disposable PostgreSQL, validates the gateway and HTTPS configuration, and uploads the checksummed `aws-ec2-release` artifact.
2. **Deploy:** verifies the artifact checksum, transfers the archive and versioned helper over SSH/SCP, checks host runtime configuration, NFS, RAM and pending migrations, then starts the selected services. Pending migrations stop deployment; the workflow does not apply them.
3. **Health checks:** readiness, Buyer, Builder login and Admin login must respond through the loopback gateway. HTTPS mode also checks public readiness. These check page availability, not a credential-based sign-in. Only successful releases update `~/realestate-current`.

A local SSH test only proves connectivity from your workstation, not from the GitHub runner. SSH success alone does not mean the application deployment succeeded. Failed startup can partially replace running containers; there is no automatic rollback. Follow the [release guide](README.md#failure-and-rollback) before retrying or rolling back.

| Error | Next check |
| --- | --- |
| Input validation or checksum failure before transfer | Selected environment, required secrets, host/user/port format and same-run release artifact |
| `Connection timed out` | Address, runner source rule, routing, ACLs and firewall |
| `Connection refused` | SSH service and listening port |
| `Permission denied (publickey)` | Linux username, matching public/private key, ownership and permissions |
| `Load key ... invalid format` | Complete private-key content and preserved line breaks |
| `Host key verification failed` | Independently verify the server key and matching host/port entry |
| Target configuration, NFS, RAM or migration error | Follow the release guide; the SSH connection and remote command have succeeded. Pending migrations require a verified snapshot and separately reviewed migration procedure |
| Gateway or HTTPS health-check failure | Container logs, runtime configuration, database readiness, DNS and HTTPS reachability; use the release guide's failure/rollback procedure |

## When AWS IAM / OIDC is needed

If a future reviewed workflow must push to ECR, read S3 or invoke Systems Manager, configure GitHub OIDC to assume a narrowly scoped AWS IAM role. GitHub's [AWS OIDC guide](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws) describes the identity provider, audience `sts.amazonaws.com`, repository/environment trust restriction and `id-token: write` permission.

OIDC supplies AWS API credentials; it does not open port 22 or install an SSH key. It is unnecessary for the current SSH release workflow. Switching to an AWS API deployment mechanism requires a separately reviewed workflow change.

No AWS CLI commands are needed for these instructions. The console steps change SSH access rules, and dispatching the workflow deploys a release. Any agent-run `aws` or `az` commands require explicit command-specific permission under the repository deployment rules.
