Purpose: grouped Apache vhosts for DinnerHopping.

This folder contains the canonical Apache virtual host configuration files used by the project.

Files:
- apache-single-domain.conf  - single vhost serving frontend and proxying /api to backend
- apache-api.conf           - API-focused proxy rules (kept for reference)
- apache-frontend.conf      - frontend-only vhost (kept for reference)

Usage:
- Copy the desired file(s) into your system Apache `sites-available` and enable them.
- Or include the file directly from a server-level Apache conf: `Include /opt/dinnerhopping/deploy/apache/apache-single-domain.conf`

Notes:
- These are copies of the top-level files from `deploy/` to group them. Originals remain at `deploy/` for now to avoid breaking existing deploy scripts.
