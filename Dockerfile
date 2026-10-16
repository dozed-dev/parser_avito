FROM mcr.microsoft.com/playwright:v1.52.0-noble
LABEL org.opencontainers.image.source=https://github.com/Duff89/parser_avito

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
WORKDIR /app
RUN python3 -m venv /opt/avito-venv \
    && /opt/avito-venv/bin/python -m pip install --upgrade pip \
    && /opt/avito-venv/bin/python -m pip install -r requirements.txt

COPY . /app
RUN chown -R 1000:1000 /app /opt/avito-venv
COPY entrypoint.sh /
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]

