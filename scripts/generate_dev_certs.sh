#!/usr/bin/env bash
set -e

CERT_DIR="docker/nginx/certs"
mkdir -p "$CERT_DIR"

if [ ! -f "$CERT_DIR/dev.crt" ]; then
    echo "=== Generating Self-Signed SSL Certificate for localhost ==="
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERT_DIR/dev.key" \
        -out "$CERT_DIR/dev.crt" \
        -subj "/C=IL/ST=Local/L=Local/O=MAGAgent/OU=Dev/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
    echo "Certificates created in $CERT_DIR"
else
    echo "SSL Certificate already exists in $CERT_DIR"
fi
