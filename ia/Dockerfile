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
# Puis exporté en OpenVINO (moteur d'Intel), bien plus rapide sur le Celeron :
# - 320 px quantifié INT8 (calcul en entiers, calibré sur 8 images COCO) : le modèle de la démo ;
# - 416 px en flottant : en réserve, détecte mieux les personnes lointaines mais plus lent.
RUN pip install --no-cache-dir nncf \
    && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')" \
    && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='openvino', imgsz=320, int8=True, data='coco8.yaml')" \
    && mv yolov8n_int8_openvino_model yolov8n_320_int8_openvino_model \
    && python -c "from ultralytics import YOLO; YOLO('yolov8n.pt').export(format='openvino', imgsz=416)" \
    && mv yolov8n_openvino_model yolov8n_416_openvino_model \
    && rm -rf datasets

COPY . .
# Modèles de visage OpenCV (YuNet + SFace), vérifiés par empreinte SHA-256.
RUN python outils/telecharger_modeles.py

# Conteneur non-root (architecture v2) ; /donnees reçoit le modèle appris et les captures.
RUN useradd --create-home --uid 1000 ia \
    && mkdir -p /donnees \
    && chown -R ia:ia /app /donnees
USER ia
ENV DOSSIER_DONNEES=/donnees \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    PYTHONUNBUFFERED=1

CMD ["python", "anomalies.py"]
