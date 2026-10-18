FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Tesseract + language models, DjVu tools, Pango for WeasyPrint, and fonts that cover
# polytonic Greek, Hebrew and Arabic for the PDF/EPUB exports.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-eng tesseract-ocr-lat tesseract-ocr-grc tesseract-ocr-ell \
        tesseract-ocr-heb tesseract-ocr-ara tesseract-ocr-syr \
        tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-ita tesseract-ocr-spa \
        djvulibre-bin \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
        fonts-noto-core fonts-gfs-didot fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY static ./static

RUN mkdir -p /data
EXPOSE 8000

# One process only: the OCR queue runs in-process and resumes from the database after restarts.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
