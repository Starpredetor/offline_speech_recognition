# Offline Speech Recognition

A complete offline speech recognition system using:
- **Realtime STT:** Vosk (English/Hindi)
- **File STT:** Faster-Whisper
- **Translation:** Argos Translate (EN↔HI)
- **GUI:** Flask web interface with mic toggle and live output

## Quick Start

### Local Setup (Python 3.10+)

```bash
# 1. Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# or: source .venv/bin/activate  # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Setup translation models
python -c "from modules.argos_setup import setup_argos_models; setup_argos_models()"

# 4a. Run CLI
python main.py

# 4b. Run Web GUI
python app.py
# → Open http://localhost:5000 in browser
```

### Docker Setup

```bash
# Build image (~3-4GB, includes all models)
docker build -t offline-speech:latest .

# Run container
docker run -it --rm \
  -v /dev/snd:/dev/snd \
  -p 5000:5000 \
  offline-speech:latest

# → Open http://localhost:5000 in browser
```

## Usage

**CLI Mode:**
- Option 1: Realtime mic transcription (Vosk)
- Option 2: Transcribe audio file (Faster-Whisper)
- Option 3: Translate text
- Option 4: Setup translation models

**Web GUI:**
- Select source language (Auto/EN/HI)
- Select target translation (None/EN/HI/Other)
- Click "Start Mic" to listen and transcribe
- Click "Setup Translation Models" to install offline translation

## Model Notes

- **Vosk models:** ~2GB (auto-extracted in Docker)
- **Argos models:** ~500MB (auto-setup)
- **Whisper:** Auto-downloaded on first use (200MB-1.5GB)

Model links (for manual download):
- [Vosk English Giga](https://alphacephei.com/vosk/models/vosk-model-en-us-0.42-gigaspeech.zip)
- [Vosk Hindi](https://alphacephei.com/vosk/models/vosk-model-hi-0.22.zip)
- [Argos English-Hindi](https://argos-net.com/v1/translate-en_hi-1_1.argosmodel)
- [Argos Hindi-English](https://argos-net.com/v1/translate-hi_en-1_1.argosmodel)
