# syntax=docker/dockerfile:1
# Скат frontend: Node builds the Vite app, Nginx serves it. No Node in the final image.

# ---- build ----------------------------------------------------------------------
FROM node:22-alpine AS build
WORKDIR /app

# Dependencies first, so source edits reuse this layer.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
# Logic shared with the mobile app lives next to this project (../shared); the build gets it
# as the named context "shared" (docker-compose.frontend.yml, or
# `docker build --build-context shared=../shared .`).
COPY --from=shared . /shared

# Vite embeds VITE_* into the bundle at build time; they cannot change at runtime.
# Environment variables take precedence over any .env file Vite would read.
ARG VITE_API_URL
ARG VITE_SITE_URL=""
ARG VITE_DONATE_URL=""
ARG VITE_GOOGLE_SITE_VERIFICATION=""
ARG VITE_YANDEX_VERIFICATION=""
ENV VITE_API_URL=$VITE_API_URL \
    VITE_SITE_URL=$VITE_SITE_URL \
    VITE_DONATE_URL=$VITE_DONATE_URL \
    VITE_GOOGLE_SITE_VERIFICATION=$VITE_GOOGLE_SITE_VERIFICATION \
    VITE_YANDEX_VERIFICATION=$VITE_YANDEX_VERIFICATION

RUN case "$VITE_API_URL" in \
      https://*) ;; \
      *) echo "VITE_API_URL must be an https:// origin (got '${VITE_API_URL}')" >&2; exit 1 ;; \
    esac \
 && npm run build

# ---- runtime --------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
