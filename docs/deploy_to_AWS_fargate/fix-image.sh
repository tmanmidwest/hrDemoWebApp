#!/bin/bash
# =============================================================================
# fix-image.sh — Migrate container image from GHCR to Amazon ECR
# =============================================================================
# Fixes the 403 Forbidden error when Fargate tries to pull from GHCR.
# Run this once — afterwards deploy.sh will use ECR automatically.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

CHECKMARK="${GREEN}✔${NC}"
ARROW="${BLUE}▶${NC}"
WARNING="${YELLOW}⚠${NC}"

log()     { echo -e "${ARROW}  $1"; }
success() { echo -e "${CHECKMARK}  $1"; }
warn()    { echo -e "${WARNING}  ${YELLOW}$1${NC}"; }
error()   { echo -e "${RED}✖  ERROR: $1${NC}" >&2; exit 1; }
header()  { echo -e "\n${BOLD}${BLUE}── $1 ${NC}"; }
skip()    { echo -e "  ${YELLOW}↷  Skipping: $1${NC}"; }

# Idempotently ensure a CloudWatch log group exists. Fails loudly if it can't
# be created — a missing log group makes ECS tasks fail to start (the awslogs
# driver does NOT auto-create the group), which is hard to diagnose otherwise.
ensure_log_group() {
  local group="$1"

  if aws logs describe-log-groups \
      --log-group-name-prefix "$group" \
      --region "$REGION" \
      --query "logGroups[?logGroupName=='${group}'] | [0].logGroupName" \
      --output text 2>/dev/null | grep -qx "$group"; then
    return 0
  fi

  local create_err
  if ! create_err=$(aws logs create-log-group \
      --log-group-name "$group" \
      --region "$REGION" 2>&1); then
    if echo "$create_err" | grep -q "ResourceAlreadyExistsException"; then
      return 0
    fi
    error "Could not create CloudWatch log group '$group': $create_err"
  fi

  aws logs describe-log-groups \
    --log-group-name-prefix "$group" \
    --region "$REGION" \
    --query "logGroups[?logGroupName=='${group}'] | [0].logGroupName" \
    --output text 2>/dev/null | grep -qx "$group" \
    || error "Log group '$group' still does not exist after creation attempt."
}

# ── AWS SESSION VALIDATION ────────────────────────────────────────────────────
header "Validating AWS session"

CALLER=$(aws sts get-caller-identity --output json 2>/dev/null) \
  || error "Not logged in to AWS. Run 'aws configure' or refresh your session and try again."

SESSION_ACCOUNT=$(echo "$CALLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['Account'])")
SESSION_USER=$(echo "$CALLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['Arn'].split('/')[-1])")
success "Logged in as: $SESSION_USER (Account: $SESSION_ACCOUNT)"

# ── SELECT DEPLOYMENT INSTANCE ─────────────────────────────────────────────────
# Find every instance's state file. Set INSTANCE=<name> to pick one directly;
# otherwise use the only one, or choose from a list when several exist.
shopt -s nullglob
STATE_FILES=( .hr-demo-state* )
shopt -u nullglob

[ "${#STATE_FILES[@]}" -gt 0 ] || error "No deployment found. Run deploy.sh first."

STATE_FILE=""
if [ -n "${INSTANCE:-}" ]; then
  for f in "${STATE_FILES[@]}"; do
    grep -q "^APP_NAME=${INSTANCE}$" "$f" && { STATE_FILE="$f"; break; }
  done
  [ -n "$STATE_FILE" ] || error "No deployment found for instance '${INSTANCE}'."
elif [ "${#STATE_FILES[@]}" -eq 1 ]; then
  STATE_FILE="${STATE_FILES[0]}"
else
  echo ""
  echo -e "  ${BOLD}Multiple deployments found — choose one:${NC}"
  i=1
  for f in "${STATE_FILES[@]}"; do
    nm=$(grep '^APP_NAME=' "$f" | cut -d= -f2)
    rg=$(grep '^REGION=' "$f" | cut -d= -f2)
    echo -e "    ${BOLD}${i})${NC} ${nm}  (${rg})"
    i=$((i + 1))
  done
  echo ""
  read -rp "  Select [1-${#STATE_FILES[@]}]: " sel
  { [[ "$sel" =~ ^[0-9]+$ ]] && [ "$sel" -ge 1 ] && [ "$sel" -le "${#STATE_FILES[@]}" ]; } \
    || error "Invalid selection."
  STATE_FILE="${STATE_FILES[$((sel - 1))]}"
fi

# shellcheck source=/dev/null
source "$STATE_FILE"

GHCR_IMAGE="ghcr.io/tmanmidwest/hrdemowebapp:latest"
ECR_REPO="${APP_NAME}-webapp"
ECR_IMAGE="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:latest"

# ── PRE-FLIGHT ────────────────────────────────────────────────────────────────
header "Pre-flight checks"

command -v docker >/dev/null 2>&1 || error "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
docker info >/dev/null 2>&1 || error "Docker is not running. Start Docker Desktop and try again."
success "Docker is running"

# ── ECR REPOSITORY ────────────────────────────────────────────────────────────
header "Amazon ECR repository"

log "Creating ECR repository (or reusing if it exists)..."
EXISTING=$(aws ecr describe-repositories \
  --repository-names "$ECR_REPO" \
  --region "$REGION" \
  --query 'repositories[0].repositoryUri' \
  --output text 2>/dev/null || echo "")

if [ -z "$EXISTING" ] || [ "$EXISTING" = "None" ]; then
  aws ecr create-repository \
    --repository-name "$ECR_REPO" \
    --region "$REGION" >/dev/null
fi
success "ECR repository: $ECR_IMAGE"

# ── PULL, TAG, PUSH ───────────────────────────────────────────────────────────
header "Copying image from GHCR to ECR"

log "Logging Docker into ECR..."
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin \
  "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" 2>/dev/null
success "Docker logged into ECR"

log "Cloning repo and building image locally (GHCR is private — building from source)..."
BUILD_DIR=$(mktemp -d)
git clone https://github.com/tmanmidwest/hrDemoWebApp.git "$BUILD_DIR" --depth 1 --quiet
docker buildx build --platform linux/amd64 --push -t "$ECR_IMAGE" "$BUILD_DIR"
rm -rf "$BUILD_DIR"
success "Image built: $ECR_IMAGE"

log "Pushing image to ECR (this may take a minute)..."
docker push "$ECR_IMAGE"
success "Image pushed to ECR: $ECR_IMAGE"

# ── UPDATE TASK DEFINITION ────────────────────────────────────────────────────
header "Updating ECS task definition"

# The task def points the awslogs driver at $LOG_GROUP. If that group is missing
# (e.g. deleted, or never written to state by an older deploy), the task will
# fail to start with a ResourceNotFoundException. Guarantee it exists first.
LOG_GROUP="${LOG_GROUP:-/ecs/${APP_NAME}-webapp}"
ensure_log_group "$LOG_GROUP"
success "Log group ready: $LOG_GROUP"

log "Registering new task definition with ECR image..."
aws ecs register-task-definition \
  --family "${APP_NAME}-webapp" \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 256 \
  --memory 512 \
  --execution-role-arn "arn:aws:iam::${ACCOUNT_ID}:role/ecsTaskExecutionRole" \
  --container-definitions "[
    {
      \"name\": \"${APP_NAME}-webapp\",
      \"image\": \"${ECR_IMAGE}\",
      \"essential\": true,
      \"portMappings\": [{ \"containerPort\": 8000, \"protocol\": \"tcp\" }],
      \"environment\": [
        { \"name\": \"HRSOT_LOG_LEVEL\", \"value\": \"INFO\" },
        { \"name\": \"HRSOT_BIND_HOST\", \"value\": \"0.0.0.0\" },
        { \"name\": \"HRSOT_BIND_PORT\", \"value\": \"8000\" }
      ],
      \"mountPoints\": [{
        \"sourceVolume\": \"${APP_NAME}-data\",
        \"containerPath\": \"/data\",
        \"readOnly\": false
      }],
      \"healthCheck\": {
        \"command\": [\"CMD-SHELL\", \"curl -f http://localhost:8000/health || exit 1\"],
        \"interval\": 30,
        \"timeout\": 5,
        \"retries\": 3,
        \"startPeriod\": 10
      },
      \"logConfiguration\": {
        \"logDriver\": \"awslogs\",
        \"options\": {
          \"awslogs-group\": \"${LOG_GROUP}\",
          \"awslogs-region\": \"${REGION}\",
          \"awslogs-stream-prefix\": \"ecs\"
        }
      }
    }
  ]" \
  --volumes "[
    {
      \"name\": \"${APP_NAME}-data\",
      \"efsVolumeConfiguration\": {
        \"fileSystemId\": \"${EFS_ID}\",
        \"transitEncryption\": \"ENABLED\",
        \"authorizationConfig\": {
          \"accessPointId\": \"${ACCESS_POINT_ID}\",
          \"iam\": \"DISABLED\"
        }
      }
    }
  ]" >/dev/null
success "Task definition updated"

# ── UPDATE SERVICE ────────────────────────────────────────────────────────────
header "Restarting ECS service"

log "Forcing new deployment with ECR image..."
aws ecs update-service \
  --cluster "$APP_NAME" \
  --service "${APP_NAME}-webapp" \
  --task-definition "${APP_NAME}-webapp" \
  --force-new-deployment \
  --region "$REGION" >/dev/null
success "Service update triggered"

# ── UPDATE STATE FILE ─────────────────────────────────────────────────────────
# Save ECR image to state so deploy.sh uses it going forward
grep -v "^CONTAINER_IMAGE=" "$STATE_FILE" > "${STATE_FILE}.tmp" || true
echo "CONTAINER_IMAGE=${ECR_IMAGE}" >> "${STATE_FILE}.tmp"
mv "${STATE_FILE}.tmp" "$STATE_FILE"
success "State file updated to use ECR image"

# ── WAIT FOR HEALTHY ──────────────────────────────────────────────────────────
header "Waiting for app to become healthy"
log "This takes 3-5 minutes..."
echo ""

attempt=0
while [ $attempt -lt 40 ]; do
  RUNNING=$(aws ecs describe-services \
    --cluster "$APP_NAME" \
    --services "${APP_NAME}-webapp" \
    --query 'services[0].runningCount' \
    --output text --region "$REGION" 2>/dev/null || echo "0")
  HEALTH=$(aws elbv2 describe-target-health \
    --target-group-arn "$TG_ARN" \
    --query 'TargetHealthDescriptions[0].TargetHealth.State' \
    --output text --region "$REGION" 2>/dev/null || echo "unknown")
  echo -ne "  Running tasks: ${RUNNING} | ALB target health: ${HEALTH}\r"
  if [ "$RUNNING" = "1" ] && [ "$HEALTH" = "healthy" ]; then
    echo ""
    break
  fi
  sleep 10
  attempt=$((attempt + 1))
done

echo ""
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  Image migration complete!${NC}"
echo -e "${BOLD}${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}App URL:${NC}   http://${ALB_DNS}/"
echo -e "  ${BOLD}API Docs:${NC}  http://${ALB_DNS}/docs"
echo ""
echo -e "  Future deployments will use ECR automatically."
echo -e "  The image is now stored in your own AWS account — no GHCR dependency."
echo ""
