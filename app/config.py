from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parent.parent
# Names taken from the .env file (not from the real environment). A restart must forget them, or the new process would
# inherit the old values through the environment and never read the changed file.
LOADED_FROM_FILE: set[str] = set()


def _load_env_file(path: Path) -> None:
    """Minimal .env reader. Real environment variables win over the file."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in os.environ:
            LOADED_FROM_FILE.add(key)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    stt_provider: str
    assemblyai_key: str
    assemblyai_models: tuple[str, ...]
    max_upload_bytes: int
    host: str
    port: int
    llm_provider: str = "none"
    llm_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    coach_max_per_day: int = 30
    llm_chat_model: str = ""
    tts_provider: str = "none"
    tts_key: str = ""
    tts_model: str = ""
    tts_voice: str = "alloy"
    tts_base_url: str = ""
    tts_max_chars_per_day: int = 60000
    chat_max_per_day: int = 400
    azure_speech_key: str = ""
    azure_speech_region: str = ""
    pron_locale: str = "en-US"
    pron_concurrency: int = 1
    audio_model: str = "google/gemini-3.1-flash-lite"
    audio_key: str = ""
    native_language: str = "French"
    app_password_hash: str = ""
    tls_cert: str = ""
    tls_key: str = ""


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Settings from the environment (after reading .env), or from the given mapping, which is how a saved but not yet applied
    configuration is checked."""
    if env is None:
        _load_env_file(ROOT / ".env")
        env = os.environ
    data_dir = Path(env.get("DATA_DIR", "./data"))
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    models = tuple(
        m.strip() for m in env.get("ASSEMBLYAI_SPEECH_MODELS", "").split(",") if m.strip()
    )
    return Settings(
        data_dir=data_dir,
        stt_provider=env.get("STT_PROVIDER", "assemblyai").strip().lower(),
        assemblyai_key=env.get("ASSEMBLYAI_API_KEY", "").strip(),
        assemblyai_models=models,
        max_upload_bytes=int(env.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024,
        host=env.get("HOST", "127.0.0.1"),
        port=int(env.get("PORT", "8765")),
        llm_provider=env.get("LLM_PROVIDER", "none").strip().lower(),
        llm_key=env.get("LLM_API_KEY", "").strip(),
        llm_model=env.get("LLM_MODEL", "").strip(),
        llm_base_url=env.get("LLM_BASE_URL", "").strip(),
        coach_max_per_day=int(env.get("COACH_MAX_PER_DAY", "30")),
        llm_chat_model=env.get("LLM_CHAT_MODEL", "").strip(),
        tts_provider=env.get("TTS_PROVIDER", "none").strip().lower(),
        tts_key=env.get("TTS_API_KEY", "").strip() or (
            env.get("LLM_API_KEY", "").strip() if env.get("TTS_PROVIDER", "").strip().lower() == "openrouter" else ""),
        tts_model=env.get("TTS_MODEL", "").strip(),
        tts_voice=env.get("TTS_VOICE", "alloy").strip(),
        tts_base_url=env.get("TTS_BASE_URL", "").strip(),
        chat_max_per_day=int(env.get("CHAT_MAX_PER_DAY", "400")),
        tts_max_chars_per_day=int(env.get("TTS_MAX_CHARS_PER_DAY", "60000")),
        azure_speech_key=env.get("AZURE_SPEECH_KEY", "").strip(),
        azure_speech_region=env.get("AZURE_SPEECH_REGION", "").strip().lower(),
        pron_locale=env.get("PRONUNCIATION_LOCALE", "en-US").strip(),
        pron_concurrency=int(env.get("PRONUNCIATION_CONCURRENCY", "1")),
        app_password_hash=env.get("APP_PASSWORD_HASH", "").strip(),
        tls_cert=env.get("TLS_CERT", "").strip(),
        tls_key=env.get("TLS_KEY", "").strip(),
        native_language=env.get("NATIVE_LANGUAGE", "French").strip() or "French",
        audio_model=env.get("AUDIO_COACH_MODEL", "google/gemini-3.1-flash-lite").strip(),
        audio_key=env.get("AUDIO_COACH_KEY", "").strip() or (
            env.get("TTS_API_KEY", "").strip() or env.get("LLM_API_KEY", "").strip()
            if "openrouter" in (env.get("TTS_PROVIDER", "") + env.get("LLM_PROVIDER", "")).lower() else ""),
    )
