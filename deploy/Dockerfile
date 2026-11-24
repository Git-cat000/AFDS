ARG AFDS_NGINX_IMAGE=registry-1.docker.io/library/nginx:stable-alpine
ARG AFDS_DOCKER_CLI_IMAGE=registry-1.docker.io/library/docker:cli
FROM registry-1.docker.io/library/python@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS backend

ARG AFDS_APP_VERSION=1.0.0
LABEL io.afds.runtime="afds-worker-v1" \
      org.opencontainers.image.version="${AFDS_APP_VERSION}"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AFDS_DATA_DIR=/data \
    AFDS_DATABASE_URL=sqlite:////data/afds.db \
    AFDS_MODE=team \
    AFDS_WORKER_CACHE_DIR=/tmp/afds-worker \
    HOME=/tmp
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ffmpeg=7:5.1.9-0+deb12u1 \
       ca-certificates=20250419~deb12u2 \
       fonts-dejavu-core=2.37-6 \
       fonts-noto-cjk=1:20220127+repack1-1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 afds \
    && useradd --uid 10001 --gid 10001 --no-create-home afds
COPY backend/requirements.lock /tmp/afds-requirements.lock
RUN python -m pip install --no-cache-dir --only-binary=:all: --requirement /tmp/afds-requirements.lock
WORKDIR /opt/afds/backend
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./alembic.ini
USER 10001:10001
CMD ["python", "-m", "app.execution.worker"]

FROM registry-1.docker.io/library/node@sha256:0e2646ab57e77b7a384a0cb59b6815b182c77e1f9c3ba36bd8435bd9c4e6680e AS frontend-build
WORKDIR /opt/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/src ./src
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
RUN npm run build

FROM ${AFDS_DOCKER_CLI_IMAGE} AS docker-cli
FROM backend AS script-controller
USER root
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
USER 10001:10001

FROM ${AFDS_NGINX_IMAGE} AS web
COPY --from=frontend-build /opt/frontend/dist /opt/afds/web
COPY deploy/nginx.conf /etc/afds/nginx.conf
USER 10001:10001
EXPOSE 8443
ENTRYPOINT ["nginx", "-c", "/etc/afds/nginx.conf", "-g", "daemon off;"]
