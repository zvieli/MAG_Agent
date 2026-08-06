#!/usr/bin/env bash
set -e

echo "=== 1. Generating Dev SSL Certificates ==="
./scripts/generate_dev_certs.sh

echo "=== 2. Starting Docker Environment (HTTPS Proxy on port 7071) ==="
docker-compose down --remove-orphans
docker-compose up -d

echo ""
echo "=========================================================="
echo "=== IMPORTANT ONE-TIME STEP FOR MIXED CONTENT BROWSER  ==="
echo "=========================================================="
echo "Open your browser and navigate to:"
echo "   https://localhost:7071/api/health"
echo ""
echo "Click 'Advanced' -> 'Proceed to localhost (unsafe)' to trust"
echo "the self-signed SSL certificate in your browser session."
echo "=========================================================="
