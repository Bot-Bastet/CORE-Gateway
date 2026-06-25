FROM python:3.12-slim

LABEL maintainer="Bastet Project"
LABEL description="Face & State Secure Server"

WORKDIR /app

# Dépendances système (libmagic1, curl pour le healthcheck, unzip+git pour l'auto-updater)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 curl unzip git \
    && rm -rf /var/lib/apt/lists/*

# Copier requirements.txt et installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Volumes (faces + state/myges data)
VOLUME ["/data"]

EXPOSE 44888

# Le code source est monté en volume depuis l'hôte via docker-compose
# Le script d'entrée réinstalle les deps si requirements.txt a changé
CMD ["sh", "-c", "pip install --no-cache-dir -r /app/requirements.txt 2>/dev/null; exec uvicorn main:app --host 0.0.0.0 --port 44888 --log-level info --proxy-headers"]
