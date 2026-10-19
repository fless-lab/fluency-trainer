"""AssemblyAI adapter (asynchronous pre-recorded transcription).

Checked against the public docs: upload with POST /v2/upload, create with POST /v2/transcript
(audio_url, disfluencies=true), poll GET /v2/transcript/{id}; words carry text/start/end/confidence
with times in milliseconds.

NOT checked against the live service: the exact model selection field. It is only sent if
ASSEMBLYAI_SPEECH_MODELS is set (as "speech_models"). If the provider rejects the request, the full
error body is stored with the session and in the event log, and the audio is kept so you can retry.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from .base import ProviderError, STTResult

BASE = "https://api.assemblyai.com"
POLL_SECONDS = 2.0
TIMEOUT_SECONDS = 15 * 60


class AssemblyAISTT:
    name = "assemblyai"

    def __init__(self, api_key: str, models: tuple[str, ...] = ()):
        self.api_key = api_key
        self.models = models

    def _headers(self) -> dict[str, str]:
        return {"authorization": self.api_key}

    async def transcribe(self, audio_path: Path, mime: str, hint_seconds: float | None) -> STTResult:
        if not self.api_key:
            raise ProviderError("ASSEMBLYAI_API_KEY is not set")
        async with httpx.AsyncClient(base_url=BASE, headers=self._headers(), timeout=60.0) as client:
            upload_url = await self._upload(client, audio_path)
            body: dict[str, Any] = {
                "audio_url": upload_url,
                "disfluencies": True,
                "punctuate": True,
                "format_text": True,
                "language_code": "en",
            }
            if self.models:
                body["speech_models"] = list(self.models)
            r = await client.post("/v2/transcript", json=body)
            self._raise_for(r, "create transcript")
            job_id = r.json()["id"]

            waited = 0.0
            while True:
                r = await client.get(f"/v2/transcript/{job_id}")
                self._raise_for(r, "poll transcript")
                data = r.json()
                status = data.get("status")
                if status == "completed":
                    return self._to_result(job_id, data)
                if status == "error":
                    raise ProviderError(f"transcription failed: {data.get('error')}")
                waited += POLL_SECONDS
                if waited > TIMEOUT_SECONDS:
                    raise ProviderError(f"timed out waiting for job {job_id}")
                await asyncio.sleep(POLL_SECONDS)

    async def _upload(self, client: httpx.AsyncClient, path: Path) -> str:
        async def chunks():
            with path.open("rb") as fh:
                while True:
                    block = fh.read(1024 * 1024)
                    if not block:
                        break
                    yield block

        r = await client.post("/v2/upload", content=chunks(), timeout=300.0)
        self._raise_for(r, "upload")
        return r.json()["upload_url"]

    @staticmethod
    def _raise_for(r: httpx.Response, step: str) -> None:
        if r.status_code >= 400:
            raise ProviderError(f"{step} failed with HTTP {r.status_code}: {r.text[:1500]}")

    @staticmethod
    def _to_result(job_id: str, data: dict[str, Any]) -> STTResult:
        words = [
            {
                "text": w.get("text", ""),
                "start": int(w.get("start", 0)),
                "end": int(w.get("end", 0)),
                "confidence": w.get("confidence"),
            }
            for w in data.get("words") or []
        ]
        duration = data.get("audio_duration")
        return STTResult(
            provider="assemblyai",
            job_id=job_id,
            text=data.get("text") or "",
            words=words,
            audio_seconds=float(duration) if duration is not None else None,
            raw=data,
        )
