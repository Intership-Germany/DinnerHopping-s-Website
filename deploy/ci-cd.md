# CI/CD GitHub Actions -> VPS

## Résumé
Push sur `dev` ou `main` déclenche le workflow `.github/workflows/deploy.yml` qui:
1. Ouvre une connexion SSH vers le VPS (clé stockée dans secret `VPS_SSH_KEY`).
2. Clone initialement (si absent) puis exécute `deploy/deploy.sh` côté serveur.
3. Le script fait git pull, rebuild docker, restart service systemd, healthcheck.

## Secrets requis (Settings > Secrets and variables > Actions)
- `VPS_HOST` : IP publique ou IP VPN (ex: 10.8.0.1 si accessible depuis GitHub runner — Attention: si 10.8.0.1 est un réseau privé non routable depuis internet, GitHub ne pourra PAS s'y connecter directement. Dans ce cas: self-hosted runner nécessaire sur le même réseau.)
- `VPS_SSH_USER` : utilisateur (ex: deploy).
- `VPS_SSH_KEY` : clé privée OpenSSH (format begin OPENSSH PRIVATE KEY) correspondant à la clé autorisée dans `~/.ssh/authorized_keys` sur le VPS.

Optionnel:
- `HEALTH_URL` exportable côté serveur si tu modifies le script pour le lire.

## Important: Accessibilité réseau
GitHub héberge les runners publics sur Internet. Une IP privée `10.8.0.1` (VPN interne) n'est probablement *pas* accessible. Solutions:
1. Utiliser l'IP publique du VPS dans `VPS_HOST`.
2. OU installer un runner self-hosted sur le VPS (ou une machine du même réseau).
   - `./config.sh` (GitHub Actions runner) dans `/opt/actions-runner`.
   - Workflow peut alors utiliser `runs-on: self-hosted`.

## Minimal sudo
Le script déclenche `systemctl` : l'utilisateur a besoin d'un sudo sans mot de passe pour ce service uniquement. Ajouter dans `/etc/sudoers.d/dinnerhopping`:
```
deploy ALL=NOPASSWD: /bin/systemctl stop docker-compose-app.service, /bin/systemctl start docker-compose-app.service, /bin/systemctl status docker-compose-app.service
```
Adapter si chemin binaire différent (`which systemctl`).

## Premier déploiement manuel
Sur le serveur:
```bash
sudo mkdir -p /opt/dinnerhopping
sudo chown deploy: /opt/dinnerhopping
cd /opt/dinnerhopping
git clone git@github.com:<org>/<repo>.git .
cp deploy/example.env backend/app/.env
# Installer systemd unit
sudo cp deploy/docker-compose-app.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now docker-compose-app.service
```

## Déploiement via workflow
- Commit sur dev -> build & restart.
- Pour forcer: onglet Actions -> workflow Deploy -> Run workflow.

## Rollback via SSH
Voir `update-deploy-rollback.md`. (Possibilité d'ajouter un job qui tag l'image stable.)

## Améliorations futures
- Build image Docker dans GitHub (multi-stage, push registry), pull côté serveur -> temps réduit.
- Ajouter tests (pytest) avant étape deploy: job séparé qui doit réussir.
- Ajouter un endpoint `/health` (FastAPI) pour un check plus rapide (<50ms) que /docs.
- Environnement staging (branch staging) + domaine staging.

## Échec Healthcheck
Le workflow échouera sur la step Healthcheck. Consulter logs:
```
journalctl -u docker-compose-app.service -e
sudo docker compose -f /opt/dinnerhopping/deploy/production-docker-compose.yml logs --tail=200 backend
```
