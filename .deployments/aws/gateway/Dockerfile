# syntax=docker/dockerfile:1
ARG APPLICATION_IMAGE
FROM ${APPLICATION_IMAGE} AS application

FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY .deployments/aws/gateway/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=application --chown=101:101 /app/reailEstate_common/backend/staticfiles/ /usr/share/nginx/html/static/
COPY --from=application --chown=101:101 /app/reailEstate_common/frontend/dist/builder/assets/ /usr/share/nginx/html/static/assets/
EXPOSE 8080
