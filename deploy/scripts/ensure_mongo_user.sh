#!/usr/bin/env bash
set -euo pipefail

# Ensure Mongo admin user exists (preserve data). Uses an ephemeral mongo client container.
# Usage: run from repo root. Requires docker.

HERE=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE="$HERE/backend.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "deploy/backend.env not found; please create it or run from deploy/"
  exit 1
fi

MONGO_USER=$(grep '^MONGO_USER=' "$ENV_FILE" | head -n1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//")
MONGO_PASS=$(grep '^MONGO_PASSWORD=' "$ENV_FILE" | head -n1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//")

if [ -z "$MONGO_USER" ] || [ -z "$MONGO_PASS" ]; then
  echo "MONGO_USER or MONGO_PASSWORD not set in $ENV_FILE"
  exit 1
fi

# find the mongo container created by compose
MC=$(docker compose -f "$HERE/docker-compose.dev.yml" ps -q mongo 2>/dev/null || true)
if [ -z "$MC" ]; then
  # fallback container name
  MC=$(docker ps -q --filter "name=deploy-mongo-1" | head -n1 || true)
fi

if [ -z "$MC" ]; then
  echo "Could not find running mongo container. Start the dev stack first."
  exit 1
fi

# get the container network name
NETWORK=$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$MC")
if [ -z "$NETWORK" ]; then
  echo "Could not determine docker network for mongo container"
  exit 1
fi

echo "Using network: $NETWORK"

CHECK_OUTPUT=$(docker run --rm --network "$NETWORK" mongo:6.0 mongosh --quiet "mongodb://mongo:27017/admin" --eval \
"try { var u = db.getUser('$MONGO_USER'); if (u == null) { print('MONGO_USER_NOT_FOUND'); } else { print('MONGO_USER_EXISTS'); } } catch(e) { print('MONGO_ERROR:'+e); }" 2>&1 || true)

if echo "$CHECK_OUTPUT" | grep -q '^MONGO_USER_EXISTS'; then
  echo "User '$MONGO_USER' already exists. Nothing to do."
  exit 0
fi

if echo "$CHECK_OUTPUT" | grep -q '^MONGO_ERROR:'; then
  echo "Mongo reported an error while checking user:"
  echo "$CHECK_OUTPUT"
  echo "This typically means the server requires authentication. To add the user while preserving data you'll need to run the createUser command authenticated as an existing admin."
  exit 2
fi

if echo "$CHECK_OUTPUT" | grep -q '^MONGO_USER_NOT_FOUND'; then
  echo "User $MONGO_USER not found — creating..."
  CREATE_OUT=$(docker run --rm --network "$NETWORK" mongo:6.0 mongosh --quiet "mongodb://mongo:27017/admin" --eval \
  "db.getSiblingDB('admin').createUser({user: '$MONGO_USER', pwd: '$MONGO_PASS', roles: [{role: 'root', db: 'admin'}]}); print('MONGO_USER_CREATED');" 2>&1 || true)
  echo "$CREATE_OUT"
  if echo "$CREATE_OUT" | grep -q 'MONGO_USER_CREATED'; then
    echo "User created successfully."
    exit 0
  else
    echo "Failed to create user. Output:"
    echo "$CREATE_OUT"
    exit 3
  fi
fi

echo "Unexpected response:"
echo "$CHECK_OUTPUT"
exit 4
