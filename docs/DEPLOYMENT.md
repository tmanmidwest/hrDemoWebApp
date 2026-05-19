# Deployment

The app ships as a single Docker container. The image is published to GitHub Container Registry at `ghcr.io/tmanmidwest/hrwebapp`.

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
  ghcr.io/tmanmidwest/hrwebapp:latest
```

Access at `http://localhost:8000`.

## Docker Compose

`docker-compose.yml` is included in the repo:

```yaml
services:
  hr-sot:
    image: ghcr.io/tmanmidwest/hrwebapp:latest
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

## AWS ECS Fargate

**Prerequisites**:
- VPC with public and private subnets
- EFS filesystem for persistent storage
- ECR or use the public image directly
- Application Load Balancer with HTTPS listener (ACM cert)

**Task definition essentials**:

```json
{
  "family": "demo-hr-sot",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "containerDefinitions": [{
    "name": "hr-sot",
    "image": "ghcr.io/tmanmidwest/hrwebapp:latest",
    "essential": true,
    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
    "mountPoints": [{"sourceVolume": "data", "containerPath": "/data"}],
    "healthCheck": {
      "command": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
      "interval": 30,
      "timeout": 5,
      "retries": 3
    }
  }],
  "volumes": [{
    "name": "data",
    "efsVolumeConfiguration": {
      "fileSystemId": "fs-xxxxx",
      "rootDirectory": "/demo-hr-sot"
    }
  }]
}
```

**Service**: 1 desired task, attached to a target group on the ALB. Do not scale beyond 1 — SQLite cannot handle concurrent writers from multiple containers.

## Azure Container Apps

```bash
az containerapp create \
  --name demo-hr-sot \
  --resource-group <rg> \
  --environment <env> \
  --image ghcr.io/tmanmidwest/hrwebapp:latest \
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
  --image ghcr.io/tmanmidwest/hrwebapp:latest \
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
        image: ghcr.io/tmanmidwest/hrwebapp:latest
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
git clone https://github.com/tmanmidwest/hrWebApp.git
cd hrWebApp
docker build -t demo-hr-sot:local .
docker run -d -p 8000:8000 -v $(pwd)/data:/data demo-hr-sot:local
```

## Verifying a deployment

After the container starts, verify:

```bash
# Health check
curl http://<host>:8000/health
# Expected: {"status": "ok", "database": "ok"}

# Swagger UI accessible
curl -I http://<host>:8000/docs
# Expected: 200 OK

# Log in to the web UI
# Username: robbytheadmin
# Password: N0nPr0dF0r$@viynt8
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
docker pull ghcr.io/tmanmidwest/hrwebapp:latest
docker stop demo-hr-sot
docker rm demo-hr-sot
# Re-run with same volume mount
docker run -d --name demo-hr-sot -p 8000:8000 -v $(pwd)/data:/data \
  ghcr.io/tmanmidwest/hrwebapp:latest
```

Alembic migrations run automatically on container startup. The mounted volume preserves all data.
