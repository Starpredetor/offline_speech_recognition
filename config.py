from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QStandardPaths


APP_NAME = "OfflineSpeechRecognition"


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_app_data_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    path = Path(location) if location else Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


BASE_DIR = get_base_dir()
MODELS_DIR = BASE_DIR / "models"
APP_DATA_DIR = get_app_data_dir()
LOG_DIR = APP_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


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
    app_name: str = APP_NAME
    app_data_dir: Path = APP_DATA_DIR
    log_dir: Path = LOG_DIR
    offline_mode: bool = True

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

    sample_rate: int = 16000
    channels: int = 1


CONFIG = AppConfig()
