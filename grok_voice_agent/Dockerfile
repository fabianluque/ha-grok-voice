ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY app /app/app
COPY rootfs /
RUN chmod a+x /etc/services.d/grok/run
