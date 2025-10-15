# Multi-stage Dockerfile for DinnerHopping frontend
# Stage 1: optional config generation using node if a deploy-provided env is available
FROM node:18-alpine AS builder
WORKDIR /app

# Copy public assets to builder
COPY frontend/public/ ./public/

# Copy centralized env when present at build-time (deploy/frontend.env)
# The compose files are adjusted to use the repository root as the build context so
# the `deploy/` folder is available during docker build.
COPY deploy/frontend.env .env

# Generate config.js from the provided .env (tolerant if keys are missing)
RUN if [ -f .env ]; then node generate-config.js || true; else echo "no .env found, skipping config generation"; fi

# Stage 2: serve static files with Apache httpd
FROM httpd:2.4

# Copy built static files from the builder stage
COPY --from=builder /app/public/ /usr/local/apache2/htdocs/

# Copy project vhost from deploy directory (requires build context to include repo root)
COPY deploy/apache/my-httpd.conf /usr/local/apache2/conf/extra/my-httpd.conf
# Ensure vhost log directory exists and is writable
RUN mkdir -p /var/log/apache2 && chown -R www-data:www-data /var/log/apache2
# Ensure required proxy and headers modules are loaded (mod_proxy, proxy_http, proxy_wstunnel, headers)
# Insert LoadModule lines before the first "Include conf/extra" to guarantee modules are available
RUN set -eux; \
	HTTPD_CONF=/usr/local/apache2/conf/httpd.conf; \
	# Helper to ensure a LoadModule line is present and uncommented
	ensure_uncomment(){ \
		module_pattern="$1"; \
		# If an uncommented LoadModule line exists, nothing to do
		if grep -qE "^\s*LoadModule .*${module_pattern}" "$HTTPD_CONF"; then \
			return 0; \
		fi; \
		# If a commented LoadModule line exists, uncomment it in-place
		if grep -qE "^\s*#\s*LoadModule .*${module_pattern}" "$HTTPD_CONF"; then \
			sed -E -i.bak "s/^\s*#\s*(LoadModule .*${module_pattern})/\1/" "$HTTPD_CONF"; \
			return 0; \
		fi; \
		return 1; \
	}; \
	# Try to ensure each required module is available (uncomment if possible)
	ensure_uncomment "mod_proxy.so" || true; \
	ensure_uncomment "mod_proxy_http.so" || true; \
	ensure_uncomment "mod_proxy_wstunnel.so" || true; \
	ensure_uncomment "mod_headers.so" || true; \
	# If proxy modules are still not present, insert LoadModule lines before the first Include conf/extra
	if ! grep -qE "^\s*LoadModule .*mod_proxy.so" "$HTTPD_CONF"; then \
		awk 'BEGIN{inserted=0} /Include conf\\/extra/ && !inserted { print "LoadModule proxy_module modules/mod_proxy.so"; print "LoadModule proxy_http_module modules/mod_proxy_http.so"; print "LoadModule proxy_wstunnel_module modules/mod_proxy_wstunnel.so"; inserted=1 } { print }' "$HTTPD_CONF" > "$HTTPD_CONF.new" && mv "$HTTPD_CONF.new" "$HTTPD_CONF"; \
	fi; \
	# Ensure our project vhost is included (idempotent)
	grep -q "Include conf/extra/my-httpd.conf" "$HTTPD_CONF" || echo "\n# Include project vhost\nInclude conf/extra/my-httpd.conf" >> "$HTTPD_CONF"

EXPOSE 80

CMD ["httpd-foreground"]
