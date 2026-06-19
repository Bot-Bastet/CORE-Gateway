FROM python:3.12-slim

LABEL maintainer="Bastet Project"
LABEL description="Face & State Secure Server"

WORKDIR /app

# Dépendances système (libmagic1, curl pour le healthcheck, unzip+git pour l'auto-updater)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 curl unzip git \
    && rm -rf /var/lib/apt/lists/*

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copier le code applicatif
COPY *.py .

# Volumes (faces + state/myges data)
VOLUME ["/data"]

EXPOSE 44888

# Lancement HTTP (SSL désactivé pour éviter les erreurs de certificat sur IP publique)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "44888", "--log-level", "info", "--proxy-headers"]
