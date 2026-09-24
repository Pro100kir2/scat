# Скат API: FastAPI + Xray scanner. The frontend is deployed separately (Vercel).
FROM python:3.11-slim

ARG XRAY_VERSION=25.1.30
ARG TARGETARCH

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && case "${TARGETARCH:-amd64}" in arm64) XRAY_ARCH=arm64-v8a ;; *) XRAY_ARCH=64 ;; esac \
 && curl -fsSL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip" \
 && unzip -j /tmp/xray.zip xray -d /usr/local/bin \
 && chmod +x /usr/local/bin/xray \
 && rm /tmp/xray.zip \
 && apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY config.txt .

RUN useradd --system --home /app skat && mkdir -p /app/data && chown skat /app/data
USER skat

ENV XRAY_BINARY=/usr/local/bin/xray \
    DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1
VOLUME ["/app/data"]
EXPOSE 8000

# One worker: the scanner binds local ports and coordinates through the cache lock.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
