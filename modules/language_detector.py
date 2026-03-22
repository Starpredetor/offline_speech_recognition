from __future__ import annotations

from typing import Iterable

from langdetect import DetectorFactory, LangDetectException, detect


DetectorFactory.seed = 0


class LanguageDetector:
    """Language detection helper backed by langdetect."""

    def detect_language(self, text: str, allowed: Iterable[str] | None = None, fallback: str = "en") -> str:
        normalized = text.strip()
        if not normalized:
            return fallback

        try:
            lang = detect(normalized)
        except LangDetectException:
            return fallback

        if allowed is not None:
            allowed_set = set(allowed)
            if lang not in allowed_set:
                return fallback

        return lang

    def choose_best_candidate(self, candidates: list[tuple[str, str]], fallback: str = "en") -> tuple[str, str]:
        """Selects the best transcript from candidate (lang, text) pairs."""
        non_empty = [(lang, text.strip()) for lang, text in candidates if text.strip()]
        if not non_empty:
            return "", fallback
        if len(non_empty) == 1:
            lang, text = non_empty[0]
            return text, lang

        scored: list[tuple[int, str, str]] = []
        for model_lang, text in non_empty:
            detected = self.detect_language(text, allowed=["en", "hi"], fallback=model_lang)
            word_count = len(text.split())
            score = word_count + (2 if detected == model_lang else 0)
            scored.append((score, text, detected))

        best = max(scored, key=lambda item: item[0])
        return best[1], best[2]
