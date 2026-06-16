#!/bin/bash
# Deployment script for the Hajj & Umrah News API on a VPS.
# Run from the project root (where this Dockerfile lives).

set -e

echo "🚀 Deploying Hajj & Umrah News API..."

# Stop and remove any existing container.
echo "📦 Stopping existing container..."
docker stop hajj-bot 2>/dev/null || true
docker rm hajj-bot 2>/dev/null || true

# Build the image (build context = project root).
echo "🔨 Building Docker image..."
docker build -t hajj-bot .

# Run the container. Secrets are NOT baked into the image — pass them via --env-file.
# Expects a .env file in the current directory (TELEGRAM_TOKEN, AWS_* keys, etc.).
echo "▶️  Starting container..."
docker run -d \
  --name hajj-bot \
  --restart unless-stopped \
  --env-file quality_bot/.env \
  -p 8010:8010 \
  hajj-bot

sleep 3

echo "✅ Checking container status..."
docker ps | grep hajj-bot || true

echo ""
echo "📋 Recent logs:"
docker logs --tail 20 hajj-bot || true

echo ""
echo "✨ Deployment complete! API: http://<server-ip>:8010  (docs at /docs)"
echo "📝 Logs:    docker logs -f hajj-bot"
echo "🛑 Stop:    docker stop hajj-bot"
echo "🔄 Restart: docker restart hajj-bot"
