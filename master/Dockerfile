FROM python:3.12-slim

# Install curl for healthchecks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

ENV HOST=0.0.0.0
ENV PORT=8000
ENV SECONDARIES=""
ENV LOG_LEVEL=INFO

EXPOSE 8000
CMD ["python", "app.py"]
