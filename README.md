# Offline Speech Recognition

Research-grade technical specification for an offline, real-time speech-to-text and translation system with subtitle overlay.

## 1. Scope and Problem Definition

This system performs real-time transcription and optional translation with no cloud dependency in normal operation.

Pipeline objective:

\[
x(t) \rightarrow \hat{y}_{\text{ASR}} \rightarrow \hat{y}_{\text{MT}} \rightarrow \text{overlay/UI}
\]

where:

- \(x(t)\): captured audio waveform
- \(\hat{y}_{\text{ASR}}\): ASR text hypothesis
- \(\hat{y}_{\text{MT}}\): translated hypothesis

Supported language set in current code path:

\[
\mathcal{L}=\{\text{en},\text{hi}\}
\]

## 2. Current Measurement Status (As of 2026-04-14)

Important: no committed benchmark result files or paper-style evaluation artifacts are present in this repository yet. Therefore, no experimentally validated WER/CER/BLEU/COMET percentages are claimed here.

What is available now:

- Live UI latency estimate (event-to-event) in milliseconds.
- Runtime debug counters (chunks, partial/final outputs, energy indicators).

What is not yet committed:

- Dataset-level ASR accuracy (WER/CER).
- Dataset-level translation quality (BLEU/chrF/COMET).
- Reproducible benchmark scripts with frozen test sets and confidence intervals.

## 3. Exact Implemented Technical Parameters

### 3.1 Global Configuration

From current implementation:

- Offline mode default: True
- Target ASR sample rate: 16000 Hz
- Default channels: 1 (mono target path)
- Vosk models:
  - English: vosk-model-en-us-0.42-gigaspeech
  - Hindi: vosk-model-hi-0.22
- Argos models:
  - translate-en_hi-1_1.argosmodel
  - translate-hi_en-1_1.argosmodel

### 3.2 Audio Segmentation and Streaming

Chunk/block duration:

- Primary stream default: 120 ms
- Worker-selected chunk duration:
  - 100 ms for system/WASAPI loopback path
  - 140 ms for microphone path

Blocksize equation:

\[
N_{\text{block}} = f_s \cdot \frac{T_{\text{chunk}}}{1000}
\]

where \(f_s\) is capture sample rate and \(T_{\text{chunk}}\) is in ms.

Realtime queue policy:

- Queue size: 8 chunks
- On overflow: drop oldest chunk, keep newest chunk (bounded-latency design)

### 3.3 Channel Reduction and Resampling

For multichannel input \(c>1\), mono fold-down is arithmetic mean per frame:

\[
x_{\text{mono}}[n] = \frac{1}{c}\sum_{k=1}^{c} x_k[n]
\]

If capture sample rate differs from target sample rate (16 kHz), linear interpolation resampling is applied.

### 3.4 Microphone Voice-Focus DSP

Voice-focus is enabled for microphone mode and disabled for loopback mode.

Pre-emphasis/high-pass stage:

\[
y[n]=x[n]-\alpha x[n-1],\quad \alpha=0.95
\]

Adaptive RMS-driven noise floor update:

- Initial \(\text{noise\_floor}=120\)
- If \(\text{RMS}<1.35\cdot\text{noise\_floor}\):

\[
\text{noise\_floor}_{t+1}=0.985\,\text{noise\_floor}_t + 0.015\,\max(1,\text{RMS})
\]

- Else:

\[
\text{noise\_floor}_{t+1}=0.998\,\text{noise\_floor}_t + 0.002\,\max(1,\text{RMS})
\]

Gate threshold:

\[
\theta=\max(70,1.4\cdot\text{noise\_floor})
\]

Piecewise gain:

- \(g=0.18\), if \(\text{RMS}<\theta\)
- \(g=0.45\), if \(\theta\le\text{RMS}<1.3\theta\)
- \(g=0.75\), if \(1.3\theta\le\text{RMS}<1.9\theta\)
- \(g=1.0\), otherwise

Output clipping range:

\[
[-32768,32767] \text{ (int16)}
\]

### 3.5 Pause/Silence Handling in Worker

Energy proxy per chunk:

\[
E=\frac{1}{N}\sum_{n=1}^{N}|x[n]|
\]

Current thresholds:

- Input energy activity threshold: 150.0
- Silence counter threshold: 10 consecutive chunks
- Additional startup guard before clearing: chunk_count > 10

Audio level bar mapping:

\[
\text{level}=\text{clip}_{[0,100]}\left(\frac{E}{120}\right)
\]

### 3.6 ASR Runtime Behavior

- Recognizer: Vosk KaldiRecognizer
- Sampling rate passed to recognizer: 16000 Hz
- State machine per chunk:
  - final when AcceptWaveform is true and text non-empty
  - partial from PartialResult when non-empty
  - empty otherwise
- Auto mode computes both en and hi candidates (if models are available), then resolves language by detector selection.

### 3.7 Translation Runtime Behavior

- Translation backend: Argos Translate
- Pair translator caching: per language pair tuple (from, to)
- Translation executor: single worker thread (max_workers = 1)
- Cache key:

\[
(\ell_{src},\ell_{tgt},\text{text})
\]

- Offline safeguards:
  - ARGOS_CHUNK_TYPE=MINISBD
  - ARGOS_STANZA_AVAILABLE=0

### 3.8 Overlay Text Packing

Current defaults:

- Max overlay characters per line: 56
- Max words fallback per line: 10
- Sentence splitting regex: (?<=[.!?।])\s+

Incremental final emission logic removes prefix overlap versus previous final text per detected language.

## 4. Computational Characteristics (Implementation-Level)

- Audio queue operations: \(O(1)\) amortized push/pop.
- Mono fold-down: \(O(N\cdot c)\) per chunk.
- Linear interpolation resampling: \(O(N)\) for target sample count.
- Voice-focus pass (pre-emphasis + RMS + gain): \(O(N)\) per chunk.
- Translation scheduling: serialized single-worker execution to avoid out-of-order text churn.

## 5. Metrics for Research Paper Reporting

Use the following exact metric definitions when publishing results.

### 5.1 ASR Metrics

Word Error Rate:

\[
\text{WER}=\frac{S+D+I}{N}
\]

Character Error Rate:

\[
\text{CER}=\frac{S_c+D_c+I_c}{N_c}
\]

where:

- \(S,D,I\): substitutions, deletions, insertions at word level
- \(S_c,D_c,I_c\): same at character level
- \(N,N_c\): reference word/character counts

### 5.2 Translation Metrics

- BLEU and chrF for lexical overlap quality.
- COMET for semantic quality (if reproducible model/version pinned).

### 5.3 Streaming Performance Metrics

Real-time factor:

\[
\text{RTF}=\frac{t_{\text{processing}}}{t_{\text{audio}}}
\]

Latency summary:

- Report mean, median, p90, p95, p99
- Report separately for partial and final hypotheses

Confidence interval (95%):

\[
\bar{x} \pm 1.96\frac{s}{\sqrt{n}}
\]

## 6. Reproducibility Checklist

For publication-quality reporting, include these fields in your experiment appendix:

- Commit hash and date
- OS, CPU, RAM, audio backend (sounddevice/soundcard)
- Model versions (Vosk and Argos exact package names)
- Sample rate, chunk duration, and voice-focus toggle
- Dataset splits and domain (clean/noisy/far-field)
- Random seed policy and number of repeated runs

## 7. Minimal Run Instructions

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python core/argos_setup.py
python main.py
```

## 8. Model Download References

- Vosk English Giga: https://alphacephei.com/vosk/models/vosk-model-en-us-0.42-gigaspeech.zip
- Vosk Hindi: https://alphacephei.com/vosk/models/vosk-model-hi-0.22.zip
- Argos English-Hindi: https://argos-net.com/v1/translate-en_hi-1_1.argosmodel
- Argos Hindi-English: https://argos-net.com/v1/translate-hi_en-1_1.argosmodel


