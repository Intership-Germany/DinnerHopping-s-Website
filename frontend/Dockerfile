FROM httpd:alpine

# Copy project sources into the container
COPY . /app

# Generate public/config.js from .env (used by static HTML pages)
WORKDIR /app


RUN apk add nodejs npm && \
	node generate-config.js

# Copy the public folder into Apache's web root
RUN rm -rf /usr/local/apache2/htdocs/* && cp -r public/* /usr/local/apache2/htdocs/


# Copy .htaccess into the web root
COPY public/.htaccess /usr/local/apache2/htdocs/.htaccess

# Copy custom Apache configuration into conf.d/
COPY my-httpd.conf /usr/local/apache2/conf/conf.d/my-httpd.conf0

# Add ServerName localhost and include conf.d/*.conf in the main httpd.conf
RUN echo "" >> /usr/local/apache2/conf/httpd.conf \
	&& echo "ServerName localhost" >> /usr/local/apache2/conf/httpd.conf \
	&& sed -ri 's/^#\s*(LoadModule\s+rewrite_module\s+modules\/mod_rewrite\.so)/\1/' /usr/local/apache2/conf/httpd.conf \
	&& sed -ri 's/^#\s*(LoadModule\s+env_module\s+modules\/mod_env\.so)/\1/' /usr/local/apache2/conf/httpd.conf \
	&& echo "Include conf/conf.d/*.conf" >> /usr/local/apache2/conf/httpd.conf

# Expose port 80
EXPOSE 80
