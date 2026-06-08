FROM python:3.12-slim

LABEL maintainer="Bastet Project"
LABEL description="Face & State Secure Server"

WORKDIR /app

# Dépendances système (libmagic1) et création des certificats SSL (openssl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 openssl \
    && rm -rf /var/lib/apt/lists/*

# Génération d'un certificat SSL auto-signé pour HTTPS
RUN openssl req -x509 -newkey rsa:4096 -keyout /app/key.pem -out /app/cert.pem -sha256 -days 365 -nodes -subj "/C=FR/CN=localhost"

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code applicatif
COPY main.py .

# Volumes (faces + state/myges data)
VOLUME ["/data"]

EXPOSE 44888

# Lancement HTTPS natif d'Uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "44888", "--ssl-keyfile", "/app/key.pem", "--ssl-certfile", "/app/cert.pem", "--log-level", "info", "--proxy-headers"]
