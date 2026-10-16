#!/bin/bash

cd /app
if [ "${APP_MODE:-parser}" = "mcp" ]; then
  exec /opt/avito-venv/bin/python mcp_server.py
fi

# Workaround for the first request timing out in podman
wait_seconds=1
echo "Waiting for $wait_seconds seconds..."
sleep $wait_seconds
exec /opt/avito-venv/bin/python parser_cls.py
