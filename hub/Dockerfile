FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
# Local face models (YuNet ~230 KB, SFace ~37 MB; sha256-checked). Outside /app/models,
# which docker-compose mounts over. If the build has no network, the hub fetches them
# into /app/data/models on first start instead.
COPY app/face_local.py /tmp/face_local.py
RUN cd /tmp && HUB_FACE_MODEL_DIR=/opt/face-models python -c "import face_local as f; assert f.find_model('yunet', True) and f.find_model('sface', True)" || echo "face models not baked in"
COPY app/ /app/
COPY models/ /app/models/
RUN python /app/models/gen_tiny_edge.py || true
EXPOSE 7443 7500/udp 7501/udp 7502/udp 8080
ENV HUB_FACE_DB=/app/data/faces.db HUB_MODEL_DIR=/app/models
RUN mkdir -p /app/data
CMD ["sh", "-c", "python /app/models/gen_tiny_edge.py || true; exec python -u main.py"]
