# Spheroid Review as an online website (Hugging Face Spaces, Render, Railway, Fly.io, any Docker host).
#   docker build -t spheroid-review .
#   docker run -p 7860:7860 -e SPHEROID_PASSWORD=secret -v $PWD/sessions:/data/sessions spheroid-review
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    SPHEROID_SESSIONS=/data/sessions \
    SPHEROID_PRELOAD_MODEL=1 \
    HF_HOME=/data/hf-cache \
    OMP_NUM_THREADS=2

RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch: a fraction of the size of the default CUDA build (free hosting has no GPU)
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
COPY requirements.txt /tmp/requirements.txt
# modal: optional GPU detection on Modal (SPHEROID_GPU=modal, see deploy/modal/sferoid_gpu.py)
RUN pip install -r /tmp/requirements.txt gunicorn modal

# Hugging Face Spaces run the container as user 1000
RUN useradd -m -u 1000 user && mkdir -p /data && chown -R user /data
USER user
WORKDIR /home/user/app
COPY --chown=user . .

EXPOSE 7860
CMD gunicorn --workers 1 --threads 8 --timeout 900 --bind 0.0.0.0:${PORT} app.wsgi:app
