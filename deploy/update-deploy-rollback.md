# Update & Rollback

## Assumptions
- The production repository lives in `/opt/dinnerhopping`.
- The systemd unit `docker-compose-app.service` manages the stack defined in `deploy/production-docker-compose.yml`.
- All services (frontend, backend, MongoDB) are built locally via Docker Compose.

## Standard update
```bash
cd /opt/dinnerhopping

# Make sure we are on the expected branch and fetch the latest commit
git fetch origin main
git checkout main
git pull --ff-only origin main

# Stop the running stack and rebuild images locally
sudo systemctl stop docker-compose-app.service
sudo /usr/bin/docker-compose -f deploy/production-docker-compose.yml build --pull

# Relaunch the stack
sudo systemctl start docker-compose-app.service

# Quick health check (backend endpoint)
curl -f https://dinnerhoppings.acrevon.fr/api/health > /dev/null && echo OK || echo FAIL
```

## Quick rollback
If the new release fails:
```bash
cd /opt/dinnerhopping
sudo systemctl stop docker-compose-app.service

# Inspect recent commits and pick the previous one
git log --oneline -5
git checkout <previous_commit_hash>

# Rebuild and relaunch the previous revision
sudo /usr/bin/docker-compose -f deploy/production-docker-compose.yml build --pull
sudo systemctl start docker-compose-app.service
```

## Rollback using a tagged image
When you keep a known-good tag you can roll back faster:
```bash
docker tag dinnerhopping/backend:stable dinnerhopping/backend:rollback
# Update the compose file temporarily to pin the rollback tag if needed
```

## Manual health check
Expose `/api/health` (already used by CI) or another fast endpoint returning HTTP 200 to monitor the deployment automatically.

## Tip: zero-downtime deployment (future improvement)
- Build with a temporary service name (for example `backend_blue`).
- Update the reverse proxy to route to the new container (Apache graceful reload).
- Retire the old container once the traffic has drained.

## Apache reverse proxy quick reference

When ports 80/443 are already used by other services on the host, reuse an
existing `<VirtualHost>` and include the proxy rules instead of creating a brand
new site definition. Copy the snippet below (or adapt it) inside the vhost that
handles `dinnerhoppings.acrevon.fr`.

```apache
ProxyPreserveHost On
ProxyPassMatch "^/.well-known/acme-challenge/" "!"
RequestHeader set X-Forwarded-Proto expr=%{REQUEST_SCHEME}
RequestHeader set X-Forwarded-Port expr=%{SERVER_PORT}

ProxyPass "/chats/ws" "ws://127.0.0.1:8000/chats/ws"
ProxyPassReverse "/chats/ws" "ws://127.0.0.1:8000/chats/ws"

ProxyPass "/api/" "http://127.0.0.1:8000/"
ProxyPassReverse "/api/" "http://127.0.0.1:8000/"

ProxyPass "/" "http://127.0.0.1:40332/" retry=0 keepalive=On
ProxyPassReverse "/" "http://127.0.0.1:40332/"
```
 

Enable the required modules once (`sudo a2enmod proxy proxy_http proxy_wstunnel
headers rewrite ssl`), run `sudo apachectl configtest`, and reload Apache with
`sudo systemctl reload apache2`.
