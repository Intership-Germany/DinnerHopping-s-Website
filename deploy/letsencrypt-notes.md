# Obtaining Let's Encrypt Certificates

## Prerequisites
- DNS configured: `api.example.com` and/or `www.example.com` point (A / AAAA) to the VPS IP
- Port 80 accessible (firewall open) during HTTP-01 validation

## Install certbot (Debian/Ubuntu)
```bash
sudo apt update
sudo apt install -y certbot python3-certbot-apache
```

## Generate certificates for two separate subdomains
```bash
sudo certbot --apache -d api.example.com -d www.example.com -d example.com
```
Follow the assistant (force HTTPS redirection).

## Generate separate certificates
```bash
sudo certbot --apache -d api.example.com
sudo certbot --apache -d www.example.com -d example.com
```

## Renewal
Certbot installs a systemd timer: verify
```bash
systemctl list-timers | grep certbot
sudo certbot renew --dry-run
```

## Post-renew hook (reload Apache if necessary)
Certbot's apache plugin does this automatically; otherwise:
```
/etc/letsencrypt/renewal-hooks/deploy/reload-apache.sh
```
```bash
#!/bin/sh
systemctl reload apache2
```
Grant execution rights: `chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-apache.sh`
