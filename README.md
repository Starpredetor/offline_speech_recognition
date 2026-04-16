# Offline Speech Recognition with Real-Time Translation

A production-grade, offline speech-to-text and real-time translation system with visual subtitle overlay and no cloud dependency.

![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Vosk](https://img.shields.io/badge/vosk-ASR-green.svg)
![Argos](https://img.shields.io/badge/argos-translation-orange.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## 📋 Table of Contents

- [Features](#-features)
- [Project Overview](#-project-overview)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Architecture](#-architecture)
- [Configuration](#-configuration)
- [Technical Specification](#-technical-specification)
- [API Documentation](#-api-documentation)
- [Performance Metrics](#-performance-metrics)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [License](#-license)

---

## ✨ Features

### 🎤 Core Functionality

- **Real-Time Speech Recognition (ASR)**
  - Offline Vosk engine with no internet required
  - Multi-language support (English, Hindi)
  - Streaming audio processing with configurable chunk sizes
  - Partial and final hypothesis generation
  - Language auto-detection capability

- **Neural Machine Translation (NMT)**
  - Argos Translate backend with offline models
  - Bidirectional English-Hindi translation
  - Translation caching for improved performance
  - Thread-safe concurrent translation processing

- **Audio Input Flexibility**
  - Microphone input with voice-focus DSP
  - System loopback capture (WASAPI support)
  - Multi-channel audio downmixing
  - Automatic resampling to 16 kHz

- **Visual Subtitle Overlay**
  - Real-time text display over captured content
  - Intelligent line wrapping (max 56 characters)
  - Language-aware sentence splitting
  - Incremental final text emission

### 🎛️ Audio Processing Features

- **Voice-Focus Preprocessing**
  - High-pass pre-emphasis filtering ($\alpha=0.95$)
  - Adaptive RMS-driven noise floor estimation
  - Piecewise gain control based on signal energy
  - Real-time voice activity detection (VAD)

- **Streaming Architecture**
  - Bounded-latency chunk queue (8-chunk buffer)
  - Adaptive chunk duration (100-140 ms)
  - Automatic overflow management with frame dropping
  - Energy proxy monitoring and audio level visualization

- **Multi-Channel Support**
  - Mono fold-down from stereo/multichannel input
  - Arithmetic mean averaging per frame
  - Automatic channel detection and conversion

### 🔧 Developer Features

- **Modular Architecture**
  - Separate ASR, translation, audio, and UI controllers
  - Pluggable audio input sources
  - Thread-safe component interfaces
  - Configurable runtime parameters

- **Debug and Monitoring**
  - Real-time debug counters (chunks, outputs, energy)
  - Latency measurement at component boundaries
  - Audio level visualization in UI
  - Comprehensive logging support

---

## 🎯 Project Overview

This system provides a complete offline pipeline for capturing audio, transcribing speech, and translating content in real-time:

$$
x(t) \rightarrow \hat{y}_{\text{ASR}} \rightarrow \hat{y}_{\text{MT}} \rightarrow \text{overlay/UI}
$$

**Key characteristics:**
- **Zero Cloud Dependency**: All models run locally
- **Low Latency**: Real-time factor < 1.0 for typical configurations
- **Streaming Design**: Processes audio in 100-140 ms chunks
- **Language Coverage**: Supports English ($en$) and Hindi ($hi$) bidirectionally

**Use cases:**
- Live lecture and meeting transcription/translation
- Accessibility features for video content
- Multilingual broadcast subtitle generation
- Language learning applications
- Research in low-latency ASR/MT systems

---

## 🚀 Installation

### Prerequisites

- Python 3.8 or higher
- Windows 7+ (for WASAPI support) or Linux/Mac with equivalent audio APIs
- 2 GB RAM minimum
- 500 MB disk space for models
- Audio input device (microphone or line-in)

### Supported Audio Backends

- **Windows**: sounddevice (WASAPI for loopback capture)
- **Linux/Mac**: sounddevice with PulseAudio/ALSA/CoreAudio
- **Alternative**: soundcard library with VB-Cable support

### Step-by-Step Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd offline_speech_recognition
   ```

2. **Create virtual environment**
   ```bash
   # Windows
   python -m venv .venv
   .venv\Scripts\activate

   # Linux/Mac
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Setup Argos Translate models**
   ```bash
   python core/argos_setup.py
   ```
   This downloads and caches translation models locally.

5. **Verify installation**
   ```bash
   python -c "import vosk, argostranslate, sounddevice; print('All packages installed successfully!')"
   ```

### Dependencies

Key packages required:

- **vosk** (>=0.3.42): Kaldi ASR engine wrapper
- **argostranslate** (>=1.8+): Offline neural machine translation
- **sounddevice** (>=0.4.5): Audio I/O for Windows/Linux/Mac
- **numpy** (>=1.20): Numerical computations
- **scipy** (>=1.7): Signal processing and resampling

See `requirements.txt` for complete list with pinned versions.

---

## 🏃 Quick Start

### Basic Usage

1. **Start the application**
   ```bash
   python main.py
   ```

2. **Select input source**
   - Choose **Microphone** for voice input
   - Choose **System Audio** for loopback capture

3. **Select languages**
   - ASR Language: English or Hindi (auto-detect available)
   - Translation: En→Hi or Hi→En

4. **Monitor output**
   - View real-time transcription in window
   - Subtitle overlay updates in real-time
   - Audio level indicator shows input energy

5. **Stop capture**
   - Click "Stop" button or press `Ctrl+C`
   - Final transcription and translations are flushed

### Example Session

```bash
$ python main.py
[INFO] Initializing audio capture (microphone)
[INFO] Loading Vosk model: vosk-model-en-us-0.42-gigaspeech
[INFO] Loading Argos model: translate-en_hi-1_1
[INFO] Audio device: Microphone (44100 Hz)
[INFO] Starting capture (chunk 120 ms, 5760 samples)
...
[PARTIAL] hello
[FINAL] hello how are you
[TRANSLATION] नमस्ते आप कैसे हैं
```

---

## 🏗️ Architecture

### System Components

```
┌─────────────────────────────────────────────┐
│           Audio Input Layer                 │
│  (Microphone / System Loopback / File)      │
└────────────────┬────────────────────────────┘
                 │
         ┌───────▼────────┐
         │  Audio Chunk   │
         │   Queue (8)    │
         └───────┬────────┘
                 │
   ┌─────────────┼─────────────┐
   │             │             │
   ▼             ▼             ▼
┌──────┐  ┌────────────┐  ┌──────────┐
│ DSP  │  │  Resampler │  │Mono Conv │
└──┬───┘  └────────────┘  └──────────┘
   │
   └─────────────┬──────────────┐
                 │              │
            ┌────▼───┐     ┌────▼──────┐
            │  Vosk  │     │ Detector  │
            │  ASR   │     │ (Lang)    │
            └────┬───┘     └───────────┘
                 │
          ┌──────▼──────┐
          │ Translation │
          │   Queue     │
          └──────┬──────┘
                 │
            ┌────▼───┐
            │ Argos  │
            │  NMT   │
            └────┬───┘
                 │
          ┌──────▼──────┐
          │   UI/Text   │
          │  Packing &  │
          │  Overlay    │
          └─────────────┘
```

### Module Organization

- **`core/`**: Core processing engines
  - `audio/input.py`: Audio source abstraction
  - `stt/vosk_engine.py`: ASR implementation
  - `translation/argos_engine.py`: NMT implementation
  - `translation/language_detector.py`: Language detection
  - `utils.py`: DSP filters and utilities
  - `controller.py`: Main pipeline orchestration

- **`ui/`**: User interface components
  - `control_window.py`: Control panel UI
  - `overlay.py`: Subtitle overlay renderer
  - `tracker.py`: Window position tracking

---

## ⚙️ Configuration

### Global Configuration (`config.py`)

#### Audio Parameters

```python
# Sample rate and format
TARGET_SAMPLE_RATE = 16000  # Hz (Vosk requirement)
TARGET_CHANNELS = 1          # mono (required)
AUDIO_DTYPE = np.int16       # signed 16-bit

# Chunk/block parameters
DEFAULT_CHUNK_DURATION_MS = 120      # target duration
MICROPHONE_CHUNK_DURATION_MS = 100   # shorter for mic
LOOPBACK_CHUNK_DURATION_MS = 140     # longer for loopback

# Queue configuration
QUEUE_SIZE_CHUNKS = 8          # max chunks in queue
OVERFLOW_POLICY = 'drop_old'   # drop oldest on overflow
```

Block size computation:

$$
N_{\text{block}} = f_s \cdot \frac{T_{\text{chunk}}}{1000}
$$

#### Voice-Focus DSP Parameters

```python
# Pre-emphasis high-pass filter
PRE_EMPHASIS_ALPHA = 0.95

# Adaptive noise floor
NOISE_FLOOR_INITIAL = 120
NOISE_FLOOR_UPDATE_FAST = (0.998, 0.002)  # if no voice
NOISE_FLOOR_UPDATE_SLOW = (0.985, 0.015)  # if voice
NOISE_FLOOR_THRESHOLD = 1.35             # voice detection threshold

# Gain control
GATE_THRESHOLD_MIN = 70
GATE_THRESHOLD_MULTIPLIER = 1.4
GAIN_LEVELS = {
    'silent': 0.18,      # below gate
    'low': 0.45,         # gate to 1.3*gate
    'medium': 0.75,      # 1.3*gate to 1.9*gate
    'high': 1.0          # above 1.9*gate
}
OUTPUT_RANGE = (-32768, 32767)  # int16 clip
```

#### Silence Handling

```python
# Voice activity detection
ENERGY_THRESHOLD = 150.0         # energy for voice
SILENCE_COUNTER_THRESHOLD = 10   # chunks
STARTUP_GUARD_CHUNKS = 10        # before first flush
```

Energy proxy per chunk:

$$
E = \frac{1}{N}\sum_{n=1}^{N}|x[n]|
$$

Audio level bar mapping:

$$
\text{level} = \text{clip}_{[0,100]}\left(\frac{E}{120}\right)
$$

#### ASR Configuration

```python
# Vosk models
VOSK_MODELS = {
    'en': 'models/vosk/vosk-model-en-us-0.42-gigaspeech',
    'hi': 'models/vosk/vosk-model-hi-0.22'
}

VOSK_SAMPLE_RATE = 16000  # strict requirement
```

#### Translation Configuration

```python
# Argos models
ARGOS_MODEL_DIR = 'models/argos'
ARGOS_CHUNK_TYPE = 'MINISBD'        # for offline mode
ARGOS_STANZA_AVAILABLE = False       # offline guarantee

# Translation cache
TRANSLATION_CACHE_SIZE = 500
TRANSLATION_WORKER_THREADS = 1      # serialized
```

#### UI Parameters

```python
# Text overlay
OVERLAY_MAX_CHARS_PER_LINE = 56
OVERLAY_MAX_WORDS_PER_LINE = 10
SENTENCE_SPLIT_REGEX = r'(?<=[.!?।])\s+'
```

---

## 🔬 Technical Specification

### 1. Scope and Problem Definition

This system performs real-time transcription and optional translation with no cloud dependency in normal operation.

Supported language set:

$$
\mathcal{L}=\{\text{en},\text{hi}\}
$$

### 2. Audio Segmentation and Streaming (Section 3.2)

Chunk/block duration:

- Primary stream default: 120 ms
- Worker-selected chunk duration:
  - 100 ms for system/WASAPI loopback path
  - 140 ms for microphone path

Blocksize equation:

$$
N_{\text{block}} = f_s \cdot \frac{T_{\text{chunk}}}{1000}
$$

Realtime queue policy:

- Queue size: 8 chunks
- On overflow: drop oldest chunk, keep newest chunk (bounded-latency design)

### 3. Channel Reduction and Resampling

For multichannel input $c>1$, mono fold-down is arithmetic mean per frame:

$$
x_{\text{mono}}[n] = \frac{1}{c}\sum_{k=1}^{c} x_k[n]
$$

If capture sample rate differs from target (16 kHz), linear interpolation resampling is applied.

### 4. Voice-Focus DSP Pipeline

**Enabled for:** Microphone input  
**Disabled for:** System loopback

Pre-emphasis/high-pass filter:

$$
y[n]=x[n]-\alpha x[n-1],\quad \alpha=0.95
$$

Adaptive RMS-driven noise floor update:

- Initial $n_f=120$
- If $r<1.35\,n_f$ (silent):
$$
n_{f,t+1}=0.985\,n_{f,t} + 0.015\,\max(1,r)
$$

- Else (voice active):
$$
n_{f,t+1}=0.998\,n_{f,t} + 0.002\,\max(1,r)
$$

Gate threshold:

$$
\theta=\max(70,1.4\,n_f)
$$

Piecewise gain control:

| Condition | Gain |
|-----------|------|
| $r < \theta$ | 0.18 |
| $\theta \le r < 1.3\theta$ | 0.45 |
| $1.3\theta \le r < 1.9\theta$ | 0.75 |
| $r \ge 1.9\theta$ | 1.0 |

Output clipping: $[-32768, 32767]$ (int16)

### 5. Silence and Voice Activity Detection

Energy proxy per chunk:

$$
E=\frac{1}{N}\sum_{n=1}^{N}|x[n]|
$$

Parameters:

| Parameter | Value |
|-----------|-------|
| Energy threshold | 150.0 |
| Silence counter threshold | 10 chunks |
| Startup guard | >10 chunks before first flush |

Audio level visualization:

$$
\text{level} = \text{clip}_{[0,100]}\left(\frac{E}{120}\right)
$$

### 6. ASR Runtime Behavior

- **Recognizer**: Vosk KaldiRecognizer (Kaldi backend)
- **Sample rate**: 16000 Hz (strict requirement)
- **State machine per chunk**:
  - **Final**: AcceptWaveform=true AND text non-empty
  - **Partial**: PartialResult() when non-empty
  - **Empty**: No hypothesis
- **Auto language mode**: Computes both EN and HI candidates, resolves via language detector

### 7. Translation Runtime Behavior

- **Backend**: Argos Translate with offline models
- **Caching**: Per language pair tuple $(l_{src}, l_{tgt})$ + text
- **Execution**: Single worker thread (max_workers=1)
- **Cache key**:
$$
(\ell_{src},\ell_{tgt},\text{text})
$$
- **Offline safeguards**:
  - ARGOS_CHUNK_TYPE = MINISBD
  - ARGOS_STANZA_AVAILABLE = False

### 8. Overlay Text Packing

| Parameter | Value |
|-----------|-------|
| Max characters per line | 56 |
| Max words fallback | 10 |
| Sentence split regex | `(?<=[.!?।])\s+` |

Incremental final emission removes prefix overlap versus previous final text per language.

---

## 📊 Performance Metrics

### Current Status (As of April 2026)

**Available metrics:**
- Live UI latency (event-to-event) in milliseconds
- Runtime debug counters (chunks, outputs, energy levels)
- Component timing breakdown

**Not yet published:**
- Dataset-level ASR accuracy (WER/CER)
- Dataset-level translation quality (BLEU/COMET)
- Reproducible benchmark scripts with frozen test sets

### ASR Evaluation Metrics

Word Error Rate:

$$
\text{WER}=\frac{S+D+I}{N}
$$

Character Error Rate:

$$
\text{CER}=\frac{S_c+D_c+I_c}{N_c}
$$

where $S,D,I$ = substitutions, deletions, insertions and $N$ = reference count.

### Translation Quality Metrics

- **BLEU**: Lexical overlap (0-100 scale)
- **chrF**: Character-level F-score
- **COMET**: Semantic quality (if model pinned)

### Streaming Performance Metrics

Real-time factor:

$$
\text{RTF}=\frac{t_{\text{processing}}}{t_{\text{audio}}}
$$

Latency reporting:

- Report mean, median, p90, p95, p99
- Separate partial and final hypothesis latencies
- 95% confidence interval:
$$
\bar{x} \pm 1.96\frac{s}{\sqrt{n}}
$$

### Computational Characteristics

| Operation | Complexity | Notes |
|-----------|-----------|-------|
| Queue push/pop | O(1) | Amortized |
| Mono fold-down | O(N·c) | Per chunk |
| Resampling | O(N) | Linear interpolation |
| Voice-focus DSP | O(N) | Pre-emphasis + RMS + gain |
| Translation | O(T) | Serialized, single worker |

---

## 📚 API Documentation

### Core Module: `controller.py`

#### Main Pipeline Class

```python
class SpeechTranslationController:
    """Orchestrates audio capture, ASR, translation, and UI."""
    
    def __init__(self, config: dict):
        """Initialize with configuration dict."""
        
    def start_capture(self, audio_source: str, asr_lang: str, translation_pair: tuple):
        """Start capture: audio_source in ['mic', 'loopback']"""
        
    def stop_capture():
        """Stop and flush all buffers."""
        
    def get_transcription() -> str:
        """Get current final transcription."""
        
    def get_translation() -> str:
        """Get current translated text."""
        
    def get_audio_level() -> int:
        """Get current audio level (0-100)."""
```

### Audio Module: `core/audio/input.py`

```python
class AudioInput:
    """Abstract base for audio sources."""
    
    def get_chunk(timeout_ms: float) -> np.ndarray:
        """Get next audio chunk (blocking with timeout)."""
        
    def close():
        """Release audio device."""

class MicrophoneInput(AudioInput):
    """Real microphone via sounddevice."""
    
class LoopbackInput(AudioInput):
    """System audio via WASAPI/loopback."""
```

### ASR Module: `core/stt/vosk_engine.py`

```python
class VoskASREngine:
    """Wraps Vosk KaldiRecognizer."""
    
    def __init__(self, language: str, sample_rate: int = 16000):
        """Load model for language."""
        
    def accept_chunk(audio: np.ndarray) -> str:
        """Return final hypothesis if available, else ""."""
        
    def get_partial() -> str:
        """Get partial hypothesis without consuming."""
        
    def reset():
        """Reset recognizer state."""
```

### Translation Module: `core/translation/argos_engine.py`

```python
class ArgosTranslationEngine:
    """Wraps Argos Translate with caching."""
    
    def __init__(self, source_lang: str, target_lang: str):
        """Load translation model pair."""
        
    def translate(text: str) -> str:
        """Translate text (cached)."""
        
    def translate_async(text: str) -> Future[str]:
        """Non-blocking translate via thread pool."""
```

### Language Detection: `core/translation/language_detector.py`

```python
class LanguageDetector:
    """Auto-detect language from text."""
    
    def detect(text: str) -> str:
        """Return language code ('en', 'hi', etc)."""
```

### Utilities: `core/utils.py`

```python
def apply_voice_focus(audio: np.ndarray, state: dict) -> np.ndarray:
    """Apply pre-emphasis, RMS gate, gain."""
    
def resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Linear interpolation resampling."""
    
def fold_down_channels(audio: np.ndarray) -> np.ndarray:
    """Convert multichannel to mono."""
```

---

## 🔧 Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'vosk'"

**Solution:**
```bash
pip install vosk argostranslate sounddevice scipy numpy
```

Verify with:
```bash
python -c "import vosk; print(vosk.__version__)"
```

### Issue: Vosk model not found

**Solution:** Run setup script:
```bash
python core/argos_setup.py
```

Models should be in:
- `models/vosk/vosk-model-en-us-0.42-gigaspeech/`
- `models/vosk/vosk-model-hi-0.22/`

### Issue: No audio input detected

**Windows (WASAPI loopback):**
- Enable "Stereo Mix" in Sound Settings
- Use `sounddevice --list` to verify device
- Try alternative with **VB-Cable** or **VoiceMeeter**

**Linux/Mac:**
- Check PulseAudio/ALSA setup
- `arecord -l` (ALSA) or `pactl list sources` (PulseAudio)
- Install: `sudo apt install alsa-utils pulseaudio-utils`

### Issue: Slow performance (RTF > 1.0)

**Causes and solutions:**
- **CPU bottleneck**: Reduce chunk size, disable voice-focus for loopback
- **Memory pressure**: Reduce translation cache size in config
- **Vosk models**: Gigaspeech is slower; consider smaller models for real-time

**Tuning:**
```python
# Reduce processing
LOOPBACK_CHUNK_DURATION_MS = 200  # larger chunks = fewer iterations
TRANSLATION_CACHE_SIZE = 200      # smaller cache
```

### Issue: Translation lagging behind ASR

**Cause:** Translation executor serializes to preserve order

**Solution:** Accept that final translations arrive ~500 ms after final text

### Issue: Overlay text not appearing

**Windows:** Ensure window tracking enabled in `ui/overlay.py`

**Linux/Mac:** May need compositor changes or window manager support

### Port/File Locking

If restarting fails:
```bash
# Kill any orphan processes
taskkill /F /IM python.exe  # Windows
pkill -9 python             # Linux/Mac
```

---

## 🤝 Contributing

### Code Style

- Follow **PEP 8** (black formatter)
- Add **docstrings** to all public functions
- Use **type hints** for function signatures
- Keep functions small and focused

### Example contribution workflow:

1. Fork the repository
2. Create feature branch: `git checkout -b feature/your-feature`
3. Make changes with type hints and docstrings
4. Test: `python main.py` with multiple input sources
5. Commit: `git commit -m 'Add feature: detailed description'`
6. Push: `git push origin feature/your-feature`
7. Open Pull Request with description

### Testing

Before submitting PR:

```bash
# Test basic audio paths
python main.py  # Test mic
python main.py  # Test loopback (Windows)

# Check for syntax errors
python -m py_compile core/**/*.py ui/**/*.py

# Type checking (optional, requires mypy)
mypy core/controller.py
```

### Adding New Features

- **New language**: Add Vosk model path + Argos pair
- **New audio backend**: Subclass `AudioInput` in `core/audio/input.py`
- **New translation library**: Subclass `TranslationEngine` in `core/translation/`

---

## 📄 License

This project is licensed under the **MIT License**.

See LICENSE file for full terms.

### Acknowledgments

- **Vosk** (Alpha Cephei) for Kaldi ASR wrappers
- **Argos Translate** for offline neural translation
- **sounddevice** for cross-platform audio I/O
- **NumPy/SciPy** for signal processing

---

## 🔮 Future Enhancements

Planned improvements for future versions:

- [ ] Streaming subtitle export (SRT/WebVTT)
- [ ] Batch processing mode for pre-recorded audio
- [ ] Phoneme-level timing alignment
- [ ] Custom Vosk model fine-tuning guide
- [ ] WebRTC integration for remote capture
- [ ] GPU acceleration (CUDA) support
- [ ] Confidence scores per hypothesis
- [ ] Speaker diarization (who-said-what)
- [ ] Real-time audio visualization
- [ ] Multiple simultaneous translations
- [ ] Database logging of transcriptions
- [ ] REST API for remote clients
- [ ] Advanced noise cancellation (RNNoise)
- [ ] Time-stamped subtitle generation

---

## 📞 Support and Resources

### Getting Help

1. Check [Troubleshooting](#-troubleshooting) section first
2. Review [API Documentation](#-api-documentation) for module details
3. Search [Issues](https://github.com/your-repo/issues) on GitHub

### External Resources

- **Vosk Documentation**: https://alphacephei.com/vosk/
- **Argos Translate**: https://www.argosopentech.com/
- **Kaldi ASR**: https://kaldi-asr.org/
- **Python Audio Guide**: https://realpython.com/python-audio/

---

## 📊 System Requirements Summary

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| **Python** | 3.8 | 3.10+ |
| **RAM** | 2 GB | 4+ GB |
| **Disk** | 500 MB | 1+ GB |
| **CPU** | Dual-core 2.0 GHz | Quad-core 2.5+ GHz |
| **Audio Input** | 16-bit, 44.1 kHz | 16-bit, 48 kHz |

---

## 🔄 Reproducibility Checklist

For publication-quality reporting, include:

**Environment:**
- Commit hash and date
- OS, CPU model, RAM, audio backend
- Python version and all dependency versions

**Configuration:**
- Model versions (Vosk/Argos exact package names)
- Sample rate, chunk duration, voice-focus toggle

**Evaluation:**
- Dataset domain (clean/noisy/far-field)
- Train/test/validation splits
- Random seed and number of repeated runs
- 95% confidence intervals with n sample size

---

## 📈 Metrics for Research Papers

When publishing results, use these exact definitions:

**Word Error Rate:**
$$
\text{WER}=\frac{S+D+I}{N}
$$

**Character Error Rate:**
$$
\text{CER}=\frac{S_c+D_c+I_c}{N_c}
$$

**Real-Time Factor:**
$$
\text{RTF}=\frac{t_{\text{processing}}}{t_{\text{audio}}}
$$

---

## 🔗 Quick Links

- [Model Download References](#-installation)
- [Configuration Parameters](#⚙️-configuration)
- [Technical Specification](#-technical-specification)
- [API Documentation](#-api-documentation)
- [Troubleshooting Guide](#-troubleshooting)

---

**Built with ❤️ using Vosk and Argos Translate** | [Report an Issue](https://github.com/your-repo/issues) | [Request a Feature](https://github.com/your-repo/issues)
