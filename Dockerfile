# ---- builder: resolve and install locked dependencies into /opt/venv ------------------------------
FROM python:3.12-slim AS builder

# CPU-only PyTorch wheels keep the image small. Set to an empty string to use the default
# (CUDA-enabled) wheels from PyPI instead.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv \
    && uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/requirements.txt \
    && if [ -n "$TORCH_INDEX_URL" ]; then \
         grep -viE '^(nvidia-|triton)' /tmp/requirements.txt > /tmp/requirements.cpu.txt; \
         VIRTUAL_ENV=/opt/venv uv pip install --index-strategy unsafe-best-match \
           --extra-index-url "$TORCH_INDEX_URL" -r /tmp/requirements.cpu.txt; \
       else \
         VIRTUAL_ENV=/opt/venv uv pip install -r /tmp/requirements.txt; \
       fi


# ---- runtime ---------------------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# opencv-python links against libGL / glib
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Match these to the owner of the host directories you mount (see MEDIA_HOST_DIR / LOG_HOST_DIR in .env).
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --gid "$APP_GID" app \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home --shell /usr/sbin/nologin app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MEDIA_DIR=/data/media \
    LOG_DIR=/var/log/staff-attendance \
    MODELS_DIR=/models/yolo \
    TORCH_HOME=/models/torch \
    YOLO_CONFIG_DIR=/models/ultralytics

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY alembic.ini pyproject.toml ./
COPY database ./database
COPY inference ./inference
COPY api ./api
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# /models is a named volume in compose; pre-creating it here gives the volume the right owner.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /data/media /var/log/staff-attendance /models/yolo /models/torch /models/ultralytics /app/testing \
    && chown -R app:app /data /var/log/staff-attendance /models /app/testing

USER app
EXPOSE 5860

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5860/healthz', timeout=4)"

ENTRYPOINT ["entrypoint.sh"]
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "5860", "--no-access-log"]
