from __future__ import annotations

import importlib
import os
from threading import RLock

from config import AppConfig


class TranslationEngine:

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._lock = RLock()
        self._cached_modules: tuple[object, object] | None = None
        self._pair_translators: dict[tuple[str, str], object] = {}
        if self.config.offline_mode:
            os.environ["ARGOS_CHUNK_TYPE"] = "MINISBD"
            os.environ["ARGOS_STANZA_AVAILABLE"] = "0"

    def _get_argos_modules(self):
        if self.config.offline_mode:
            os.environ["ARGOS_CHUNK_TYPE"] = "MINISBD"
            os.environ["ARGOS_STANZA_AVAILABLE"] = "0"
        try:
            package = importlib.import_module("argostranslate.package")
            translate = importlib.import_module("argostranslate.translate")
            return package, translate
        except ModuleNotFoundError:
            raise RuntimeError("argos-translate is not installed. Install dependencies first.")

    def _get_cached_argos_modules(self):
        with self._lock:
            if self._cached_modules is None:
                self._cached_modules = self._get_argos_modules()
            return self._cached_modules

    def _get_pair_translator(self, from_lang: str, to_lang: str):
        pair = (from_lang, to_lang)
        with self._lock:
            cached = self._pair_translators.get(pair)
            if cached is not None:
                return cached

        _package, translate = self._get_cached_argos_modules()
        installed_languages = translate.get_installed_languages()
        from_language = next((lang for lang in installed_languages if lang.code == from_lang), None)
        to_language = next((lang for lang in installed_languages if lang.code == to_lang), None)

        if from_language is None or to_language is None:
            raise RuntimeError(
                f"Argos language pair not installed: {from_lang} -> {to_lang}. Install .argosmodel files first."
            )

        translator = from_language.get_translation(to_language)
        with self._lock:
            existing = self._pair_translators.get(pair)
            if existing is not None:
                return existing
            self._pair_translators[pair] = translator
        return translator

    def install_available_packages(self, allow_online: bool = False) -> None:
        if self.config.offline_mode and not allow_online:
            raise RuntimeError(
                "Offline mode is enabled: package index refresh is disabled because it requires internet. "
                "Use local .argosmodel files with core/argos_setup.py instead."
            )

        package, _translate = self._get_cached_argos_modules()

        package.update_package_index()
        available = package.get_available_packages()
        print(f"Argos package index updated. Available packages: {len(available)}")

    def translate(self, text: str, from_lang: str, to_lang: str) -> str:
        if not text:
            return ""

        translator = self._get_pair_translator(from_lang=from_lang, to_lang=to_lang)
        try:
            return translator.translate(text)
        except Exception as exc:
            message = str(exc)
            if "raw.githubusercontent.com" in message or "stanza-resources" in message:
                raise RuntimeError(
                    "Translation attempted to access online Stanza resources. "
                    "Offline mode requires local Argos models only; restart the app after this update."
                ) from exc
            raise
