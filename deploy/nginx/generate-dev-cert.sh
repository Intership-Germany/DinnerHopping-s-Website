#!/usr/bin/env sh
set -e
OUT_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$OUT_DIR"
echo "Generating self-signed dev cert into $OUT_DIR"
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout "$OUT_DIR/dev.key" \
  -out "$OUT_DIR/dev.crt" \
  -subj "/C=US/ST=Dev/L=Dev/O=Dev/OU=Dev/CN=dev.local"
echo "Done. You may need to install dev.crt as trusted on developer machines or use the VPN IP as SAN with mkcert."
