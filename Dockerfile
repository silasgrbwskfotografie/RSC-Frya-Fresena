FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin \
    TMH_SERVER=1 \
    TMH_HOST=0.0.0.0 \
    TMH_PORT=8770 \
    TMH_DATA=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 1000 --home /app rsc \
 && mkdir -p /data && chown rsc /data

WORKDIR /app
COPY --chown=rsc server.py website.py ./
COPY --chown=rsc static ./static
COPY --chown=rsc web ./web

USER rsc
VOLUME ["/data"]
EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/healthz', timeout=4).status == 200 else 1)"

CMD ["python", "server.py"]
