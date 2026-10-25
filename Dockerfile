FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PUID=1000 PGID=1000
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && apt-get update \
    && apt-get install -y --no-install-recommends gosu && rm -rf /var/lib/apt/lists/*
COPY app/ .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /config /downloads
EXPOSE 8080
VOLUME ["/config", "/downloads"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/health')"
ENTRYPOINT ["/entrypoint.sh"]
