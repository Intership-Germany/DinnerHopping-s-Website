FROM httpd:alpine

# Copie les sources dans le conteneur
COPY . /app

# Génère le fichier public/config.js à partir de .env
WORKDIR /app
# RUN apk add --no-cache nodejs npm && \
# 	node generate-config.js
RUN apk add nodejs npm && \
	node generate-config.js

# Copie le contenu du dossier public dans le dossier web Apache
RUN rm -rf /usr/local/apache2/htdocs/* && cp -r public/* /usr/local/apache2/htdocs/


# Copie la configuration .htaccess dans le dossier web
COPY public/.htaccess /usr/local/apache2/htdocs/.htaccess

# Copie la configuration Apache personnalisée dans conf.d/
COPY my-httpd.conf /usr/local/apache2/conf/conf.d/my-httpd.conf

# Ajoute ServerName localhost dans le fichier principal httpd.conf pour supprimer l'avertissement
RUN echo "" >> /usr/local/apache2/conf/httpd.conf && echo "ServerName localhost" >> /usr/local/apache2/conf/httpd.conf

# Expose le port 80
EXPOSE 80
