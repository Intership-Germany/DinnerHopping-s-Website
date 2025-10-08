Local Mail + HTTPS for development

This should help when your real SMTP (OVH) is blocked or you want to test email flows locally.

What this provides
- MailHog SMTP server (SMTP: port 1025) + web UI (HTTP: port 8025)
- nginx HTTPS endpoint (443 inside container -> mapped to host 8443) using a self-signed cert

Quick start (from repo root)

1) Generate a trusted dev cert using mkcert (recommended) or fallback to a self-signed cert.

Preferred: mkcert (generates locally-trusted certs)

   ./deploy/nginx/generate-mkcert.sh

If you don't have mkcert installed, on macOS you can run:

   brew install mkcert nss
   mkcert -install

Fallback (not recommended): if you still want a quick self-signed cert, the repository previously included a script but mkcert is preferred for ease of use.

2) Start MailHog + nginx:

   docker compose -f deploy/dev-mail.yml up -d

3) Configure backend environment for local SMTP

- If running backend as a local process on your machine and talking to MailHog via the mapped port:

  SMTP_HOST=127.0.0.1
  SMTP_PORT=1025

- If running backend in another container on the same docker network (use service name):

  SMTP_HOST=mailhog
  SMTP_PORT=1025

4) Access MailHog UI in your browser to view emails:

   http://127.0.0.1:8025

5) Access the dev HTTPS endpoint over the VPN

 - The nginx container exposes container port 443 on the host. Other developers connected to the VPN (10.8.0.0/24) can reach your machine at the VPN IP (for example 10.8.0.6) on port 443: https://10.8.0.6
 - If your browser rejects the certificate, either install the generated mkcert root CA on developer machines (preferred) or trust `deploy/nginx/certs/dev.crt` manually.

Notes and tips
- MailHog does NOT deliver emails to external domains. It's for development only.
- If you need to test real delivery to external addresses, use a transactional provider or a dedicated test SMTP account.
- To forward API requests through nginx to the backend, uncomment and adjust the proxy_pass block in `deploy/nginx/conf.d/dev-mail.conf`.

Example .env snippet for a local backend process

   # point backend to local MailHog
   SMTP_HOST=127.0.0.1
   SMTP_PORT=1025

VPN notes


Virtual host for dinnerhopping.com
---------------------------------

This setup also provides a vhost for the `dinnerhopping.com` hostname. Nginx is configured to:

- proxy `/api/` to the backend container at `http://backend:8000/api/`
- proxy `/` to a frontend dev server running on the host at `http://host.docker.internal:3000/`

For local testing add entries to `/etc/hosts` (requires sudo):

   sudo sh -c 'echo "127.0.0.1 dinnerhopping.com" >> /etc/hosts'
   sudo sh -c 'echo "10.8.0.2 dinnerhopping.com" >> /etc/hosts'

Notes:

- Nginx binds to host port 443. If something else listens on 443 (apache/httpd), stop it:

   sudo apachectl stop

- If you see `502 Bad Gateway` on `https://dinnerhopping.com/` it means the frontend dev server isn't running on host port 3000.

