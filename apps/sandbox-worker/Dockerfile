FROM docker.io/cloudflare/sandbox:0.12.4-python

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
