FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/market_lens.sqlite3
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    libreoffice-writer libreoffice-impress fonts-dejavu-core fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*
COPY requirements*.txt ./
ARG INSTALL_MEDIA=0
RUN pip install --no-cache-dir -r requirements-lock.txt \
    && if [ "$INSTALL_MEDIA" = "1" ]; then pip install --no-cache-dir -r requirements-media.txt; fi \
    && useradd --create-home --uid 10001 appuser
COPY app ./app
COPY --from=web /web/dist ./frontend/dist
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
