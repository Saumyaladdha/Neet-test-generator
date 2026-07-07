FROM python:3.12-slim

WORKDIR /app

# Install system deps for pymupdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libmupdf-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV LOG_FORMAT=json

EXPOSE 8000

# Default: API server. Override CMD for workers.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
