FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional photo matching (CLIP + OpenCV, see requirements-clip.txt): build with --build-arg WITH_CLIP=true, or
# `args: { WITH_CLIP: "true" }` under the backend's build in Compose. It adds CPU-only torch and OpenCV (the image grows
# to about 3.2 GB) and bakes the model weights in at /opt/models, so nothing is downloaded at run time and the /data
# backups stay small.
ARG WITH_CLIP=false
ENV HF_HOME=/opt/models/huggingface TORCH_HOME=/opt/models/torch
COPY requirements-clip.txt .
RUN if [ "$WITH_CLIP" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 && rm -rf /var/lib/apt/lists/* && \
        pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-clip.txt && \
        python -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='laion2b_s34b_b79k')" && \
        chmod -R a+rX /opt/models; \
    fi

COPY app ./app

# Non-root user; /data holds the SQLite DB and card images (mounted as a volume)
RUN useradd --system --uid 1000 cardvault && mkdir -p /data && chown cardvault /data
USER cardvault

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
