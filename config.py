from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
MODELS_DIR = BASE_DIR / "models"


def _pick_first_existing(candidates: list[Path]) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def is_vosk_model_dir(path: Path) -> bool:
    return path.is_dir() and (path / "am" / "final.mdl").exists() and (path / "conf").exists()


def _unwrap_single_child_dir(path: Path) -> Path:
    current = path
    while current.is_dir():
        children = [child for child in current.iterdir() if child.is_dir()]
        if len(children) != 1:
            break
        if is_vosk_model_dir(current):
            break
        current = children[0]
    return current


def _pick_first_vosk_dir(patterns: list[str], fallback: Path) -> Path:
    vosk_dir = MODELS_DIR / "vosk"

    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend([path for path in vosk_dir.glob(pattern) if path.is_dir()])

    for candidate in candidates:
        normalized = _unwrap_single_child_dir(candidate)
        if is_vosk_model_dir(normalized):
            return normalized

    normalized_fallback = _unwrap_single_child_dir(fallback)
    return normalized_fallback


@dataclass(slots=True)
class AppConfig:
    whisper_model_size: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"

    vosk_model_en: Path = _pick_first_vosk_dir(
        patterns=["vosk-model-en*", "vosk-model-small-en*"],
        fallback=MODELS_DIR / "vosk" / "vosk-model-en-us-0.42-gigaspeech",
    )
    vosk_model_hi: Path = _pick_first_vosk_dir(
        patterns=["vosk-model-hi*", "vosk-model-small-hi*"],
        fallback=MODELS_DIR / "vosk" / "vosk-model-hi-0.22",
    )

    vosk_model_en_zip: Path = MODELS_DIR / "vosk" / "vosk-model-en-us-0.42-gigaspeech.zip"
    vosk_model_hi_zip: Path = MODELS_DIR / "vosk" / "vosk-model-hi-0.22.zip"

    argos_models_dir: Path = MODELS_DIR / "argos"
    argos_en_hi_package: Path = _pick_first_existing(
        [
            MODELS_DIR / "argos" / "translate-en_hi-1_1.argosmodel",
            MODELS_DIR / "argos" / "translate-en_hi.argosmodel",
        ]
    )
    argos_hi_en_package: Path = _pick_first_existing(
        [
            MODELS_DIR / "argos" / "translate-hi_en-1_1.argosmodel",
            MODELS_DIR / "argos" / "translate-hi_en.argosmodel",
        ]
    )

    whisper_models_dir: Path = MODELS_DIR / "whisper"

    sample_rate: int = 16000
    channels: int = 1


CONFIG = AppConfig()
