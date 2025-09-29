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

# Copy custom Apache configuration into conf.d/ and ensure it's loaded
RUN mkdir -p /usr/local/apache2/conf/conf.d \
		&& if ! grep -q "conf/conf.d" /usr/local/apache2/conf/httpd.conf; then \
				 printf '\n# Load DinnerHopping virtual hosts\nIncludeOptional conf/conf.d/*.conf\n' >> /usr/local/apache2/conf/httpd.conf; \
			 fi
COPY my-httpd.conf /usr/local/apache2/conf/conf.d/00-dinnerhopping.conf

# Expose port 80
EXPOSE 80
