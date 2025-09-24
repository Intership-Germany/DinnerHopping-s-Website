# Obtention des certificats Let's Encrypt

## Pré-requis
- DNS configuré: `api.example.com` et/ou `www.example.com` pointent (A / AAAA) vers l'IP du VPS
- Port 80 accessible (firewall ouvert) le temps de la validation HTTP-01

## Installation certbot (Debian/Ubuntu)
```bash
sudo apt update
sudo apt install -y certbot python3-certbot-apache
```

## Générer certificats pour deux sous-domaines séparés
```bash
sudo certbot --apache -d api.example.com -d www.example.com -d example.com
```
Suivre l'assistant (forcer redirection HTTPS).

## Générer certificats séparés
```bash
sudo certbot --apache -d api.example.com
sudo certbot --apache -d www.example.com -d example.com
```

## Renouvellement
Certbot installe un timer systemd: vérifier
```bash
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

## Post-renew hook (recharger Apache si nécessaire)
Certbot plugin apache le fait automatiquement; sinon:
```
/etc/letsencrypt/renewal-hooks/deploy/reload-apache.sh
```
```bash
#!/bin/sh
systemctl reload apache2
```
Donner droits d'exécution: `chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-apache.sh`
