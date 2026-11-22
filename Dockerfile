FROM python:3.12-slim

WORKDIR /app

# .env в образ не копируется. Переменные передаются при запуске:
#   docker run --env-file .env aryuna-bot
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

CMD ["python", "bot.py"]
