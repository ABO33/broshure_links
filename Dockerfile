FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BROCHURE_HOST=0.0.0.0 \
    BROCHURE_PORT=5174 \
    PRAKTIS_HEADLESS=1 \
    PRAKTIS_PROFILE=/app/.praktis-browser-profile
ENV PRAKTIS_CHANNEL=

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

EXPOSE 5174

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5174/api/health', timeout=3).read()"

CMD ["python", "app.py"]
