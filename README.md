# Offline Speech Recognition (Starter)

This is a starter scaffold for an offline speech recognition system using:

- Realtime STT: Vosk
- File STT: Faster-Whisper
- Offline translation: Argos Translate

## Project Layout

- `models/` local model storage
- `modules/` pipeline modules
- `main.py` CLI entrypoint
- `config.py` runtime configuration
- `requirements.txt` Python dependencies

## Quick Start

1. Create and activate a Python 3.10+ virtual environment.
2. Install dependencies:
   - `pip install -r requirements.txt`
3. Place model files:
   - Vosk models under `models/vosk/`
   - Argos language packages under `models/argos/` and install them
   - Whisper models are auto-downloaded to `models/whisper/` on first run
4. Run:
   - `python main.py`

## Current Status

- CLI menu is ready
- File transcription pipeline is wired
- Realtime pipeline entrypoint is scaffolded (audio stream integration pending)
- Translation wrapper is wired (requires installed Argos language packs)

## Next Implementation Steps

1. Implement microphone streaming in `modules/audio_input.py`
2. Connect streaming chunks to `RealtimeSTTEngine` in `modules/realtime_stt.py`
3. Add output file exporters (txt/srt/json)
4. Add model setup helpers and validation command
