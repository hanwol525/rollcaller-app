# RollCaller API image.
#
# Python 3.12 + system ffmpeg (browser-audio transcode) + pip deps.
# 3.12 (not 3.13) because kokoro==0.8.4 requires Python <3.13.
# Serves app.main:app on port 8000 via uvicorn.
FROM python:3.12-slim

# System packages: ffmpeg is the only addition (audio transcoding).
# espeak-ng is needed by phonemizer for g2p fallback.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg espeak-ng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching.
COPY app/requirements.txt /app/app/requirements.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /app/app/requirements.txt

# Copy the application code.
COPY app/ /app/app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]