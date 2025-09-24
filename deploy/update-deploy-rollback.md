# Mise à jour & Rollback

## Hypothèses
- Déploiement cloné dans `/opt/dinnerhopping` (git repo)
- Service systemd: `docker-compose-app.service`

## Mise à jour standard
```bash
cd /opt/dinnerhopping
sudo systemctl stop docker-compose-app.service
# Sauvegarde image actuelle (optionnel)
docker image ls | grep dinnerhopping

# Mettre à jour code
git fetch --all
git checkout dev   # ou main
git pull --ff-only

# Rebuild & relance
sudo systemctl start docker-compose-app.service

# Vérification
curl -f https://api.example.com/docs > /dev/null && echo OK || echo FAIL
```

## Rollback rapide
Si la nouvelle version échoue:
```bash
sudo systemctl stop docker-compose-app.service
# Revenir au commit précédent
git log --oneline -5
git checkout <commit_prec>
# Rebuild
sudo systemctl start docker-compose-app.service
```

## Rollback via image sauvegardée
Si vous avez taggé une image stable:
```bash
docker tag dinnerhopping_backend:stable dinnerhopping_backend:rollback
# dans docker-compose, forcer image: et pas build: (variante avancée)
```

## Healthcheck manuel
Vous pouvez ajouter un endpoint `/health` (à créer) qui retourne 200 rapidement pour automatiser.

## Astuce: déploiement zéro-downtime (plus tard)
- Lancer nouveau container sur autre port interne (ex 8001)
- Changer ProxyPass Apache vers 8001 (graceful reload)
- Arrêter ancien container.
