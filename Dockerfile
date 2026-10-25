FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PUID=1000 PGID=1000
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && apt-get update \
    && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
COPY app/ .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /config /data/downloads
EXPOSE 6500
VOLUME ["/config", "/data/downloads"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:6500/health')"
ENTRYPOINT ["/entrypoint.sh"]
