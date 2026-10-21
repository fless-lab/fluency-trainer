from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Minimal .env reader. Real environment variables win over the file."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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


def load_settings() -> Settings:
    _load_env_file(ROOT / ".env")
    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    models = tuple(
        m.strip() for m in os.environ.get("ASSEMBLYAI_SPEECH_MODELS", "").split(",") if m.strip()
    )
    return Settings(
        data_dir=data_dir,
        stt_provider=os.environ.get("STT_PROVIDER", "assemblyai").strip().lower(),
        assemblyai_key=os.environ.get("ASSEMBLYAI_API_KEY", "").strip(),
        assemblyai_models=models,
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8765")),
        llm_provider=os.environ.get("LLM_PROVIDER", "none").strip().lower(),
        llm_key=os.environ.get("LLM_API_KEY", "").strip(),
        llm_model=os.environ.get("LLM_MODEL", "").strip(),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").strip(),
        coach_max_per_day=int(os.environ.get("COACH_MAX_PER_DAY", "30")),
        llm_chat_model=os.environ.get("LLM_CHAT_MODEL", "").strip(),
        tts_provider=os.environ.get("TTS_PROVIDER", "none").strip().lower(),
        tts_key=os.environ.get("TTS_API_KEY", "").strip() or (
            os.environ.get("LLM_API_KEY", "").strip() if os.environ.get("TTS_PROVIDER", "").strip().lower() == "openrouter" else ""),
        tts_model=os.environ.get("TTS_MODEL", "").strip(),
        tts_voice=os.environ.get("TTS_VOICE", "alloy").strip(),
        tts_base_url=os.environ.get("TTS_BASE_URL", "").strip(),
        chat_max_per_day=int(os.environ.get("CHAT_MAX_PER_DAY", "400")),
        tts_max_chars_per_day=int(os.environ.get("TTS_MAX_CHARS_PER_DAY", "60000")),
    )
