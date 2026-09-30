# Скат API: FastAPI + Xray scanner, plus the provider-probe binaries it serves.
# The frontend is deployed separately.

# ---- provider probe: static binaries for every installation profile ------------------------
# Cross-compiled on the build machine's own architecture; nothing of Go ends up in the API image.
FROM --platform=$BUILDPLATFORM golang:1.24-alpine AS probe
ARG PROBE_VERSION=1.0.0
WORKDIR /src
COPY probe/go.mod ./
COPY probe/cmd ./cmd
COPY probe/internal ./internal
RUN go vet ./... && go test ./...
RUN set -eu; mkdir -p /dist; \
    build() { \
      profile="$1"; shift; \
      env CGO_ENABLED=0 GOOS=linux "$@" go build -trimpath \
        -ldflags "-s -w -X main.version=${PROBE_VERSION} -X main.profile=${profile}" \
        -o "/dist/scat-probe-${profile}" ./cmd/scat-probe; \
    }; \
    build linux-amd64    GOARCH=amd64; \
    build linux-386      GOARCH=386 GO386=softfloat; \
    build linux-arm64    GOARCH=arm64; \
    build linux-armv7    GOARCH=arm GOARM=7; \
    build linux-armv6    GOARCH=arm GOARM=6; \
    build linux-armv5    GOARCH=arm GOARM=5; \
    build linux-mips     GOARCH=mips GOMIPS=softfloat; \
    build linux-mipsle   GOARCH=mipsle GOMIPS=softfloat; \
    build linux-mips64   GOARCH=mips64 GOMIPS64=softfloat; \
    build linux-mips64le GOARCH=mips64le GOMIPS64=softfloat; \
    build linux-riscv64  GOARCH=riscv64; \
    cd /dist && echo "${PROBE_VERSION}" > VERSION && sha256sum scat-probe-* > SHA256SUMS && ls -l

# ---- API --------------------------------------------------------------------------------------
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
COPY probe/install.sh ./probe/install.sh
COPY --from=probe /dist ./probe-dist

RUN useradd --system --home /app skat && mkdir -p /app/data && chown skat /app/data
USER skat

ENV XRAY_BINARY=/usr/local/bin/xray \
    DATA_DIR=/app/data \
    PROBE_DIST_DIR=/app/probe-dist \
    PROBE_INSTALLER=/app/probe/install.sh \
    PYTHONUNBUFFERED=1
VOLUME ["/app/data"]
EXPOSE 8000

# One worker: the scanner binds local ports and coordinates through the cache lock, and
# probe long-polls are woken in-process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
