FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEBNOTE_HOST=0.0.0.0 \
    WEBNOTE_PORT=8765 \
    WEBNOTE_STORAGE=/data

WORKDIR /app
COPY server.py /app/server.py
COPY API_USAGE.txt /app/API_USAGE.txt
COPY LICENSE /app/LICENSE
RUN chmod 0444 /app/server.py /app/API_USAGE.txt /app/LICENSE && mkdir /data && chown 10001:10001 /data && chmod 0700 /data
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2))['status'] == 'ok'"]
CMD ["python", "/app/server.py"]
