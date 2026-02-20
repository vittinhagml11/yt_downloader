FROM python:3.10-slim

# Устанавливаем ffmpeg И Node.js (он нужен для обхода защиты YouTube)
RUN apt-get update && \
    apt-get install -y ffmpeg nodejs && \
    apt-get clean

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "main.py"]