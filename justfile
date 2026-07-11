set dotenv-load := false

classifier_dir := "classifier"
output_dir := "output"
infra_dir := "infra"
videos_dir := "videos"

# Build the Go pipeline binary
build:
    cd pipeline && go build -o bin/pipeline ./cmd/pipeline

# Install / sync Python classifier dependencies
install:
    cd {{classifier_dir}} && uv sync

# =============================================================================
# WORKFLOW 1: Cloud Processing (S3 -> Process -> S3)
# Requires: AWS credentials with access to demo-hand-tracking-bucket
# =============================================================================

# Download video from S3 to local cache
# Usage: just download-video bakery
download-video prefix="bakery":
    #!/usr/bin/env bash
    set -e
    mkdir -p {{videos_dir}}/{{prefix}}
    VIDEO_FILE="{{videos_dir}}/{{prefix}}/clip.mp4"
    if [ -f "$VIDEO_FILE" ]; then
        echo "Video already cached: $VIDEO_FILE"
    else
        echo "Downloading {{prefix}} video from S3..."
        aws s3 cp \
            "s3://demo-hand-tracking-bucket/{{prefix}}/{{prefix}}_01/clip.mp4" \
            "$VIDEO_FILE"
        echo "Downloaded to $VIDEO_FILE"
    fi

# Run full cloud pipeline: S3 download -> classify -> S3 upload
# Usage: just run-cloud 1 bakery demo-ha-sadiq
run-cloud limit="1" prefix="bakery" dest_bucket="":
    #!/usr/bin/env bash
    set -e
    mkdir -p {{output_dir}}
    DEST_ARG=""
    if [ -n "{{dest_bucket}}" ]; then
        DEST_ARG="-dest-bucket {{dest_bucket}}"
    fi
    cd pipeline && ./bin/pipeline \
        -limit {{limit}} \
        -prefix "{{prefix}}" \
        -base-fps 1 \
        -event-fps 5 \
        -dense-fps 10 \
        -context-s 3 \
        -max-frames-per-video 250 \
        -primary-conf 0.5 \
        -secondary-conf 0.15 \
        -presence-threshold 0.06 \
        -classifier-dir ../{{classifier_dir}} \
        -output ../{{output_dir}} \
        $DEST_ARG

# =============================================================================
# WORKFLOW 2: Local Processing (Local Video -> Local Frames)
# No cloud access needed - works fully offline
# =============================================================================

# Classify frames from a local video file
# Usage: just run-local videos/bakery/clip.mp4
# Output: output/<video_stem>/frames/{label}/*.jpg
run-local video:
    #!/usr/bin/env bash
    set -e
    if [ ! -f "{{video}}" ]; then
        echo "ERROR: Video file not found: {{video}}"
        echo ""
        echo "Place your video file in the videos/ directory, e.g.:"
        echo "  videos/bakery/clip.mp4"
        echo ""
        echo "Then run:"
        echo "  just run-local videos/bakery/clip.mp4"
        exit 1
    fi
    # Extract stem: videos/bakery/clip.mp4 -> bakery__clip
    DIR_NAME=$(dirname "{{video}}" | xargs basename)
    FILE_NAME=$(basename "{{video}}" .mp4)
    STEM="${DIR_NAME}__${FILE_NAME}"
    OUTPUT_PATH="{{output_dir}}/$STEM"
    mkdir -p "$OUTPUT_PATH"
    echo "Processing {{video}} -> $OUTPUT_PATH"
    cd {{classifier_dir}} && uv run classify-video \
        "../{{video}}" \
        --out "../$OUTPUT_PATH/report.json" \
        --frames-dir "../$OUTPUT_PATH" \
        --base-fps 1 \
        --event-fps 5 \
        --dense-fps 10 \
        --context-s 3 \
        --primary-conf 0.5 \
        --secondary-conf 0.15 \
        --presence-threshold 0.06
    echo ""
    echo "Done! Output:"
    echo "  Report: $OUTPUT_PATH/report.json"
    echo "  Frames: $OUTPUT_PATH/frames/{label}/*.jpg"

# Shortcut: process the default bakery video
# Usage: just run-bakery
run-bakery:
    just run-local videos/bakery/clip.mp4

# Build then run cloud pipeline
all: build run-cloud

# Remove output frames (keeps report JSON)
clean-frames:
    find {{output_dir}} -name "*.jpg" -delete

# Remove all output
clean:
    rm -rf {{output_dir}}

# Ingest pipeline output into the backend
# Usage: just ingest ./output http://localhost:8080 admin@example.com admin123
ingest dir="./output" api_url="http://localhost:8080" email="admin@example.com" password="admin123":
    #!/usr/bin/env bash
    set -e
    echo "Fetching admin token from {{api_url}}..."
    TOKEN=$(curl -s -X POST "{{api_url}}/auth/login" \
      -H 'Content-Type: application/json' \
      -d '{"email":"{{email}}","password":"{{password}}"}' \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")
    if [ -z "$TOKEN" ]; then
        echo "Failed to get token. Check credentials."
        exit 1
    fi
    echo "Ingesting {{dir}}..."
    curl -s -X POST "{{api_url}}/admin/ingest" \
      -H 'Content-Type: application/json' \
      -H "Authorization: Bearer $TOKEN" \
      -d '{"directory":"{{dir}}"}' | python3 -m json.tool

# Start local dev environment (postgres + backend)
dev:
    docker compose up -d --build

# Stop local dev environment
dev-stop:
    docker compose down

# View backend logs
logs:
    docker compose logs -f backend

# Rebuild and restart backend
restart:
    docker compose up -d --build backend

# Full local demo: start backend, ingest frames
demo: dev
    @echo "Waiting for backend to start..."
    @sleep 5
    just ingest

# Initialize Terraform
infra-init:
    cd {{infra_dir}} && terraform init

# Plan infrastructure changes
infra-plan:
    cd {{infra_dir}} && terraform plan

# Deploy to production
# Requires: infra/terraform.tfvars (copy from terraform.tfvars.example)
deploy:
    #!/usr/bin/env bash
    set -e
    cd {{infra_dir}}

    if [ ! -f terraform.tfvars ]; then
        echo "ERROR: infra/terraform.tfvars not found!"
        echo ""
        echo "Copy and edit the example file:"
        echo "  cp infra/terraform.tfvars.example infra/terraform.tfvars"
        echo ""
        echo "Required variables:"
        echo "  - key_name: Your EC2 key pair name"
        echo "  - postgres_password: Strong database password"
        echo "  - jwt_secret: 64+ char random string (openssl rand -hex 32)"
        echo "  - admin_password: Admin user password"
        echo "  - s3_bucket: Your S3 bucket for frames"
        echo "  - s3_access_key, s3_secret_key: AWS credentials"
        exit 1
    fi

    terraform init -upgrade
    terraform apply -auto-approve

    echo ""
    echo "  Deployment Complete!"
    terraform output

# Destroy production infrastructure
destroy:
    cd {{infra_dir}} && terraform destroy

# Show deployment outputs
deploy-info:
    cd {{infra_dir}} && terraform output

# SSH into production server
ssh:
    #!/usr/bin/env bash
    cd {{infra_dir}}
    IP=$(terraform output -raw public_ip 2>/dev/null)
    KEY=$(terraform output -raw key_name 2>/dev/null || echo "your-key")
    if [ -z "$IP" ]; then
        echo "No deployment found. Run 'just deploy' first."
        exit 1
    fi
    ssh -i ~/.ssh/$KEY.pem ubuntu@$IP

# View production logs
prod-logs:
    #!/usr/bin/env bash
    cd {{infra_dir}}
    IP=$(terraform output -raw public_ip 2>/dev/null)
    KEY=$(terraform output -raw key_name 2>/dev/null || echo "your-key")
    if [ -z "$IP" ]; then
        echo "No deployment found."
        exit 1
    fi
    ssh -i ~/.ssh/$KEY.pem ubuntu@$IP "cd /opt/human-archive && docker compose -f docker-compose.prod.yml logs -f"

# Redeploy (pull latest and rebuild)
redeploy:
    #!/usr/bin/env bash
    cd {{infra_dir}}
    IP=$(terraform output -raw public_ip 2>/dev/null)
    KEY=$(terraform output -raw key_name 2>/dev/null || echo "your-key")
    if [ -z "$IP" ]; then
        echo "No deployment found."
        exit 1
    fi
    echo "Redeploying on $IP..."
    ssh -i ~/.ssh/$KEY.pem ubuntu@$IP "cd /opt/human-archive && git pull && docker compose -f docker-compose.prod.yml up -d --build"
