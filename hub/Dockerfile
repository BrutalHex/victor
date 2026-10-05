FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app/ /app/
COPY models/ /app/models/
RUN python /app/models/gen_tiny_edge.py || true
EXPOSE 7443 7500/udp 7501/udp 7502/udp 8080
ENV HUB_FACE_DB=/app/data/faces.db HUB_MODEL_DIR=/app/models
RUN mkdir -p /app/data
CMD ["sh", "-c", "python /app/models/gen_tiny_edge.py || true; exec python -u main.py"]
