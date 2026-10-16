# Rebuild React from source during deployment; never ship the stale local bundle.
FROM node:22-alpine AS frontend-builder
WORKDIR /build
COPY frontend-src/package.json frontend-src/package-lock.json ./frontend-src/
RUN cd frontend-src && npm ci --no-audit --no-fund
COPY frontend-src ./frontend-src
RUN cd frontend-src && npm run typecheck && npm run build

FROM python:3.13-slim AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE_PATH=/app/data/aurora.db
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY backend ./backend
COPY assets ./assets
COPY --from=frontend-builder /build/frontend ./frontend
RUN useradd --uid 10001 --create-home aurora \
  && mkdir -p /app/data /app/logs \
  && chown -R aurora:aurora /app/data /app/logs
# Railway mounts persistent volumes as root after the build. The startup
# initializes /app/data ownership and drops to UID 10001 before serving HTTP.
USER root
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/healthz',timeout=3)" || exit 1
CMD ["python", "-m", "app.container_start"]
