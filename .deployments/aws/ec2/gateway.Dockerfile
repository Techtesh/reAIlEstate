# syntax=docker/dockerfile:1
ARG APPLICATION_IMAGE
FROM ${APPLICATION_IMAGE} AS application
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY .deployments/aws/gateway/nginx.conf /etc/nginx/conf.d/default.conf
# Only Caddy can reach this listener; backend services also bind to loopback.
RUN sed -i 's/listen 8080;/listen 127.0.0.1:8080;/' /etc/nginx/conf.d/default.conf \
    && sed -i 's/worker_connections  *1024;/worker_connections 4096;/' /etc/nginx/nginx.conf
COPY --from=application --chown=101:101 /app/reailEstate_common/backend/staticfiles/ /usr/share/nginx/html/static/
COPY --from=application --chown=101:101 /app/reailEstate_common/frontend/dist/builder/assets/ /usr/share/nginx/html/static/assets/
