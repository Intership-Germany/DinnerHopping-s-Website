#!/usr/bin/env sh
set -e

# generate-mkcert.sh
# Uses mkcert to create a certificate for development, automatically adding the machine's
# VPN IP (if found) as a SAN so other devs on the VPN can connect without cert warnings.

OUT_DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$OUT_DIR"

# Find a likely VPN IP in 10.8.0.0/24 (OpenVPN typical). Fallback to 127.0.0.1
VPN_IP=$(ifconfig 2>/dev/null | awk '/inet /{print $2}' | grep '^10\.8\.' | head -n1 || true)
if [ -z "$VPN_IP" ]; then
  echo "No VPN IP in 10.8.x.x found; defaulting to 127.0.0.1"
  VPN_IP=127.0.0.1
else
  echo "Found VPN IP: $VPN_IP"
fi

# Check for mkcert
if ! command -v mkcert >/dev/null 2>&1; then
  echo "mkcert not found. Install mkcert: https://github.com/FiloSottile/mkcert#installation"
  echo "On macOS: brew install mkcert nss && mkcert -install"
  exit 2
fi

# Ensure local CA is installed
mkcert -install || true

CRT_PATH="$OUT_DIR/dev.crt"
KEY_PATH="$OUT_DIR/dev.key"

echo "Generating certificate for: localhost, 127.0.0.1, $VPN_IP, dinnerhopping.com"
# Include dinnerhopping.com so local vhost can present a valid cert for the hostname.
mkcert -cert-file "$CRT_PATH" -key-file "$KEY_PATH" "localhost" "127.0.0.1" "$VPN_IP" "dinnerhopping.com"

echo "Certificates written to:"
echo "  $CRT_PATH"
echo "  $KEY_PATH"
echo "Distribute the generated root CA (if needed) to other dev machines or ensure mkcert -install was run on them."
