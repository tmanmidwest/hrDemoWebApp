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
chmod +x setup.sh deploy.sh manage.sh update.sh teardown.sh fix-image.sh restore-state.sh

# 1. Check all prerequisites are in place
./setup.sh

# 2. Deploy — takes about 10 minutes, prints your app URL when done
./deploy.sh
```

That's it. The scripts pull the app source from GitHub, build it, push it to
your own ECR, and deploy it to Fargate — everything in your own AWS account.

---

## Enabling HTTPS on a custom domain (optional)

By default the app is served over plain HTTP at the generated ALB DNS name.
You can instead serve it over **HTTPS on your own domain** (e.g.
`testhr.trevorcombs.com`) — recommended if you'll integrate OAuth later.

When enabled, `deploy.sh` provisions a **free AWS-managed (ACM) TLS certificate**,
adds an HTTPS:443 listener to the load balancer, and redirects HTTP→HTTPS. The
app container is unchanged — the load balancer terminates TLS.

It's strictly opt-in. `deploy.sh` asks during the run, or you can pre-set it:

```bash
ENABLE_HTTPS=true DOMAIN_NAME=testhr.trevorcombs.com ./deploy.sh
```

**You'll add two CNAME records in Cloudflare during the deploy:**

1. **Certificate validation** — the script prints a CNAME (name + target). Add it
   in Cloudflare as **DNS only (grey cloud)**. The script waits until ACM validates
   the certificate (usually 2–5 minutes). Leave this record in place permanently so
   the certificate auto-renews.
2. **The app record** — at the end, the script prints a CNAME pointing your domain
   at the load balancer's DNS name. Add it (DNS only to start).

Once that second record resolves, the app is live at `https://<your-domain>/`.

> If the validation CNAME hasn't propagated in time, the script exits cleanly —
> just re-run `./deploy.sh` once it has. It reuses the same certificate and
> continues where it left off.

**Cloudflare proxy (orange cloud), later:** you can switch the app record to the
orange-cloud proxy and set **SSL/TLS → Full (strict)** in Cloudflare. The ACM
certificate keeps the Cloudflare→ALB hop valid, and you get Cloudflare's WAF/CDN
in front. DNS-only is fine to start.

**Teardown** removes the certificate along with everything else. You'll need to
delete the two leftover CNAME records from Cloudflare yourself (the script reminds
you).

---

## Pushing an update

When changes have been merged to the main branch on GitHub and you want to
deploy them live:

```bash
./update.sh
```

Shows you the exact commit being deployed, asks for confirmation, then
rebuilds the image and redeploys automatically. It also pins
`HRSOT_PUBLIC_BASE_URL` to this instance's public URL (your HTTPS domain, or the
ALB DNS name) so OIDC/OAuth single sign-on redirect URIs match what's registered
at your identity provider.

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

## Running more than one instance

At deploy time `deploy.sh` asks for an **instance name** (default `hr-demo`).
Each name is a fully isolated stack — its own load balancer, EFS storage,
container, certificate, and URL — so you can run several in the same AWS account
(e.g. a `hr-demo` POC instance and a `perm-test` instance for OAuth).

```bash
./deploy.sh                      # prompts for the instance name
INSTANCE=perm-test ./deploy.sh   # or set it up front (skips the prompt)
```

Every management script discovers all instances automatically:

- If there's only one, it just uses it (unchanged from before).
- If there are several, it lists them and asks which one.
- Set `INSTANCE=<name>` to pick one non-interactively, e.g.
  `INSTANCE=perm-test ./manage.sh status` or `INSTANCE=perm-test ./teardown.sh`.

> Each running instance has its own load balancer (~$16/month), so tear down the
> ones you aren't using.

---

## Managing from a second machine

The management scripts (`manage.sh`, `update.sh`, `teardown.sh`) read a local
state file that `deploy.sh` writes on the machine you deployed from (named
`.hr-demo-state` for the default instance, or `.hr-demo-state.<instance>` for
others). It holds the IDs of your AWS resources but is **not** synced anywhere,
so a second laptop won't have it — you'll see `No deployment found` if you try to
manage from there.

To manage an existing deployment from another machine, regenerate the file by
rediscovering your resources from AWS (this creates nothing — it's read-only).
`restore-state.sh` asks which instance name to restore (default `hr-demo`):

```bash
chmod +x restore-state.sh
./restore-state.sh                       # default region; prompts for instance
./restore-state.sh us-west-2             # or pass the region you deployed to
INSTANCE=perm-test ./restore-state.sh    # restore a specific instance, no prompt
```

Once it finishes you can run `./manage.sh status` (and the rest) normally.
The file contains only AWS resource IDs — no secrets — so copying it between
your own machines is also fine if you prefer.

---

## Remove everything

```bash
./teardown.sh
```

Deletes all AWS resources. Type `delete` to confirm.
Stops all charges. Data is permanently deleted.

---

## How instances are isolated

Every person runs `deploy.sh` against their own AWS account, and within an
account each **instance name** is its own isolated stack. Each deployment creates:
- Its own ECR repository (image built from the same public GitHub source)
- Its own ECS cluster, EFS filesystem, ALB, and security groups — all named after
  the instance, so multiple instances in one account never collide
- Its own state file tracking all resource IDs (`.hr-demo-state` for the default
  instance, `.hr-demo-state.<instance>` for others)

This state file lives only on the machine you deployed from. To operate the same
deployment from another machine, run `./restore-state.sh` there to rebuild it
(see *Managing from a second machine*).

Nobody shares infrastructure. Tearing down one instance has no effect on any other.

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
| `restore-state.sh` | Rebuild `.hr-demo-state` from AWS (e.g. on a second machine) |
| `teardown.sh` | Delete all AWS resources |
| `fix-image.sh` | One-time fix if the image fails to pull |
