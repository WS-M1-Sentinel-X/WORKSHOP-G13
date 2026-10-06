# Image commune aux deux IA (vision YOLOv8n + anomalies Isolation Forest).
# Le service choisit le script à lancer (voir docker-compose.yml).
FROM python:3.12-slim

# Bibliothèques système dont OpenCV a besoin, même sans écran.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyTorch en version CPU : environ 4 fois plus léger que la version CUDA par défaut.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Le modèle YOLOv8n est intégré à l'image : pas de téléchargement le jour de la démo.
RUN python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"

COPY . .

# Conteneur non-root (architecture v2) ; /donnees reçoit le modèle appris et les captures.
RUN useradd --create-home --uid 1000 ia \
    && mkdir -p /donnees \
    && chown -R ia:ia /app /donnees
USER ia
ENV DOSSIER_DONNEES=/donnees \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    PYTHONUNBUFFERED=1

CMD ["python", "anomalies.py"]
