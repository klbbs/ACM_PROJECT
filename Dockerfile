FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ACM_ENV=production ACM_ENABLE_CODEX=false \
    ACM_DATA_DIR=/app/data ACM_ACCOUNTS_CONFIG=/app/account.toml ACM_STATIC_DIR=/app/frontend/dist
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps .
COPY --from=frontend /build/dist ./frontend/dist
RUN groupadd --gid 10001 acm && useradd --uid 10001 --gid acm --create-home acm && \
    mkdir -p /app/data && chown -R acm:acm /app
USER acm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)"
CMD ["acm-web", "--host", "0.0.0.0", "--port", "8000"]
