FROM python:3.12-alpine

WORKDIR /app

COPY spine_server.py .

# /spines: spine images. /data: config.json (settings saved from the web UI),
# so it survives container re-creation — mount a volume here.
RUN mkdir -p /spines /data

# Defaults. ABS_URL / ABS_API_KEY are only used when config.json has no
# value, so settings saved in the web UI are not overridden on restart.
# Set DASHBOARD_TOKEN to use the web UI (requests arrive from the Docker
# bridge network, not localhost, so the dashboard is locked without it).
ENV PYTHONUNBUFFERED=1 \
    SPINES_DIR=/spines \
    CONFIG_FILE=/data/config.json \
    ABS_URL=http://audiobookshelf:13378 \
    PORT=8786

VOLUME ["/data"]

EXPOSE 8786

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT:-8786}/health" > /dev/null || exit 1

ENTRYPOINT ["python3", "spine_server.py", "--no-open"]
