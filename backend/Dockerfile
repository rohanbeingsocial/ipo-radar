FROM python:3.12-slim

WORKDIR /app

# OCR support for scanned prospectuses is opt-in (ENABLE_OCR=1). Uncomment to bake it in:
# RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt psycopg2-binary

COPY app ./app
COPY tools ./tools

EXPOSE 8000
# Seed the sample analysis (no-op if it already exists), then serve.
CMD ["sh", "-c", "python -m app.seed_demo; uvicorn app.main:app --host 0.0.0.0 --port 8000"]
