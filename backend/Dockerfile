# RollCaller API image. Python 3.12 (some deps have no 3.13 wheels) +
# system ffmpeg/espeak-ng. Serves app.main:app on 8000 via uvicorn.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg espeak-ng curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching. The '+cpu' torch pin needs PyTorch's
# CPU wheel index.
COPY app/requirements.txt /app/app/requirements.txt
RUN pip install --no-cache-dir -r /app/app/requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY app/ /app/app/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]