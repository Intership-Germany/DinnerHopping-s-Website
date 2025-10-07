# Security & Hardening VPS DinnerHopping

## Network / Firewall
- Allow only: 22 (SSH), 80 (HTTP), 443 (HTTPS). Block 27017 (Mongo) exposed only in the Docker network.
- UFW example:
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

## SSH
- Disable direct root login: `PermitRootLogin no`
- Use SSH keys, disable password authentication: `PasswordAuthentication no`
- Use Fail2ban to protect SSH.

## Fail2ban
```bash
apt install fail2ban
cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local
# Enable sshd, apache-auth, apache-badbots
systemctl enable --now fail2ban
fail2ban-client status
```

## Updates
- Enable unattended-upgrades (Debian/Ubuntu) for security.

## Permissions
- Deployment directory: `/opt/dinnerhopping` owned by a non-root user (e.g., deploy) who is a member of the Docker group.
- Application logs: mount with 750 permissions, user-only access.

## Secrets
- Do not commit the production `.env` file.
- Use `chmod 600` on `.env`.

## HTTP Security Headers
- Already added in the Apache configuration. Also add:
```
Header always set Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
```

## Basic Monitoring
- Healthcheck script: `curl -f https://api.example.com/docs || echo 'api down'` in cron.
- Regularly review `docker logs` and Apache logs.

## Backups
- Regular Mongo dump (if critical data): `mongodump --archive=/backups/$(date +%F).gz --gzip --db dinnerhopping` via container exec.

## Resource Limitation
- Optionally add in production docker-compose:
```
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
```
(compatible with Swarm; otherwise use `--cpus` / `--memory` via run, or cgroups).
