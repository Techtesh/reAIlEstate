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
# Native image processing uses opencv-python-headless; no GUI runtime is required.
COPY reailEstate_common/backend/requirements*.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-runtime.txt
RUN python -c "import cv2, numpy as np, pymupdf, psycopg; assert cv2.cvtColor(np.zeros((2, 2, 3), dtype=np.uint8), cv2.COLOR_RGB2GRAY).shape == (2, 2); pdf = pymupdf.open(); pdf.new_page(); assert pdf.tobytes().startswith(b'%PDF')"
COPY reailEstate_common/backend/ ./reailEstate_common/backend/
COPY adminService/ ./adminService/
COPY builderService/ ./builderService/
COPY buyerService/ ./buyerService/
COPY --from=frontend-build /build/reailEstate_common/frontend/dist /app/reailEstate_common/frontend/dist
RUN python buyerService/manage.py collectstatic --noinput --settings=buyer_service.settings \
    && python adminService/manage.py collectstatic --noinput --settings=admin_service.settings
EXPOSE 8000
CMD ["gunicorn", "--bind=0.0.0.0:8000", "--workers=2", "--access-logfile=-", "buyer_service.wsgi:application"]
