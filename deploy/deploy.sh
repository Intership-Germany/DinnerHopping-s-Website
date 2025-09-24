#!/usr/bin/env bash
set -euo pipefail

# Deploy script idempotent pour DinnerHopping
# Usage: ./deploy.sh [branch] [service_name]
# branch par défaut: dev
# service_name systemd par défaut: docker-compose-app.service

BRANCH=${1:-dev}
SERVICE=${2:-docker-compose-app.service}
APP_DIR=/opt/dinnerhopping
COMPOSE_DIR="$APP_DIR/deploy"
COMPOSE_FILE=production-docker-compose.yml

log() { echo "[deploy] $(date +'%Y-%m-%dT%H:%M:%S') $*"; }

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

log "Construction / mise à jour des containers"
cd "$COMPOSE_DIR"
# Build et up (mongo est persistant via volume nommé)
docker compose -f "$COMPOSE_FILE" pull || true
if ! docker compose -f "$COMPOSE_FILE" build --pull; then
  log "Échec build" >&2
  exit 1
fi

log "Redémarrage via systemd ($SERVICE)"
if systemctl is-enabled --quiet "$SERVICE"; then
  sudo systemctl stop "$SERVICE" || true
  sudo systemctl start "$SERVICE"
else
  log "Service systemd pas encore installé, lancement direct compose"
  docker compose -f "$COMPOSE_FILE" up -d
fi

log "Attente 5s pour démarrage backend"
sleep 5

HEALTH_URL=${HEALTH_URL:-"https://api.example.com/docs"}
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
