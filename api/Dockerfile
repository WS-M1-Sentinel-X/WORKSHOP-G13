# API des alertes Sentinel-X : Python seul (bibliothèque standard + SQLite), aucune dépendance à installer.
FROM python:3.12-slim

WORKDIR /app
COPY serveur.py .

# Conteneur non-root ; /donnees reçoit la base SQLite (volume Docker).
RUN useradd --create-home --uid 1000 api \
    && mkdir -p /donnees \
    && chown -R api:api /app /donnees
USER api
ENV DOSSIER_DONNEES=/donnees \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "serveur.py"]
