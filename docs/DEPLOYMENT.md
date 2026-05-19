# Deployment

The app ships as a single Docker container. The image is published to GitHub Container Registry at `ghcr.io/tmanmidwest/hrdemowebapp`.

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

```bash
docker run -d \
  --name demo-hr-sot \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  --restart unless-stopped \
  ghcr.io/tmanmidwest/hrdemowebapp:latest
```

Access at `http://localhost:8000`.

## Docker Compose

`docker-compose.yml` is included in the repo:

```yaml
services:
  hr-sot:
    image: ghcr.io/tmanmidwest/hrdemowebapp:latest
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    restart: unless-stopped
```

Run with:

```bash
docker compose up -d
```

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

- **Image**: build from source (`docker buildx build --platform linux/amd64`) and push to ECR — the public GHCR image requires authentication and is not suitable for Fargate pulls
- **CPU/Memory**: 256 CPU units / 512 MB is sufficient; the original docs suggest 512/1024 which also works
- **Volume**: EFS with an access point owned by uid/gid 1000 mounted at `/data`
- **Health check**: `curl -f http://localhost:8000/health || exit 1`
- **Replicas**: always 1 — SQLite cannot handle concurrent writers

## Azure Container Apps

```bash
az containerapp create \
  --name demo-hr-sot \
  --resource-group <rg> \
  --environment <env> \
  --image ghcr.io/tmanmidwest/hrdemowebapp:latest \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 0.5 --memory 1.0Gi
```

Attach an Azure Files share via the Container Apps storage configuration and mount it at `/data`.

**Important**: Keep `min-replicas` and `max-replicas` both at 1. Container Apps' default scaling rules will create multiple replicas, and SQLite cannot tolerate this.

## Google Cloud Run

```bash
gcloud run deploy demo-hr-sot \
  --image ghcr.io/tmanmidwest/hrdemowebapp:latest \
  --port 8000 \
  --min-instances 1 \
  --max-instances 1 \
  --add-volume name=data,type=cloud-storage,bucket=<bucket> \
  --add-volume-mount volume=data,mount-path=/data
```

Cloud Run with the second-generation execution environment supports volume mounts. Same single-replica caveat as Azure.

## Kubernetes

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
        image: ghcr.io/tmanmidwest/hrdemowebapp:latest
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

## Building from source

```bash
git clone https://github.com/tmanmidwest/hrDemoWebApp.git
cd hrDemoWebApp
docker build -t demo-hr-sot:local .
docker run -d -p 8000:8000 -v $(pwd)/data:/data demo-hr-sot:local
```

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

```bash
docker pull ghcr.io/tmanmidwest/hrdemowebapp:latest
docker stop demo-hr-sot
docker rm demo-hr-sot
docker run -d --name demo-hr-sot -p 8000:8000 -v $(pwd)/data:/data \
  ghcr.io/tmanmidwest/hrdemowebapp:latest
```

Alembic migrations run automatically on container startup. The mounted volume preserves all data.
