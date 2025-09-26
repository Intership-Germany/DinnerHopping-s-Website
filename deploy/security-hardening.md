# Sécurité & Durcissement VPS DinnerHopping

## Réseau / Pare-feu
- Autoriser uniquement: 22 (SSH), 80 (HTTP), 443 (HTTPS). Bloquer 27017 (Mongo) exposé seulement dans docker network.
- UFW exemple:
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

## SSH
- Désactiver root direct: `PermitRootLogin no`
- Utiliser clés SSH, désactiver password: `PasswordAuthentication no`
- Fail2ban pour protéger SSH.

## Fail2ban
```bash
apt install fail2ban
cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
# Activer sshd, apache-auth, apache-badbots
systemctl enable --now fail2ban
fail2ban-client status
```

## Mises à jour
- Activer unattended-upgrades (Debian/Ubuntu) pour sécurité.

## Permissions
- Répertoire de déploiement: `/opt/dinnerhopping` appartenant à un user non-root (ex: deploy) membre du groupe docker.
- Logs applicatifs: monter avec permissions 750, utilisateur uniquement.

## Secrets
- Ne pas commiter `.env` production.
- Utiliser `chmod 600` sur `.env`.

## HTTP Security Headers
- Déjà ajoutés dans conf Apache. Ajouter aussi:
```
Header always set Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
```

## Monitoring basique
- Script healthcheck: `curl -f https://api.example.com/docs || echo 'api down'` dans cron.
- Examiner `docker logs` et logs Apache régulièrement.

## Sauvegardes
- Dump Mongo régulier (si donnée critique) : `mongodump --archive=/backups/$(date +%F).gz --gzip --db dinnerhopping` via container exec.

## Limitation de ressources
- Ajouter (optionnel) dans docker-compose production:
```
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
```
(compatible swarm; sinon `--cpus` / `--memory` via run, ou cgroups).
