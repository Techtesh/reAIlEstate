# syntax=docker/dockerfile:1
FROM node:24-alpine AS frontend-build
WORKDIR /build/reailEstate_common/frontend
COPY reailEstate_common/frontend/package.json reailEstate_common/frontend/package-lock.json ./
RUN npm ci
COPY reailEstate_common/frontend/ ./
RUN npm run build:services

FROM python:3.14-slim AS runtime
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/reailEstate_common/backend:/app/adminService:/app/builderService:/app/buyerService
WORKDIR /app
RUN apt-get update \
    && apt-get install --no-install-recommends -y libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY reailEstate_common/backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY reailEstate_common/backend/ ./reailEstate_common/backend/
COPY adminService/ ./adminService/
COPY builderService/ ./builderService/
COPY buyerService/ ./buyerService/
COPY --from=frontend-build /build/reailEstate_common/frontend/dist /app/reailEstate_common/frontend/dist
RUN python buyerService/manage.py collectstatic --noinput --settings=buyer_service.settings
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=2", "--access-logfile=-", "buyer_service.wsgi:application"]
