FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tehran

WORKDIR /app

# tzdata برای ZoneInfo("Asia/Tehran") و ffmpeg برای تبدیل ogg→mp3 در فال‌بک صوتی
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY data/tags_seed.json ./data/tags_seed.json

CMD ["python", "-m", "bot.main"]
