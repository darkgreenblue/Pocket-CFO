FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tehran

WORKDIR /app

# tzdata برای ZoneInfo("Asia/Tehran") که یادآوری شبانه به آن نیاز دارد
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY data/tags_seed.json ./data/tags_seed.json

CMD ["python", "-m", "bot.main"]
