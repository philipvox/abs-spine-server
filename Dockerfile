FROM python:3.12-alpine

WORKDIR /app

COPY spine_server.py .

# Create the spines directory
RUN mkdir -p /spines

# Default environment
ENV SPINES_DIR=/spines
ENV ABS_URL=http://audiobookshelf:13378
ENV ABS_API_KEY=""
ENV LIBRARY_PATH=""
ENV PORT=8786

EXPOSE 8786

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost:8786/health || exit 1

ENTRYPOINT ["python3", "spine_server.py"]
