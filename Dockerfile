FROM python:3.12-slim

# tzdata so "today", midnight and the price days follow your local time (set TZ in docker-compose.yml)
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sunbird ./sunbird
COPY web ./web
COPY tools ./tools

# settings and history live in /data so they survive rebuilds
ENV SUNBIRD_CONFIG=/data/config.json \
    SUNBIRD_DB_PATH=/data/sunbird.db \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8742
CMD ["python", "-m", "sunbird"]
