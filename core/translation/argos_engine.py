from __future__ import annotations

import importlib

from config import AppConfig


class TranslationEngine:

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @staticmethod
    def _get_argos_modules():
        """Load Argos Translate modules with error handling."""
        try:
            package = importlib.import_module("argostranslate.package")
            translate = importlib.import_module("argostranslate.translate")
            return package, translate
        except ModuleNotFoundError:
            raise RuntimeError("argos-translate is not installed. Install dependencies first.")

    def install_available_packages(self) -> None:
        """Update package index and show available translation models."""
        package, _translate = self._get_argos_modules()

        package.update_package_index()
        available = package.get_available_packages()
        print(f"Argos package index updated. Available packages: {len(available)}")

    def translate(self, text: str, from_lang: str, to_lang: str) -> str:
        """Translate text from one language to another.

        Args:
            text: Text to translate
            from_lang: Source language code (e.g., 'en', 'hi')
            to_lang: Target language code

        Returns:
            Translated text
        """
        _package, translate = self._get_argos_modules()

        if not text:
            return ""

        installed_languages = translate.get_installed_languages()
        from_language = next((lang for lang in installed_languages if lang.code == from_lang), None)
        to_language = next((lang for lang in installed_languages if lang.code == to_lang), None)

        if from_language is None or to_language is None:
            raise RuntimeError(
                f"Argos language pair not installed: {from_lang} -> {to_lang}. Install .argosmodel files first."
            )

        translation = from_language.get_translation(to_language)
        return translation.translate(text)
