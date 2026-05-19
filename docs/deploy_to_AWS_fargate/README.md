# hrDemoWebApp — AWS ECS Fargate Scripts

Deploy, manage, update, and teardown the hrDemoWebApp on your own AWS account.
Each person runs these scripts against their own AWS account — fully isolated instances.

---

## What you need

- **AWS account** with permissions for ECS, ECR, EFS, EC2, ELB, and IAM
- **AWS CLI v2** — https://aws.amazon.com/cli/
- **Docker Desktop** — https://www.docker.com/products/docker-desktop/
- **Git** — on Mac run `xcode-select --install`

---

## Quick start (new deployment)

```bash
# Make all scripts executable (one time only)
chmod +x setup.sh deploy.sh manage.sh update.sh teardown.sh fix-image.sh

# 1. Check all prerequisites are in place
./setup.sh

# 2. Deploy — takes about 10 minutes, prints your app URL when done
./deploy.sh
```

That's it. The scripts pull the app source from GitHub, build it, push it to
your own ECR, and deploy it to Fargate — everything in your own AWS account.

---

## Pushing an update

When changes have been merged to the main branch on GitHub and you want to
deploy them live:

```bash
./update.sh
```

Shows you the exact commit being deployed, asks for confirmation, then
rebuilds the image and redeploys automatically.

---

## Day-to-day management

```bash
./manage.sh status     # Is it running? What's the URL?
./manage.sh stop       # Pause the app — data kept, Fargate charges stop
./manage.sh start      # Resume after stopping
./manage.sh restart    # Restart without a code change
./manage.sh logs       # Stream live logs (Ctrl+C to stop)
./manage.sh url        # Print the app URL
```

---

## Remove everything

```bash
./teardown.sh
```

Deletes all AWS resources. Type `delete` to confirm.
Stops all charges. Data is permanently deleted.

---

## How instances are isolated

Every person runs `deploy.sh` against their own AWS account. Each deployment creates:
- Its own ECR repository (image built from the same public GitHub source)
- Its own ECS cluster, EFS filesystem, ALB, and security groups
- Its own `.hr-demo-state` file tracking all resource IDs

Nobody shares infrastructure. Tearing down your instance has no effect on anyone else's.

---

## Default login

| Field | Value |
|---|---|
| Username | `robbytheadmin` |
| Password | `N0nPr0dF0r$@viynt8` |

Change the password after first login via **Settings → Admin Users → Change Password**.

---

## Script reference

| Script | Purpose |
|---|---|
| `setup.sh` | Check all prerequisites before deploying |
| `deploy.sh` | Full deployment from scratch (~10 min) |
| `update.sh` | Rebuild and redeploy from latest GitHub source |
| `manage.sh` | Stop, start, restart, logs, status |
| `teardown.sh` | Delete all AWS resources |
| `fix-image.sh` | One-time fix if the image fails to pull |
