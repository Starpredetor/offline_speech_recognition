from __future__ import annotations

from pathlib import Path

from config import CONFIG


def setup_argos_models() -> tuple[bool, str]:
    """Install .argosmodel files from models/argos directory into Argos Translate."""
    try:
        import argostranslate.package as package
        import argostranslate.translate as translate
    except ImportError:
        return False, "argostranslate is not installed. Run: pip install -r requirements.txt"

    argos_dir = CONFIG.argos_models_dir
    model_files = list(argos_dir.glob("*.argosmodel"))

    if not model_files:
        return False, f"No .argosmodel files found in {argos_dir}"

    print(f"Found {len(model_files)} Argos model file(s). Installing...")

    installed_count = 0
    errors: list[str] = []

    for model_file in model_files:
        try:
            print(f"  Installing {model_file.name}...")
            package.install_from_path(str(model_file))
            installed_count += 1
            print(f"    ✓ Installed")
        except Exception as exc:
            errors.append(f"{model_file.name}: {exc}")
            print(f"    ✗ Failed: {exc}")

    if errors:
        error_msg = "\n".join(errors)
        return (
            installed_count > 0,
            f"Installed {installed_count}/{len(model_files)} models. Errors:\n{error_msg}",
        )

    installed_languages = translate.get_installed_languages()
    lang_codes = [lang.code for lang in installed_languages]

    print("\nVerifying installed languages:")
    for lang in installed_languages:
        print(f"  - {lang.code}: {lang.name}")

    if "en" in lang_codes and "hi" in lang_codes:
        return True, f"✓ Successfully installed {installed_count} Argos model(s). EN↔HI translation ready."
    else:
        return (
            True,
            f"Installed {installed_count} model(s). Available: {', '.join(lang_codes)}. "
            "EN↔HI translation may require both translate-en_hi and translate-hi_en models.",
        )


if __name__ == "__main__":
    ok, message = setup_argos_models()
    print("\n" + ("=" * 60))
    print(message)
    print("=" * 60)
    exit(0 if ok else 1)
