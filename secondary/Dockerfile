FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

ENV PORT=8001
ENV DELAY_MS=0
ENV LOG_LEVEL=INFO

EXPOSE 8001
CMD ["python", "app.py"]
