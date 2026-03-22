FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    portaudio19-dev \
    ffmpeg \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY config.py .
COPY main.py .
COPY app.py .
COPY modules/ ./modules/
COPY templates/ ./templates/
COPY static/ ./static/

RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p models/vosk models/argos models/whisper

RUN cd models/vosk && \
    wget -q https://alphacephei.com/vosk/models/vosk-model-en-us-0.42-gigaspeech.zip && \
    unzip -q vosk-model-en-us-0.42-gigaspeech.zip && \
    rm vosk-model-en-us-0.42-gigaspeech.zip && \
    echo "✓ English Vosk model extracted"

RUN cd models/vosk && \
    wget -q https://alphacephei.com/vosk/models/vosk-model-hi-0.22.zip && \
    unzip -q vosk-model-hi-0.22.zip && \
    rm vosk-model-hi-0.22.zip && \
    echo "✓ Hindi Vosk model extracted"

RUN cd models/argos && \
    wget -q https://argos-net.com/v1/translate-en_hi-1_1.argosmodel && \
    wget -q https://argos-net.com/v1/translate-hi_en-1_1.argosmodel && \
    echo "✓ Argos translation models downloaded"

RUN python -c "from modules.argos_setup import setup_argos_models; ok, msg = setup_argos_models(); print(msg); exit(0 if ok else 1)"

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000')" || exit 1

CMD ["python", "app.py"]
