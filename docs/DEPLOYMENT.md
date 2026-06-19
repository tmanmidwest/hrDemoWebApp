# Deployment

The app ships as a single Docker container that you **build from source** — there is no public prebuilt image to pull. Every deployment below either builds the image on the spot (local Docker, Docker Compose, Portainer) or has you push your own build to a container registry you control (AWS, Azure, Cloud Run, Kubernetes). See [Building and pushing to a registry](#building-and-pushing-to-a-registry) for the build/push steps the cloud targets reference.

## Persistent storage

In all deployments, the `/data` directory inside the container **must** be a persistent volume. It contains:

- `hrsot.db` — the SQLite database
- `jwt_signing_key` — OAuth JWT signing key (regenerated if missing)
- `INITIAL_CREDENTIALS.txt` — written on first startup, safe to delete after

If `/data` is ephemeral, every container restart wipes employees, credentials, and admin accounts.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `HRSOT_DATA_DIR` | `/data` | Persistent storage location |
| `HRSOT_SESSION_SECRET` | Auto-generated | Override for cookie signing key |
| `HRSOT_INITIAL_ADMIN_PASSWORD` | `N0nPr0dF0r$@viynt8` | Override seeded admin password |
| `HRSOT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `HRSOT_BIND_HOST` | `0.0.0.0` | Host to bind |
| `HRSOT_BIND_PORT` | `8000` | Port to bind |

None are required for a working deployment.

## Local Docker

Build the image from source, then run it:

```bash
git clone https://github.com/tmanmidwest/hrDemoWebApp.git
cd hrDemoWebApp
docker build -t demo-hr-sot:local .
docker run -d \
  --name demo-hr-sot \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  --restart unless-stopped \
  demo-hr-sot:local
```

Access at `http://localhost:8000`.

## Docker Compose

The included [`docker-compose.yml`](../docker-compose.yml) builds the image from source (`build: .`) — no image pull required:

```yaml
services:
  hr-sot:
    build: .
    image: demo-hr-sot:local
    container_name: demo-hr-sot
    ports:
      - "${HRSOT_HOST_PORT:-8000}:8000"
    volumes:
      - hrsot-data:/data
    environment:
      HRSOT_LOG_LEVEL: INFO
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  hrsot-data:
```

It uses a **named volume** (`hrsot-data`), not a host bind mount. The container runs as the non-root `hrsot` user (uid/gid 1000); a fresh named volume inherits the image's `/data` ownership, so the app can write its database and session secret. A bind mount (`./data:/data`) would be created root-owned on the host and fail with `PermissionError: [Errno 13] Permission denied: '/data/session_secret'`.

Run with:

```bash
git clone https://github.com/tmanmidwest/hrDemoWebApp.git
cd hrDemoWebApp
docker compose up -d --build
```

The `--build` flag rebuilds the image; on the first run Compose builds it automatically.

## Portainer

[Portainer](https://www.portainer.io/) is a web UI for managing Docker hosts and Swarm clusters. It's a convenient way to run this app on a home server, NAS (Synology/QNAP/Unraid), or any Docker host you administer through a browser. The recommended approach is a **Stack** (Portainer's name for a Compose project).

These steps assume Portainer is already installed and connected to the Docker environment where you want to run the app. The app is a single container with one persistent volume mounted at `/data`, so no special host configuration is required.

> **Build from source.** There is no prebuilt public image to pull — deploy by building the image from this repository on your Docker host (the **recommended** path below). Building from source also guarantees you're running current code with the latest migrations. If your organization mirrors the image into its own private registry, see [Using a prebuilt image](#using-a-prebuilt-image-optional) at the end of this section.

### Recommended — Stack built from this Git repository

Portainer clones the repo, builds the image from the included `Dockerfile`, and can redeploy when the repo changes.

1. In the Portainer sidebar, select your environment (e.g. **local**), then go to **Stacks → + Add stack**.
2. **Name** the stack, e.g. `demo-hr-sot`.
3. Under **Build method**, choose **Repository** and set:
   - **Repository URL**: `https://github.com/tmanmidwest/hrDemoWebApp`
   - **Repository reference**: `refs/heads/main`
   - **Compose path**: `docker-compose.yml`
4. *(Optional)* Enable **Automatic updates** (polling or webhook) so Portainer rebuilds and redeploys when `main` changes.
5. *(Optional)* Add environment variables under **Environment variables** — see the tables below.
6. Click **Deploy the stack**. The first deploy builds the image (a minute or two), then starts the container. When the health check goes green, browse to `http://<docker-host>:8000` (or your chosen `HRSOT_HOST_PORT`).

#### Changing the published port

The bundled compose publishes the host port via a variable: `"${HRSOT_HOST_PORT:-8000}:8000"`. If host port 8000 is already in use on your Docker host (a common cause of a `Bind for 0.0.0.0:8000 failed: port is already allocated` deploy error), add a stack **Environment variable**:

| Name | Value |
|---|---|
| `HRSOT_HOST_PORT` | `8080` |

The container still listens on 8000 internally (so the healthcheck is unaffected); only the host-side mapping changes. The app is then reachable at `http://<docker-host>:8080`. `HRSOT_HOST_PORT` is a Compose substitution variable — it has no effect unless the repo's `docker-compose.yml` references it, which it does on `main`.

The bundled [`docker-compose.yml`](../docker-compose.yml) uses `build: .`, tags the result `demo-hr-sot:local`, persists `/data` to a **named volume** (`hrsot-data`), and sets `restart: unless-stopped` with the `/health` healthcheck. The named volume is important: the container runs as the non-root `hrsot` user (uid/gid 1000), and a fresh named volume inherits the image's `/data` ownership so the app can write its database and session secret. A host bind mount would be created root-owned and fail with `PermissionError: [Errno 13] Permission denied: '/data/session_secret'`. Portainer creates and manages the `hrsot-data` volume for you; browse or back it up under **Volumes**.

> **Requires a buildable host.** This builds the image on the Docker engine Portainer manages, so that engine must support image builds — true for a standard standalone Docker host. Portainer **Edge agents** and some restricted/Swarm setups disable in-stack builds; on those, mirror the image into a registry and use [Using a prebuilt image](#using-a-prebuilt-image-optional).

#### Useful environment variables

| Variable | Why you'd set it |
|---|---|
| `HRSOT_INITIAL_ADMIN_PASSWORD` | Seed a non-default admin password on first start |
| `HRSOT_SESSION_SECRET` | Pin the cookie-signing key so sessions survive redeploys |
| `HRSOT_LOG_LEVEL` | Set to `DEBUG` while troubleshooting |

See the [Environment variables](#environment-variables) table above for the full list. None are required.

### Notes specific to Portainer

- **Persistence is non-negotiable.** The `/data` volume holds the SQLite DB, the OAuth signing key, and admin credentials. If you delete the stack *and* its volume, you lose everything — see [Persistent storage](#persistent-storage). Removing just the stack while keeping the volume preserves your data for the next deploy.
- **Run exactly one replica.** SQLite cannot tolerate concurrent writers. Don't scale the service or deploy it on a Swarm with `replicas > 1`.
- **Updating**: open the stack → **Pull and redeploy** (or **Update the stack**). Portainer re-pulls the repo, rebuilds the image, and recreates the container. Alembic migrations run automatically on startup and the mounted volume preserves your data.
- **Reverse proxy / TLS**: this app serves plain HTTP on port 8000. For anything beyond a LAN demo, front it with a reverse proxy (Nginx Proxy Manager, Traefik, Caddy) to add HTTPS, and consider not publishing port 8000 directly.
- **Finding the initial credentials**: open the container in Portainer → **Console → Connect** (`/bin/sh`), then `cat /data/INITIAL_CREDENTIALS.txt`. Or browse the data volume's contents from **Volumes**.

After the stack is up and healthy, follow the [First-time setup](#first-time-setup-via-the-web-ui) steps below.

### Using a prebuilt image (optional)

Only relevant if you've built the image yourself and pushed it to a registry your Docker host can reach (e.g. a private GHCR/ECR/Harbor). There is **no public image** to pull, so this path requires either a registry you control or Portainer **registry credentials** configured for a private one.

Use **Stacks → + Add stack → Web editor** (web-editor stacks have no build context, so they can only *pull* an image, not build one) and paste:

```yaml
services:
  hr-sot:
    image: <your-registry>/demo-hr-sot:latest   # an image you built and pushed
    container_name: demo-hr-sot
    ports:
      - "8000:8000"
    volumes:
      - hrsot-data:/data
    environment:
      HRSOT_LOG_LEVEL: INFO
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  hrsot-data:
```

The named volume (`hrsot-data`) is Portainer-managed and browsable under **Volumes**; the container runs as uid/gid `1000` and named volumes inherit the right ownership automatically. To build and push the image first, see [Building from source](#building-from-source).

## First-time setup via the web UI

Once the container is running and healthy (the `/health` endpoint returns 200), do the following the first time you stand up a new instance:

1. **Open the app**. Visit `http://localhost:8000` (or your deployed hostname). The root URL redirects to `/ui/employees`, which redirects to `/ui/login` since you're not signed in yet.

2. **Log in as the seeded admin**. Username `robbytheadmin`, password `N0nPr0dF0r$@viynt8`. The full credentials are also written to `/data/INITIAL_CREDENTIALS.txt` inside the container on first startup, in case you need to recover them.

3. **Change the seeded admin's password**. Settings → Admin Users → click **Change Password** next to `robbytheadmin`. Use something the rest of your team can share, or your own password if it's a single-user demo instance.

4. *(Optional, recommended)* **Create a personal admin account**. Settings → Admin Users → **Add Admin**. Sign out, sign back in as the new account. The seeded `robbytheadmin` can still be used by the reset script for password recovery (see [SECURITY.md](SECURITY.md)).

5. **Create the credentials your integrating system will use**:
   - For Saviynt-style API key auth: Settings → API Keys → **Create New API Key**. Name it after the consuming system. Copy the displayed `hrsot_...` value — it is shown in full **only once**.
   - For OAuth 2.0: Settings → OAuth Clients → **Create New OAuth Client**. Copy the displayed `client_id` and `client_secret` — secret is shown once.

6. **Hand the credentials and base URL to whoever is configuring the integration**. For Saviynt specifically, see [SAVIYNT_INTEGRATION.md](SAVIYNT_INTEGRATION.md).

The app is now ready to serve as an HR data source. Employees, lookups, and credentials can all be managed via the UI or REST API.

## AWS ECS Fargate

The recommended way to deploy to AWS ECS Fargate is using the included shell scripts in [`docs/fargate/`](fargate/). They handle everything automatically — no AWS console required, no manual JSON task definitions, no kubectl.

### Quick start

```bash
cd docs/fargate
chmod +x *.sh
./setup.sh    # checks all prerequisites and AWS permissions
./deploy.sh   # deploys the full stack to your AWS account (~10 minutes)
```

When `deploy.sh` completes it prints your app URL. That's the base URL to hand to Saviynt or any other IGA connector.

### What the scripts create in your AWS account

| Resource | Purpose |
|---|---|
| ECR repository | Stores your container image (built from this repo's source) |
| ECS Cluster | Runs your Fargate tasks |
| EFS Filesystem | Persistent storage for the SQLite `/data` directory |
| Application Load Balancer | Public HTTP endpoint |
| Security Groups | Network access control |
| CloudWatch Log Group | Container log storage |

Everything is isolated to your own AWS account. Each person on your team deploys their own independent instance by running the same scripts against their own account.

### Managing your deployment

```bash
./manage.sh status     # check if it's running and get the URL
./manage.sh stop       # pause the app (data kept, Fargate charges stop)
./manage.sh start      # resume after stopping
./manage.sh logs       # stream live container logs
```

### Deploying an update

After merging changes to the `main` branch:

```bash
./update.sh
```

This pulls the latest source from GitHub, rebuilds the image, pushes it to ECR, and redeploys ECS — all automatically.

### Tearing down

```bash
./teardown.sh
```

Deletes all AWS resources. Prompts for confirmation before doing anything.

### Cost

Running continuously in `us-east-1`:

| Resource | Approx. monthly cost |
|---|---|
| ECS Fargate (0.25 vCPU / 0.5 GB) | ~$9 |
| Application Load Balancer | ~$16 |
| EFS + CloudWatch | ~$1 |
| **Total** | **~$26/month** |

Run `./manage.sh stop` when not in use to eliminate Fargate compute charges. Run `./teardown.sh` to stop all charges entirely.

For full details see [`docs/fargate/README.md`](fargate/README.md).

### Manual deployment reference

If you prefer to deploy manually or integrate with an existing AWS setup, the key configuration points are:

- **Image**: build from source (`docker buildx build --platform linux/amd64`) and push to ECR — there is no public image to pull; the scripted deploy does this for you
- **CPU/Memory**: 256 CPU units / 512 MB is sufficient; the original docs suggest 512/1024 which also works
- **Volume**: EFS with an access point owned by uid/gid 1000 mounted at `/data`
- **Health check**: `curl -f http://localhost:8000/health || exit 1`
- **Replicas**: always 1 — SQLite cannot handle concurrent writers

## Azure Container Apps

First [build and push](#building-and-pushing-to-a-registry) the image to an Azure Container Registry (or any registry the Container App can pull), then reference it:

```bash
az containerapp create \
  --name demo-hr-sot \
  --resource-group <rg> \
  --environment <env> \
  --image <your-registry>/demo-hr-sot:latest \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 0.5 --memory 1.0Gi
```

Attach an Azure Files share via the Container Apps storage configuration and mount it at `/data`.

**Important**: Keep `min-replicas` and `max-replicas` both at 1. Container Apps' default scaling rules will create multiple replicas, and SQLite cannot tolerate this.

## Google Cloud Run

First [build and push](#building-and-pushing-to-a-registry) the image to Google Artifact Registry (or any registry Cloud Run can pull), then reference it:

```bash
gcloud run deploy demo-hr-sot \
  --image <your-registry>/demo-hr-sot:latest \
  --port 8000 \
  --min-instances 1 \
  --max-instances 1 \
  --add-volume name=data,type=cloud-storage,bucket=<bucket> \
  --add-volume-mount volume=data,mount-path=/data
```

Cloud Run with the second-generation execution environment supports volume mounts. Same single-replica caveat as Azure.

## Kubernetes

First [build and push](#building-and-pushing-to-a-registry) the image to a registry your cluster can pull from, then reference it in the manifest below (replace `<your-registry>/demo-hr-sot:latest`).

Manifest in `deploy/k8s/`:

- `Deployment` with `replicas: 1` and `strategy.type: Recreate` (not RollingUpdate, which would briefly run two pods with the same SQLite file)
- `Service` of type `ClusterIP`
- `Ingress` with TLS
- `PersistentVolumeClaim` for `/data` (`ReadWriteOnce` is fine since we run a single replica)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-hr-sot
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: demo-hr-sot
  template:
    metadata:
      labels:
        app: demo-hr-sot
    spec:
      containers:
      - name: hr-sot
        image: <your-registry>/demo-hr-sot:latest
        ports:
        - containerPort: 8000
        volumeMounts:
        - name: data
          mountPath: /data
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 1Gi
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: demo-hr-sot-data
```

## Building and pushing to a registry

For local use, build and run directly (see [Local Docker](#local-docker)). For the cloud targets above (AWS, Azure, Cloud Run, Kubernetes), build the image and push it to a container registry your platform can pull from:

```bash
git clone https://github.com/tmanmidwest/hrDemoWebApp.git
cd hrDemoWebApp

# Build for the architecture your target runs (linux/amd64 for most clouds)
docker buildx build --platform linux/amd64 -t <your-registry>/demo-hr-sot:latest .

# Authenticate to your registry (e.g. `aws ecr get-login-password ... | docker login ...`), then:
docker push <your-registry>/demo-hr-sot:latest
```

`<your-registry>` is something you control — Amazon ECR, Azure Container Registry, Google Artifact Registry, a private GHCR namespace, Harbor, etc. There is no public prebuilt image to pull. The AWS Fargate scripts in [`docs/fargate/`](fargate/) automate this build-and-push step for ECR.

## Verifying a deployment

After the container starts, verify:

```bash
curl http://<host>:8000/health
# Expected: {"status": "ok", "database": "ok"}

curl -I http://<host>:8000/docs
# Expected: 200 OK
```

Read the initial credentials file to confirm seeding worked:

```bash
docker exec demo-hr-sot cat /data/INITIAL_CREDENTIALS.txt
```

## Backups

Backing up = copying `hrsot.db`:

```bash
docker exec demo-hr-sot sqlite3 /data/hrsot.db ".backup /data/hrsot_backup.db"
docker cp demo-hr-sot:/data/hrsot_backup.db ./hrsot_backup_$(date +%Y%m%d).db
```

Restore = stop container, replace the file, start container.

## Upgrading

Pull the latest source and rebuild. For a local Docker run:

```bash
git pull
docker build -t demo-hr-sot:local .
docker stop demo-hr-sot && docker rm demo-hr-sot
docker run -d --name demo-hr-sot -p 8000:8000 -v $(pwd)/data:/data \
  --restart unless-stopped demo-hr-sot:local
```

For Docker Compose: `git pull && docker compose up -d --build`. For Portainer: **Pull and redeploy** the stack. For cloud targets: rebuild and push a new image (see [Building and pushing to a registry](#building-and-pushing-to-a-registry)), then roll the service.

Alembic migrations run automatically on container startup. The mounted volume preserves all data.
