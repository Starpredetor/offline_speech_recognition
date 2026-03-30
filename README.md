# Offline Speech Recognition

Real-time speech-to-text transcription and translation with a transparent overlay display. No internet connection required.

## Features

- Real-time audio transcription (Vosk)
- Offline translation (Argos Translate, EN↔HI)
- Transparent subtitle overlay (Windows/macOS/Linux)
- Multi-language support (English, Hindi)

## Requirements

- Python 3.10+
- 4-5GB disk space for models
- Supported OS: Windows, macOS, Linux

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
python core/argos_setup.py
```

## Usage

### CLI

```bash
python main.py
```

Options:
1. Real-time transcription with optional overlay
2. Transcribe audio file (WAV, MP3)
3. Translate text
4. Setup translation models
5. Exit

### Web Interface

```bash
python app.py
# Open http://localhost:5000
```

### Python API

```python
from config import CONFIG
from core import TranscriptionController

controller = TranscriptionController(CONFIG)
controller.setup_directories()

controller.run_realtime(src_lang='auto', tgt_lang='hi')
```

## Project Structure

```
core/
  audio/              Audio capture
  stt/                Speech-to-text engines
  translation/        Translation & language detection
  window/             Window tracking
  controller.py       Main orchestrator
ui/
  overlay.py          PySide6 subtitle overlay
models/               Pre-trained models
main.py              CLI entry point
app.py               Flask web app
config.py            Configuration
```

## Models

Included models:
- Vosk: en-us-0.42, hi-0.22
- Argos: translate-en_hi, translate-hi_en

Configure in `config.py`.

## Configuration

Edit `config.py`:
- Model paths and sample rate

## Docker

```bash
docker build -t offline-speech:latest .
docker run -it -p 5000:5000 offline-speech:latest
```

## Troubleshooting

**No microphone detected**
- Check system audio settings
- Ensure microphone is enabled

**Vosk model not found**
- Extract ZIP files in `models/vosk/`

**Argos not working**
- Run `python core/argos_setup.py`

**High latency**
- Use specific language instead of auto-detection



**CLI Mode:**
- Option 1: Realtime mic transcription (Vosk)
- Option 2: Translate text
- Option 3: Setup translation models

**Web GUI:**
- Select source language (Auto/EN/HI)
- Select target translation (None/EN/HI/Other)
- Click "Start Mic" to listen and transcribe
- Click "Setup Translation Models" to install offline translation

## Model Notes

- **Vosk models:** ~2GB (auto-extracted in Docker)
- **Argos models:** ~500MB (auto-setup)

Model links (for manual download):
- [Vosk English Giga](https://alphacephei.com/vosk/models/vosk-model-en-us-0.42-gigaspeech.zip)
- [Vosk Hindi](https://alphacephei.com/vosk/models/vosk-model-hi-0.22.zip)
- [Argos English-Hindi](https://argos-net.com/v1/translate-en_hi-1_1.argosmodel)
- [Argos Hindi-English](https://argos-net.com/v1/translate-hi_en-1_1.argosmodel)
