#!/usr/bin/env bash
set -euo pipefail

# Deploy script idempotent pour DinnerHopping
# Usage: ./deploy.sh [branch] [service_name]
# branch par défaut: dev
# service_name systemd par défaut: docker-compose-app.service

BRANCH=${1:-main}
SERVICE=${2:-docker-compose-app.service}
APP_DIR=/opt/dinnerhopping
COMPOSE_DIR="$APP_DIR/deploy"
COMPOSE_FILE=docker-compose.prod.yml

log() { echo "[deploy] $(date +'%Y-%m-%dT%H:%M:%S') $*"; }

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DC_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC_CMD="docker-compose"
else
  log "ERREUR: ni 'docker compose' ni 'docker-compose' disponible" >&2
  exit 1
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  log "ERREUR: repo git non trouvé dans $APP_DIR" >&2
  exit 1
fi

log "Fetch dernières modifications (branch=$BRANCH)"
cd "$APP_DIR"
# Sécurité: s'assurer que la branche existe
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git checkout "$BRANCH"
else
  git fetch origin "$BRANCH":"$BRANCH"
  git checkout "$BRANCH"
fi

git fetch --all --prune
# Fast-forward uniquement pour éviter merges inattendus
if ! git pull --ff-only; then
  log "Pull fast-forward impossible. Abandon." >&2
  exit 1
fi

LAST_COMMIT=$(git rev-parse --short HEAD)
log "Code à commit $LAST_COMMIT"

log "Mise à jour des containers (pull)"
cd "$COMPOSE_DIR"
"$(${DC_CMD% *})" >/dev/null 2>&1 # no-op to satisfy shellcheck (variable use)
# Optional GHCR login for private registry (provide GHCR_USERNAME and GHCR_PAT)
if [ -n "${GHCR_USERNAME:-}" ] && [ -n "${GHCR_PAT:-}" ]; then
  log "Login GHCR (optionnel)"
  echo "$GHCR_PAT" | sudo docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin || log "Login GHCR échoué (on continue si image publique)"
fi
eval "$DC_CMD -f $COMPOSE_FILE pull" || true

log "Redémarrage via systemd ($SERVICE)"
if systemctl is-enabled --quiet "$SERVICE"; then
  sudo systemctl stop "$SERVICE" || true
  sudo systemctl start "$SERVICE"
else
  log "Service systemd pas encore installé, lancement direct compose"
  # Déployer aussi le frontend statique si présent
  if [ -d "$APP_DIR/frontend/public" ]; then
    sudo mkdir -p /var/www/dinnerhopping
    sudo rsync -a --delete "$APP_DIR/frontend/public/" /var/www/dinnerhopping/
  fi
  # Use the central env file in deploy/backend.env. Ensure deploy/backend.env exists on the server
  # before running this script (do not commit production secrets).
  eval "$DC_CMD -f $COMPOSE_FILE up -d"
fi

log "Attente 5s pour démarrage backend"
sleep 5

HEALTH_URL=${HEALTH_URL:-"https://dinnerhoppings.acrevon.fr/api/openapi.json"}
log "Healthcheck $HEALTH_URL"
if curl -k -fsS -o /dev/null "$HEALTH_URL"; then
  log "Healthcheck OK"
else
  log "Healthcheck ECHEC" >&2
  exit 2
fi

log "Nettoyage images anciennes (>2 versions)"
# Garder les 2 dernières images backend locales
IMAGES=$(docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}' | grep backend | sort | awk '{print $1}')
COUNT=0
for img in $IMAGES; do
  COUNT=$((COUNT+1))
  if [[ $COUNT -gt 2 ]]; then
    docker image rm -f "$img" || true
  fi
done

log "Déploiement terminé avec succès commit=$LAST_COMMIT"
