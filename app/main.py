from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import APP_VERSION
from . import pronunciation as pron
from .conversation import CONV_VERSION, SYSTEM as CONV_SYSTEM, delays, opening_prompt, reply_prompt, split_ready, stitch
from .patterns import build_patterns
from .tts.base import TTSError
from .tts.mock import MockTTS
from .tts.openai_compat import OpenAICompatTTS
from .notebook import find_use, KINDS, RESULTS, build_suggest_messages, clean, context_around, now_iso, parse_suggestions, schedule_after
from .coach import COACH_VERSION, MIN_WORDS, annotate_transcript, build_messages, parse_feedback
from .config import Settings, load_settings
from .db import DB
from .eventlog import EventLog, utcnow
from .metrics import METRICS_VERSION, compute_metrics
from .progress import build_drills, build_progress
from .protocols import (CHECKPOINT_BY_ID, CHECKPOINTS, DRILL_PREP_SECONDS, DRILL_SECONDS, DRILL_TOPICS, PREP_SECONDS,
                        SPEAK_SECONDS, topics_for_checkpoint)
from .llm.anthropic import AnthropicLLM
from .llm.base import LLMError, LLMProvider
from .llm.mock import MockLLM
from .llm.openrouter import OpenRouterLLM
from .providers.assemblyai import AssemblyAISTT
from .providers.base import STTProvider
from .providers.mock import MockSTT

STATIC_DIR = Path(__file__).resolve().parent / "static"

ALLOWED_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class NewSession(BaseModel):
    kind: Literal["baseline", "free", "drill", "conversation"]
    group_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    drill_round: int | None = Field(default=None, ge=1, le=3)
    checkpoint: str | None = None
    topic_id: str | None = None
    topic_text: str | None = Field(default=None, max_length=500)
    planned_seconds: int | None = Field(default=None, ge=10, le=1800)
    client_info: dict[str, Any] | None = None


class Feedback(BaseModel):
    self_rating: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=4000)


class ConvStart(BaseModel):
    scenario: str = Field(default="", max_length=400)
    research: bool = False
    live_search: bool = False
    minutes: int = Field(default=10, ge=1, le=60)
    client_info: dict[str, Any] | None = None


class Rating(BaseModel):
    rater: str = Field(min_length=1, max_length=60)
    score: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class ClientEvent(BaseModel):
    type: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_.:-]+$")
    session_id: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class GapIn(BaseModel):
    intent: str = Field(min_length=2, max_length=400)
    word_index: int | None = Field(default=None, ge=0, le=100000)


class NotebookIn(BaseModel):
    kind: Literal["gap", "phrase", "word"] = "phrase"
    intent: str = Field(min_length=2, max_length=400)
    english: str | None = Field(default=None, max_length=300)
    session_id: str | None = Field(default=None, max_length=64)
    source: Literal["manual", "coach", "prep"] = "manual"


class TargetsIn(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=12)
    source: Literal["prep", "due"] = "due"


class ChooseIn(BaseModel):
    english: str = Field(min_length=1, max_length=300)


class ReviewIn(BaseModel):
    result: Literal["fluent", "hesitated", "failed"]


class StartDate(BaseModel):
    date: Date | None = None


def make_provider(settings: Settings) -> tuple[STTProvider | None, str | None]:
    if settings.stt_provider == "mock":
        return MockSTT(), None
    if settings.stt_provider == "assemblyai":
        if not settings.assemblyai_key:
            return None, "ASSEMBLYAI_API_KEY is not set. Recordings are saved and can be transcribed once it is."
        return AssemblyAISTT(settings.assemblyai_key, settings.assemblyai_models), None
    return None, f"Unknown STT_PROVIDER '{settings.stt_provider}'"


def make_llm(settings: Settings) -> tuple[LLMProvider | None, str | None]:
    """Build the language model provider, or explain why there is none. The app works without one."""
    name = settings.llm_provider
    if name in ("", "none"):
        return None, "No language model configured. Set LLM_PROVIDER, LLM_API_KEY and LLM_MODEL in .env to enable feedback."
    if name == "mock":
        return MockLLM(), None
    if name in ("openrouter", "anthropic"):
        if not settings.llm_key:
            return None, "LLM_API_KEY is not set."
        if not settings.llm_model:
            return None, "LLM_MODEL is not set. Put the exact model name from your provider."
        cls = OpenRouterLLM if name == "openrouter" else AnthropicLLM
        return cls(settings.llm_key, settings.llm_model, settings.llm_base_url), None
    return None, f"Unknown LLM_PROVIDER '{name}'. Use openrouter, anthropic or mock."


def make_tts(settings: Settings):
    """Build the voice provider, or explain why there is none. Without one the browser's own voice is used."""
    name = settings.tts_provider
    if name in ("", "none"):
        return None, "No voice configured: the browser's own voice is used."
    if name == "mock":
        return MockTTS(), None
    if name in ("openrouter", "openai"):
        if not settings.tts_key:
            return None, "TTS_API_KEY is not set (for openrouter it falls back to LLM_API_KEY)."
        if not settings.tts_model:
            return None, "TTS_MODEL is not set."
        return OpenAICompatTTS(name, settings.tts_key, settings.tts_model, settings.tts_voice, settings.tts_base_url), None
    return None, f"Unknown TTS_PROVIDER '{name}'. Use openrouter, openai or mock."


def local_today() -> Date:
    return datetime.now().astimezone().date()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    data = settings.data_dir
    for sub in ("audio", "transcripts"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    db = DB(data / "fluency.db")
    events = EventLog(data / "events.jsonl")
    provider, provider_error = make_provider(settings)
    llm, llm_error = make_llm(settings)
    chat_llm, chat_error = (llm, llm_error)
    if settings.llm_chat_model:
        chat_llm, chat_error = make_llm(dataclasses.replace(settings, llm_model=settings.llm_chat_model))
    tts, tts_error = make_tts(settings)
    coach_locks: dict[str, asyncio.Lock] = {}
    semaphore = asyncio.Semaphore(2)
    pron_on = bool(settings.azure_speech_key and settings.azure_speech_region)
    pron_gate = asyncio.Semaphore(settings.pron_concurrency)
    pron_tasks: dict[str, list[asyncio.Task]] = {}
    pron_errors: dict[str, str] = {}

    async def assess_unit(sid: str, unit: str, path: str) -> None:
        """Score the pronunciation of one audio file with Azure. Runs in the background; a failure is only recorded."""
        started = time.monotonic()
        try:
            data = await pron.assess_audio(path, settings.azure_speech_key, settings.azure_speech_region,
                                           settings.pron_locale, pron_gate)
            db.save_pronunciation(sid, unit, data)
            pron_errors.pop(sid, None)
            events.write("pron.done", sid, unit=unit, seconds=round(time.monotonic() - started, 2),
                         audio_seconds=data["seconds"], chunks=len(data["chunks"]))
        except Exception as exc:  # noqa: BLE001 - never let a scoring problem break a session
            pron_errors[sid] = str(exc)[:300]
            events.write("pron.error", sid, unit=unit, error=str(exc)[:500])

    def queue_pron(sid: str, unit: str, path: str | None) -> None:
        if pron_on and path:
            pron_tasks.setdefault(sid, []).append(asyncio.create_task(assess_unit(sid, unit, str(path))))

    ear_on = bool(pron_on and settings.audio_key and settings.audio_model)
    ear_tasks: dict[str, asyncio.Task] = {}

    def azure_units(sid: str) -> list[dict[str, Any]]:
        return [u for u in db.get_pronunciation(sid) if u["unit"] != "ear"]

    async def run_ear(sid: str) -> None:
        """When the sounds are scored, let an audio model listen to the weakest words and say what it hears."""
        started = time.monotonic()
        try:
            await wait_pron(sid, 180)
            units = azure_units(sid)
            if not units:
                return
            summary = pron.aggregate(units)
            clips = pron.pick_clips(summary)
            if not clips:
                return
            paths = dict(pron_sources(db.get_session(sid)))
            pcm = {u: await asyncio.to_thread(pron.decode_pcm16k, paths[u]) for u in {c["unit"] for c in clips} if u in paths}
            clips = [c for c in clips if c["unit"] in pcm]
            ear = await pron.listen(clips, pcm, settings.audio_key, settings.audio_model)
            db.save_pronunciation(sid, "ear", ear)
            events.write("pron.ear", sid, seconds=round(time.monotonic() - started, 2), model=settings.audio_model, clips=len(ear["clips"]))
        except Exception as exc:  # noqa: BLE001
            events.write("pron.ear_error", sid, error=str(exc)[:500])

    def queue_ear(sid: str) -> None:
        if ear_on and not (sid in ear_tasks and not ear_tasks[sid].done()):
            ear_tasks[sid] = asyncio.create_task(run_ear(sid))

    async def wait_pron(sid: str, timeout: float) -> None:
        pending = [t for t in pron_tasks.get(sid, []) if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    async def process_session(sid: str) -> None:
        async with semaphore:
            s = db.get_session(sid)
            if not s or not s["audio_path"]:
                return
            if provider is None:
                db.update_session(sid, status="error", error=provider_error or "No speech provider configured")
                events.write("process.no_provider", sid, error=provider_error)
                return
            db.update_session(sid, status="processing", attempts=s["attempts"] + 1, error=None)
            events.write("process.start", sid, provider=provider.name, attempt=s["attempts"] + 1)
            started = time.monotonic()
            try:
                result = await provider.transcribe(Path(s["audio_path"]), s["audio_mime"] or "", s["client_seconds"])
                transcript_path = data / "transcripts" / f"{sid}.json"
                tmp = transcript_path.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(
                        {"provider": result.provider, "job_id": result.job_id, "text": result.text,
                         "words": result.words, "audio_seconds": result.audio_seconds, "raw": result.raw},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.replace(tmp, transcript_path)
                computed = compute_metrics(result.words)
                if computed["values"]:
                    db.save_metrics(sid, METRICS_VERSION, computed["values"])
                db.update_session(
                    sid,
                    status="done",
                    provider=result.provider,
                    provider_job_id=result.job_id,
                    transcript_path=str(transcript_path),
                    transcript_text=result.text,
                    audio_seconds=result.audio_seconds,
                    error=None if computed["values"] else f"No metrics computed: {', '.join(computed['flags'])}",
                )
                events.write(
                    "process.done", sid, seconds=round(time.monotonic() - started, 2),
                    n_tokens=computed["n_tokens"], flags=computed["flags"],
                    audio_seconds=result.audio_seconds, job_id=result.job_id,
                )
            except Exception as exc:  # noqa: BLE001 - everything must end up in the session record
                db.update_session(sid, status="error", error=f"{type(exc).__name__}: {exc}"[:2000])
                events.write("process.error", sid, error=str(exc), trace=traceback.format_exc(limit=6))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        events.write("app.start", None, version=APP_VERSION, provider=settings.stt_provider, provider_error=provider_error,
                     metrics_version=METRICS_VERSION)
        pending = db.list_sessions(status=("uploaded", "processing"))
        tasks = []
        for s in pending:
            events.write("process.recover", s["id"], previous_status=s["status"])
            tasks.append(asyncio.create_task(process_session(s["id"])))
        app.state.tasks = tasks
        yield
        events.write("app.stop", None)

    app = FastAPI(title="Fluency Trainer", version=APP_VERSION, lifespan=lifespan)
    app.state.db, app.state.events, app.state.settings = db, events, settings
    app.state.process_session = process_session

    @app.middleware("http")
    async def audit(request: Request, call_next):
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            if request.url.path == "/" or request.url.path.startswith("/static/"):
                # always revalidate the page and its scripts, so an updated app is never shadowed by a stale cached copy
                response.headers["Cache-Control"] = "no-cache"
            return response
        finally:
            if request.url.path.startswith("/api/"):
                events.write("http", None, method=request.method, path=request.url.path, status=status,
                             ms=round((time.monotonic() - started) * 1000, 1))

    # helpers ---------------------------------------------------------------
    def need_session(sid: str) -> dict[str, Any]:
        s = db.get_session(sid)
        if not s:
            raise HTTPException(404, "session not found")
        return s

    def public_session(s: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in s.items() if k not in ("audio_path", "transcript_path", "client_info")}
        out["has_audio"] = bool(s["audio_path"])
        out["metrics"] = db.metrics_for(s["id"], METRICS_VERSION)
        out["metrics_version"] = METRICS_VERSION
        return out

    def load_transcript(s: dict[str, Any]) -> dict[str, Any] | None:
        if not s.get("transcript_path") or not Path(s["transcript_path"]).is_file():
            return None
        return json.loads(Path(s["transcript_path"]).read_text(encoding="utf-8"))

    def start_date() -> Date | None:
        raw = db.get_setting("start_date")
        return Date.fromisoformat(raw) if raw else None

    # routes ----------------------------------------------------------------
    @app.get("/api/state")
    def state() -> dict[str, Any]:
        sd = start_date()
        day = (local_today() - sd).days if sd else None
        sessions = db.list_sessions()
        cps = []
        for cp in CHECKPOINTS:
            items = []
            for t in topics_for_checkpoint(cp["id"]):
                mine = [s for s in sessions if s["checkpoint"] == cp["id"] and s["topic_id"] == t["topic_id"]]
                items.append({**t, "sessions": [{"id": s["id"], "status": s["status"], "provider": s["provider"]} for s in mine]})
            cps.append({"id": cp["id"], "label": cp["label"], "due_day": cp["due_day"], "hint": cp["hint"],
                        "due": day is not None and day >= cp["due_day"] or (day is None and cp["due_day"] == 0),
                        "topics": items})
        return {
            "version": APP_VERSION,
            "metrics_version": METRICS_VERSION,
            "start_date": sd.isoformat() if sd else None,
            "day": day,
            "provider": settings.stt_provider,
            "provider_error": provider_error,
            "prep_seconds": PREP_SECONDS,
            "speak_seconds": SPEAK_SECONDS,
            "checkpoints": cps,
            "notebook_due": len(db.list_notebook(due_before=now_iso())),
            "llm": {"configured": llm is not None, "provider": llm.name if llm else None,
                    "model": llm.model if llm else None, "error": llm_error},
            "drill_topics": DRILL_TOPICS,
            "drill_seconds": list(DRILL_SECONDS),
        }

    @app.post("/api/start")
    def set_start(body: StartDate) -> dict[str, Any]:
        d = body.date or local_today()
        db.set_setting("start_date", d.isoformat())
        events.write("start_date.set", None, start_date=d.isoformat())
        return {"start_date": d.isoformat()}

    @app.post("/api/sessions", status_code=201)
    def create_session(body: NewSession) -> dict[str, Any]:
        topic_text, topic_id, is_repeat = body.topic_text, body.topic_id, False
        planned, prep = body.planned_seconds or 120, 0
        checkpoint = None
        if body.kind == "baseline":
            if not body.checkpoint or body.checkpoint not in CHECKPOINT_BY_ID:
                raise HTTPException(422, "unknown checkpoint")
            topics = {t["topic_id"]: t for t in topics_for_checkpoint(body.checkpoint)}
            if not body.topic_id or body.topic_id not in topics:
                raise HTTPException(422, "topic does not belong to this checkpoint")
            t = topics[body.topic_id]
            topic_text, is_repeat, checkpoint = t["text"], t["is_repeat"], body.checkpoint
            planned, prep = SPEAK_SECONDS, PREP_SECONDS
        group_id = drill_round = None
        if body.kind == "drill":
            if not body.group_id or not body.drill_round:
                raise HTTPException(422, "a drill session needs group_id and drill_round")
            group_id, drill_round = body.group_id, body.drill_round
            planned, prep = DRILL_SECONDS[drill_round - 1], DRILL_PREP_SECONDS[drill_round - 1]
            mine = [s for s in db.list_sessions() if s.get("group_id") == group_id]
            if drill_round == 1:
                topic_text = (body.topic_text or "").strip()
                if not topic_text:
                    raise HTTPException(422, "round 1 needs a topic")
                if mine:
                    raise HTTPException(409, "this drill already has a first round")
            else:
                first = [s for s in mine if s["drill_round"] == 1]
                prev = [s for s in mine if s["drill_round"] == drill_round - 1 and s["audio_path"]]
                if not first or not prev:
                    raise HTTPException(409, "the previous round of this drill is not recorded yet")
                topic_text = first[-1]["topic_text"]  # the same content, told again
        if start_date() is None:
            db.set_setting("start_date", local_today().isoformat())
            events.write("start_date.auto", None, start_date=local_today().isoformat())
        sid = uuid.uuid4().hex
        db.create_session({
            "id": sid, "created_at": utcnow(), "local_date": local_today().isoformat(), "kind": body.kind,
            "checkpoint": checkpoint, "topic_id": topic_id if body.kind == "baseline" else None, "topic_text": topic_text,
            "is_repeat": int(is_repeat), "planned_seconds": planned, "prep_seconds": prep, "status": "created",
            "client_info": json.dumps(body.client_info or {}, ensure_ascii=False),
            "group_id": group_id, "drill_round": drill_round,
        })
        events.write("session.created", sid, kind=body.kind, checkpoint=checkpoint, topic_id=topic_id,
                     planned_seconds=planned, group_id=group_id, drill_round=drill_round, client_info=body.client_info)
        return {"id": sid, "planned_seconds": planned, "prep_seconds": prep, "topic_text": topic_text}

    @app.post("/api/sessions/{sid}/audio")
    async def upload_audio(sid: str, background: BackgroundTasks, file: UploadFile = File(...),
                           duration_s: float = Form(...), mime: str = Form("")) -> dict[str, Any]:
        s = need_session(sid)
        if s["audio_path"]:
            events.write("upload.duplicate", sid)
            return {"id": sid, "status": s["status"], "already": True}
        base_mime = (mime or file.content_type or "").split(";")[0].strip().lower()
        if base_mime not in ALLOWED_MIME:
            raise HTTPException(415, f"unsupported audio type '{base_mime}'")
        if not (0 < duration_s < 7200):
            raise HTTPException(422, "invalid duration")
        final = data / "audio" / f"{sid}{ALLOWED_MIME[base_mime]}"
        tmp = final.with_suffix(final.suffix + ".part")
        size = 0
        try:
            with tmp.open("wb") as fh:
                while True:
                    block = await file.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "file too large")
                    fh.write(block)
            if size == 0:
                raise HTTPException(422, "empty file")
            os.replace(tmp, final)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        db.update_session(sid, audio_path=str(final), audio_mime=base_mime, audio_bytes=size,
                          client_seconds=duration_s, status="uploaded")
        events.write("upload.saved", sid, bytes=size, mime=base_mime, client_seconds=duration_s)
        background.add_task(process_session, sid)
        queue_pron(sid, "s", str(final))  # scored at the same time as the transcription, not after it
        queue_ear(sid)
        return {"id": sid, "status": "uploaded", "bytes": size}

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        return [public_session(s) for s in db.list_sessions()]

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str, detail: bool = False) -> dict[str, Any]:
        s = need_session(sid)
        out = public_session(s)
        out["ratings"] = db.ratings_for(sid)
        if detail:
            tr = load_transcript(s)
            if tr:
                computed = compute_metrics(tr["words"])
                out["words"] = tr["words"]
                out["pauses"] = computed["pauses"]
                out["flags"] = computed["flags"]
        return out

    @app.get("/api/sessions/{sid}/audio")
    def get_audio(sid: str) -> FileResponse:
        s = need_session(sid)
        if not s["audio_path"] or not Path(s["audio_path"]).is_file():
            raise HTTPException(404, "no audio")
        return FileResponse(s["audio_path"], media_type=s["audio_mime"] or "application/octet-stream")

    @app.post("/api/sessions/{sid}/retry", status_code=202)
    async def retry(sid: str, background: BackgroundTasks) -> dict[str, Any]:
        s = need_session(sid)
        if not s["audio_path"]:
            raise HTTPException(409, "no audio to transcribe")
        if s["status"] == "processing":
            raise HTTPException(409, "already processing")
        events.write("session.retry", sid)
        background.add_task(process_session, sid)
        return {"id": sid, "status": "queued"}

    @app.post("/api/sessions/{sid}/recompute")
    def recompute(sid: str) -> dict[str, Any]:
        s = need_session(sid)
        tr = load_transcript(s)
        if not tr:
            raise HTTPException(409, "no transcript stored")
        computed = compute_metrics(tr["words"])
        if computed["values"]:
            db.save_metrics(sid, METRICS_VERSION, computed["values"])
        events.write("metrics.recomputed", sid, version=METRICS_VERSION, flags=computed["flags"])
        return {"id": sid, "version": METRICS_VERSION, "flags": computed["flags"], "values": computed["values"]}

    @app.post("/api/sessions/{sid}/feedback")
    def feedback(sid: str, body: Feedback) -> dict[str, Any]:
        need_session(sid)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        db.update_session(sid, **fields)
        events.write("session.feedback", sid, **fields)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/ratings", status_code=201)
    def add_rating(sid: str, body: Rating) -> dict[str, Any]:
        need_session(sid)
        rid = db.add_rating(sid, body.rater, body.score, body.comment)
        events.write("session.rating", sid, rater=body.rater, score=body.score)
        return {"id": rid}

    @app.post("/api/client-event", status_code=202)
    def client_event(body: ClientEvent) -> dict[str, Any]:
        events.write(f"client.{body.type}", body.session_id, **body.payload)
        return {"ok": True}

    @app.get("/api/progress")
    def progress(include_mock: bool = False) -> dict[str, Any]:
        sessions = db.list_sessions()
        metrics = {s["id"]: db.metrics_for(s["id"], METRICS_VERSION) for s in sessions if s["status"] == "done"}
        return build_progress(sessions, metrics, include_mock=include_mock)

    @app.get("/api/patterns")
    def patterns() -> dict[str, Any]:
        return build_patterns(db.latest_coaching_per_session())

    @app.get("/api/sessions/{sid}/coach")
    def get_coaching(sid: str) -> dict[str, Any]:
        need_session(sid)
        return {"coaching": db.latest_coaching(sid, COACH_VERSION)}

    def pron_sources(s: dict[str, Any]) -> list[tuple[str, str]]:
        if s["kind"] == "conversation":
            return [(str(t["idx"]), t["audio_path"]) for t in db.get_turns(s["id"])
                    if t["role"] == "user" and t["audio_path"] and Path(t["audio_path"]).is_file()]
        if s["audio_path"] and Path(s["audio_path"]).is_file():
            return [("s", s["audio_path"])]
        return []

    @app.get("/api/sessions/{sid}/pronunciation")
    def get_pronunciation(sid: str) -> dict[str, Any]:
        s = need_session(sid)
        units = azure_units(sid)
        ear = next((u for u in db.get_pronunciation(sid) if u["unit"] == "ear"), None)
        pending = any(not t.done() for t in pron_tasks.get(sid, []))
        state = ("off" if not pron_on else "pending" if pending else "done" if units
                 else "error" if sid in pron_errors else "none" if pron_sources(s) else "no_audio")
        return {"state": state, "summary": pron.aggregate(units) if units else None, "error": pron_errors.get(sid),
                "ear": ear, "ear_pending": bool(ear_on and not ear and sid in ear_tasks and not ear_tasks[sid].done())}

    @app.post("/api/sessions/{sid}/pronunciation", status_code=202)
    async def run_pronunciation(sid: str) -> dict[str, Any]:  # async: it starts background tasks, which need the event loop
        s = need_session(sid)
        if not pron_on:
            raise HTTPException(503, "Pronunciation scoring needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION in .env.")
        if any(not t.done() for t in pron_tasks.get(sid, [])):
            return {"state": "pending"}
        sources = pron_sources(s)
        if not sources:
            raise HTTPException(409, "this session has no audio to score")
        db.clear_pronunciation(sid)
        pron_errors.pop(sid, None)
        pron_tasks[sid] = []
        for unit, path in sources:
            queue_pron(sid, unit, path)
        queue_ear(sid)
        return {"state": "pending"}

    @app.post("/api/sessions/{sid}/coach")
    async def make_coaching(sid: str, regenerate: bool = False) -> dict[str, Any]:
        s = need_session(sid)
        if llm is None:
            raise HTTPException(503, llm_error or "No language model configured")
        if s["status"] != "done" or not s["transcript_text"]:
            raise HTTPException(409, "the session has no transcript yet")
        lock = coach_locks.setdefault(sid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "feedback for this session is already being generated")
        async with lock:
            existing = db.latest_coaching(sid, COACH_VERSION)
            if existing and not regenerate:
                return {"coaching": existing, "reused": True}  # no new spend
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="milliseconds")
            if db.count_coaching_since(since) + db.count_llm_calls_since(since, exclude="chat") >= settings.coach_max_per_day:
                events.write("coach.cap_reached", sid, cap=settings.coach_max_per_day)
                raise HTTPException(429, f"Daily cap of {settings.coach_max_per_day} feedback requests reached (COACH_MAX_PER_DAY).")
            if len(s["transcript_text"].split()) < MIN_WORDS:
                raise HTTPException(422, f"too little speech to give useful feedback (under {MIN_WORDS} words)")
            annotated = None
            tr = load_transcript(s)
            if tr and tr.get("words"):
                annotated = annotate_transcript(tr["words"], compute_metrics(tr["words"])["pauses"])
            known = build_patterns(db.latest_coaching_per_session())["known_tags"]
            await wait_pron(sid, 25)  # usually finished long before: it starts as soon as the audio is transcribed
            if sid in ear_tasks:
                await asyncio.wait([ear_tasks[sid]], timeout=15)
            units = azure_units(sid)
            ear = next((u for u in db.get_pronunciation(sid) if u["unit"] == "ear"), None)
            pron_text = "\n".join(x for x in (pron.for_coach(pron.aggregate(units)) if units else "", pron.ear_for_coach(ear)) if x)
            system, user = build_messages(s, db.metrics_for(sid, METRICS_VERSION), s["transcript_text"], annotated, known,
                                          pronunciation=pron_text)
            events.write("coach.start", sid, provider=llm.name, model=llm.model, version=COACH_VERSION)
            started = time.monotonic()
            try:
                result = await llm.complete(system, user, 2600)
            except LLMError as exc:
                events.write("coach.error", sid, error=str(exc))
                raise HTTPException(502, str(exc)) from exc
            try:
                content = parse_feedback(result.text, s["transcript_text"])
            except ValueError as exc:
                events.write("coach.unusable", sid, error=str(exc), raw=result.text[:2000], usage=result.usage)
                raise HTTPException(502, f"The model's answer could not be used ({exc}). Try again.") from exc
            db.add_coaching(sid, COACH_VERSION, result.provider, result.model, content, result.usage, result.text)
            events.write("coach.done", sid, seconds=round(time.monotonic() - started, 2), provider=result.provider,
                         model=result.model, usage=result.usage,
                         quotes_not_found=sum(1 for p in content["patterns"] for e in p["examples"] if not e["quote_found"]))
            return {"coaching": db.latest_coaching(sid, COACH_VERSION), "reused": False}

    # voice conversation -----------------------------------------------------
    conv_dir = data / "conv"
    conv_dir.mkdir(parents=True, exist_ok=True)
    conv_locks: dict[str, asyncio.Lock] = {}
    conv_tasks: dict[str, list[asyncio.Task]] = {}

    def day_ago() -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="milliseconds")

    def need_conv(sid: str) -> dict[str, Any]:
        s = need_session(sid)
        if s["kind"] != "conversation":
            raise HTTPException(409, "this session is not a conversation")
        return s

    async def speak_part(sid: str, idx: int, part: int, text: str) -> dict[str, Any]:
        """Make the audio of one sentence of the partner. Never raises: a failure means the browser voice is used."""
        if tts is None:
            return {"part": part, "text": text, "audio_url": None, "voice_note": tts_error}
        if db.tts_characters_since(day_ago()) + len(text) > settings.tts_max_chars_per_day:
            events.write("tts.cap_reached", sid, cap=settings.tts_max_chars_per_day)
            return {"part": part, "text": text, "audio_url": None,
                    "voice_note": f"Daily voice cap reached (TTS_MAX_CHARS_PER_DAY={settings.tts_max_chars_per_day}); the browser voice is used."}
        try:
            started = time.monotonic()
            r = await tts.speak(text)
            ext = ".mp3" if "mpeg" in r.mime else ".wav" if "wav" in r.mime else ".bin"
            (conv_dir / f"{sid}_{idx}_{part}{ext}").write_bytes(r.audio)
            db.log_tts_call(sid, r.provider, r.model, r.characters)
            events.write("tts.done", sid, idx=idx, part=part, characters=r.characters, provider=r.provider, model=r.model,
                         seconds=round(time.monotonic() - started, 2))
            return {"part": part, "text": text, "audio_url": f"/api/conversations/{sid}/turns/{idx}/audio?part={part}", "voice_note": None}
        except TTSError as exc:
            events.write("tts.error", sid, idx=idx, part=part, error=str(exc))
            return {"part": part, "text": text, "audio_url": None, "voice_note": str(exc)}

    def chat_prompt(sid: str, s: dict[str, Any], opening: bool) -> str:
        scenario = s["topic_text"] or "A relaxed, friendly chat."
        brief = db.get_setting(f"brief:{sid}") or ""
        return opening_prompt(scenario, brief) if opening else reply_prompt(scenario, db.get_turns(sid), brief)

    def chat_gate(sid: str) -> None:
        if chat_llm is None:
            raise HTTPException(503, chat_error or "No language model configured")
        if db.count_llm_calls_since(day_ago(), purpose="chat") >= settings.chat_max_per_day:
            events.write("chat.cap_reached", sid, cap=settings.chat_max_per_day)
            raise HTTPException(429, f"Daily cap of {settings.chat_max_per_day} partner replies reached (CHAT_MAX_PER_DAY).")

    async def stream_partner(sid: str, s: dict[str, Any], opening: bool):
        """Yield events: sentence (with its audio, in order), then done. The voice of sentence 2 is made while sentence 1 plays."""
        chat_gate(sid)
        prompt = chat_prompt(sid, s, opening)
        db.log_llm_call("chat", sid)
        idx = None
        started = time.monotonic()
        first_text_s = first_audio_s = None
        buffer, sentences, jobs = "", [], []
        idx = db.add_turn(sid, "partner", "")
        words_budget = 60

        def launch(text: str) -> None:
            part = len(sentences)
            sentences.append(text)
            jobs.append(asyncio.create_task(speak_part(sid, idx, part, text)))

        try:
            use_web = bool(db.get_setting(f"livesearch:{sid}")) and hasattr(chat_llm, "research") and not opening
            for attempt in (0, 1):
                try:
                    async for piece in chat_llm.stream(CONV_SYSTEM, prompt, 220 if not use_web else 400, web=use_web):
                        if first_text_s is None:
                            first_text_s = round(time.monotonic() - started, 2)
                        buffer += piece
                        ready, buffer = split_ready(buffer)
                        for text in ready:
                            if sum(len(x.split()) for x in sentences) >= words_budget:
                                break
                            launch(text)
                            while jobs and jobs[0].done():
                                out = jobs.pop(0).result()
                                first_audio_s = first_audio_s or round(time.monotonic() - started, 2)
                                yield {"type": "sentence", "idx": idx, **out}
                            await asyncio.sleep(0)
                    if first_text_s is None and use_web and attempt == 0:  # the search ended without any spoken text
                        events.write("chat.empty_with_search", sid, debug=getattr(chat_llm, "last_debug", None))
                        use_web = False
                        continue
                    break
                except LLMError as exc:
                    if use_web and attempt == 0 and first_text_s is None:  # the chat model may not support search: retry without
                        events.write("chat.search_unavailable", sid, error=str(exc)[:300])
                        db.set_setting(f"livesearch:{sid}", "")
                        use_web = False
                        continue
                    raise
        except LLMError as exc:
            events.write("chat.error", sid, error=str(exc))
            for j in jobs:
                j.cancel()
            if not sentences:
                db.set_turn_text(sid, idx, "")
                raise HTTPException(502, str(exc)) from exc
            yield {"type": "error", "message": str(exc)}
        ready, buffer = split_ready(buffer, final=True)
        for text in ready:
            if sum(len(x.split()) for x in sentences) >= words_budget:
                break
            launch(text)
        while jobs:
            out = await jobs.pop(0)
            first_audio_s = first_audio_s or round(time.monotonic() - started, 2)
            yield {"type": "sentence", "idx": idx, **out}
        full = " ".join(sentences).strip()
        if not full:
            events.write("chat.empty", sid, debug=getattr(chat_llm, "last_debug", None), buffer=buffer[:200])
            raise HTTPException(502, "The model returned an empty reply. Try again.")
        db.set_turn_text(sid, idx, full)
        events.write("chat.reply", sid, seconds=round(time.monotonic() - started, 2), first_text_s=first_text_s,
                     first_audio_s=first_audio_s, model=chat_llm.model, sentences=len(sentences), opening=opening)
        yield {"type": "done", "idx": idx, "first_text_s": first_text_s, "first_audio_s": first_audio_s,
               "total_s": round(time.monotonic() - started, 2)}

    @app.get("/api/conversations/config")
    def conv_config() -> dict[str, Any]:
        return {"llm_ready": chat_llm is not None, "llm_error": chat_error, "voice": tts.name if tts else None,
                "voice_model": tts.model if tts else None, "voice_note": tts_error,
                "chat_model": chat_llm.model if chat_llm else None,
                "live_stt": settings.stt_provider == "assemblyai" and bool(settings.assemblyai_key)}

    @app.get("/api/conversations/stream-token")
    async def stream_token() -> dict[str, Any]:
        """A short-lived token so the browser can send audio straight to AssemblyAI without ever seeing the real key."""
        if settings.stt_provider != "assemblyai" or not settings.assemblyai_key:
            raise HTTPException(503, "Live transcription needs STT_PROVIDER=assemblyai and its key.")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get("https://streaming.assemblyai.com/v3/token", params={"expires_in_seconds": 300},
                                     headers={"Authorization": settings.assemblyai_key})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"token request failed: {exc}") from exc
        if r.status_code >= 400:
            events.write("conv.token_error", None, status=r.status_code, body=r.text[:300])
            raise HTTPException(502, f"AssemblyAI refused the live token: HTTP {r.status_code}: {r.text[:300]}")
        token = (r.json() or {}).get("token")
        if not token:
            raise HTTPException(502, "AssemblyAI returned no token")
        return {"token": token, "url": "wss://streaming.assemblyai.com/v3/ws", "sample_rate": 16000}

    @app.post("/api/conversations", status_code=201)
    async def conv_start(body: ConvStart) -> dict[str, Any]:
        chat_gate("new")
        scenario = body.scenario.strip() or "A relaxed, friendly chat."
        if start_date() is None:
            db.set_setting("start_date", local_today().isoformat())
        sid = uuid.uuid4().hex
        db.create_session({
            "id": sid, "created_at": utcnow(), "local_date": local_today().isoformat(), "kind": "conversation",
            "checkpoint": None, "topic_id": None, "topic_text": scenario, "is_repeat": 0,
            "planned_seconds": body.minutes * 60, "prep_seconds": 0, "status": "created",
            "client_info": json.dumps(body.client_info or {}, ensure_ascii=False), "group_id": None, "drill_round": None,
        })
        events.write("conv.start", sid, scenario=scenario, minutes=body.minutes, voice=tts.name if tts else None,
                     research=body.research, live_search=body.live_search)
        if body.live_search:
            db.set_setting(f"livesearch:{sid}", "1")
        research_note = None
        if body.research:
            researcher = next((m for m in (llm, chat_llm) if m is not None and hasattr(m, "research")), None)
            if researcher is None:
                research_note = "The partner could not read up on the topic: this model provider has no web search here (use LLM_PROVIDER=anthropic)."
            else:
                started = time.monotonic()
                try:
                    brief = await researcher.research(scenario, local_today().isoformat())
                    db.set_setting(f"brief:{sid}", brief)
                    db.log_llm_call("research", sid)
                    events.write("conv.research", sid, seconds=round(time.monotonic() - started, 2), model=researcher.model, chars=len(brief))
                except LLMError as exc:
                    events.write("conv.research_error", sid, error=str(exc))
                    research_note = f"The partner could not read up on the topic ({exc}). It will talk from general knowledge."
        sentences = []
        try:
            async for ev in stream_partner(sid, db.get_session(sid), opening=True):
                if ev["type"] == "sentence":
                    sentences.append(ev)
        except HTTPException:
            db.update_session(sid, status="error", error="The partner could not start the conversation.")
            raise
        return {"id": sid, "scenario": scenario, "sentences": sentences, "research_note": research_note,
                "researched": bool(db.get_setting(f"brief:{sid}"))}

    @app.get("/api/conversations/{sid}/turns/{idx}/audio")
    def conv_turn_audio(sid: str, idx: int, part: int = 0) -> FileResponse:
        need_conv(sid)
        for f in conv_dir.glob(f"{sid}_{idx}_{part}.*"):
            return FileResponse(f)
        raise HTTPException(404, "no audio for this turn")

    async def batch_words(sid: str, idx: int, path: Path, mime: str, seconds: float) -> None:
        """Transcribe a turn with the normal provider (timed words, fillers kept). Runs while the conversation goes on."""
        try:
            async with semaphore:
                result = await provider.transcribe(path, mime, seconds)
            if result.words:
                db.set_turn_words(sid, idx, result.words, result.text)
            events.write("conv.batch_stt", sid, idx=idx, words=len(result.words))
        except Exception as exc:  # noqa: BLE001
            events.write("conv.batch_stt_error", sid, idx=idx, error=str(exc))

    @app.post("/api/conversations/{sid}/turn")
    async def conv_turn(sid: str, file: UploadFile = File(...), duration_s: float = Form(...), mime: str = Form(""),
                        latency_ms: int | None = Form(None), live_text: str = Form("")) -> StreamingResponse:
        s = need_conv(sid)
        if s["status"] == "done":
            raise HTTPException(409, "this conversation is already finished")
        if provider is None:
            raise HTTPException(503, provider_error or "No speech provider configured")
        base_mime = (mime or file.content_type or "").split(";")[0].strip().lower()
        if base_mime not in ALLOWED_MIME:
            raise HTTPException(415, f"unsupported audio type '{base_mime}'")
        lock = conv_locks.setdefault(sid, asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "the previous turn is still being handled")
        blob = await file.read()
        if not blob or len(blob) > settings.max_upload_bytes:
            raise HTTPException(422, "empty or too large recording")
        n = len([t for t in db.get_turns(sid) if t["role"] == "user"])
        path = conv_dir / f"{sid}_user{n}{ALLOWED_MIME[base_mime]}"
        path.write_bytes(blob)
        live = " ".join(live_text.split())
        started = time.monotonic()
        stt_s, user_idx, text, words = 0.0, None, live, None
        if not live:  # no live transcript: transcribe now, as before
            try:
                result = await provider.transcribe(path, base_mime, duration_s)
            except Exception as exc:  # noqa: BLE001
                events.write("conv.stt_error", sid, error=str(exc))
                raise HTTPException(502, f"Transcription failed: {exc}") from exc
            stt_s = round(time.monotonic() - started, 2)
            events.write("conv.stt", sid, seconds=stt_s, words=len(result.words))
            text, words = (result.text or "").strip(), result.words
            if not words or not text:
                async def empty():
                    yield json.dumps({"type": "empty"}) + "\n"
                return StreamingResponse(empty(), media_type="application/x-ndjson")
        user_idx = db.add_turn(sid, "user", text, words=words, audio_path=str(path), latency_ms=latency_ms, speech_ms=int(duration_s * 1000))
        queue_pron(sid, str(user_idx), str(path))  # scored while the transcript and the reply are being made
        if words is None:  # the timed words come later, in the background
            conv_tasks.setdefault(sid, []).append(asyncio.create_task(batch_words(sid, user_idx, path, base_mime, duration_s)))
        if db.get_session(sid)["status"] == "created":
            db.update_session(sid, status="talking")
        events.write("conv.user_turn", sid, idx=user_idx, live=bool(live), words=len(text.split()))
        chat_gate(sid)

        async def body():
            async with lock:
                yield json.dumps({"type": "user", "text": text, "live": bool(live)}) + "\n"
                try:
                    async for ev in stream_partner(sid, s, opening=False):
                        if ev["type"] == "done":
                            ev["timing"] = {"stt_s": stt_s, "first_text_s": ev["first_text_s"], "first_audio_s": ev["first_audio_s"],
                                            "total_s": ev["total_s"]}
                            events.write("conv.turn_timing", sid, **ev["timing"], live=bool(live))
                        yield json.dumps(ev) + "\n"
                except HTTPException as exc:
                    yield json.dumps({"type": "error", "message": exc.detail}) + "\n"
        return StreamingResponse(body(), media_type="application/x-ndjson")

    @app.post("/api/conversations/{sid}/end")
    async def conv_end(sid: str) -> dict[str, Any]:
        s = need_conv(sid)
        if s["status"] == "done":
            return {"id": sid, "status": "done", "already": True}
        pending = [t for t in conv_tasks.pop(sid, []) if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=180)
        turns = db.get_turns(sid)
        words, text, seconds = stitch([t for t in turns if t["role"] == "user"])
        if not words:
            db.update_session(sid, status="error", error="No speech was captured in this conversation.")
            events.write("conv.end_empty", sid)
            return {"id": sid, "status": "error", "error": "No speech was captured."}
        transcript_path = data / "transcripts" / f"{sid}.json"
        transcript_path.write_text(json.dumps({"provider": "conversation", "job_id": None, "text": text, "words": words,
                                                "audio_seconds": seconds, "raw": {"conv_version": CONV_VERSION}},
                                               ensure_ascii=False), encoding="utf-8")
        computed = compute_metrics(words)
        if computed["values"]:
            db.save_metrics(sid, METRICS_VERSION, computed["values"])
        queue_ear(sid)
        d = delays(turns)
        missing = sum(1 for t in turns if t["role"] == "user" and not t["words"])
        db.update_session(sid, status="done", provider="conversation", transcript_path=str(transcript_path),
                          transcript_text=text, audio_seconds=seconds, client_seconds=seconds,
                          error=None if computed["values"] else f"No metrics computed: {', '.join(computed['flags'])}")
        events.write("conv.end", sid, user_turns=sum(1 for t in turns if t["role"] == "user"), turns_without_words=missing,
                     speech_seconds=round(seconds, 1), delays_ms=d)
        return {"id": sid, "status": "done", "delays_ms": d, "turns_without_words": missing}

    @app.get("/api/conversations/{sid}")
    def conv_get(sid: str) -> dict[str, Any]:
        s = need_conv(sid)
        turns = [{k: t[k] for k in ("idx", "role", "text", "latency_ms", "created_at")} for t in db.get_turns(sid)]
        return {"session": public_session(s), "turns": turns, "delays_ms": delays(db.get_turns(sid))}

    # notebook ---------------------------------------------------------------
    def need_item(item_id: int) -> dict[str, Any]:
        item = db.get_notebook(item_id)
        if not item:
            raise HTTPException(404, "notebook item not found")
        return item

    @app.get("/api/notebook")
    def notebook_list(due: bool = False, session_id: str | None = None) -> dict[str, Any]:
        items = db.list_notebook(due_before=now_iso() if due else None, session_id=session_id)
        n_due = len(db.list_notebook(due_before=now_iso()))
        return {"items": items, "due": n_due}

    @app.post("/api/notebook", status_code=201)
    def notebook_add(body: NotebookIn) -> dict[str, Any]:
        if body.session_id:
            need_session(body.session_id)
        english = clean(body.english or "", 300) or None
        item_id = db.add_notebook({
            "created_at": now_iso(), "kind": body.kind, "source": body.source, "session_id": body.session_id,
            "intent": clean(body.intent, 400), "english": english,
            "status": "learning" if english else "new", "step": 0, "due_at": now_iso() if english else None,
        })
        events.write("notebook.add", body.session_id, item=item_id, kind=body.kind, source=body.source, has_english=bool(english))
        return {"item": db.get_notebook(item_id)}

    @app.post("/api/sessions/{sid}/gaps", status_code=201)
    def gap_add(sid: str, body: GapIn) -> dict[str, Any]:
        s = need_session(sid)
        tr = load_transcript(s)
        words = (tr or {}).get("words") or []
        context, pause_ms = "", None
        if body.word_index is not None and words:
            context = context_around(words, body.word_index)
            ws = {w["before_word_index"]: w for w in compute_metrics(words)["pauses"]}
            pause_ms = ws[body.word_index]["duration_ms"] if body.word_index in ws else None
        item_id = db.add_notebook({
            "created_at": now_iso(), "kind": "gap", "source": "gap", "session_id": sid,
            "word_index": body.word_index, "pause_ms": pause_ms, "intent": clean(body.intent, 400),
            "context": context, "status": "new", "step": 0,
        })
        events.write("notebook.gap", sid, item=item_id, word_index=body.word_index, pause_ms=pause_ms)
        return {"item": db.get_notebook(item_id)}

    @app.post("/api/notebook/{item_id}/suggest")
    async def notebook_suggest(item_id: int) -> dict[str, Any]:
        item = need_item(item_id)
        if llm is None:
            raise HTTPException(503, llm_error or "No language model configured")
        lock = coach_locks.setdefault(f"nb{item_id}", asyncio.Lock())
        if lock.locked():
            raise HTTPException(409, "suggestions for this item are already being generated")
        async with lock:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="milliseconds")
            if db.count_coaching_since(since) + db.count_llm_calls_since(since) >= settings.coach_max_per_day:
                events.write("notebook.cap_reached", item["session_id"], item=item_id, cap=settings.coach_max_per_day)
                raise HTTPException(429, f"Daily cap of {settings.coach_max_per_day} language model requests reached (COACH_MAX_PER_DAY).")
            topic = None
            if item["session_id"]:
                sess = db.get_session(item["session_id"])
                topic = sess["topic_text"] if sess else None
            system, user = build_suggest_messages(item["intent"], item["context"] or "", topic)
            db.log_llm_call("notebook.suggest", str(item_id))
            events.write("notebook.suggest.start", item["session_id"], item=item_id, provider=llm.name, model=llm.model)
            try:
                result = await llm.complete(system, user, 700)
            except LLMError as exc:
                events.write("notebook.suggest.error", item["session_id"], item=item_id, error=str(exc))
                raise HTTPException(502, str(exc)) from exc
            try:
                content = parse_suggestions(result.text)
            except ValueError as exc:
                events.write("notebook.suggest.unusable", item["session_id"], item=item_id, error=str(exc), raw=result.text[:2000])
                raise HTTPException(502, f"The model's answer could not be used ({exc}). Try again.") from exc
            db.update_notebook(item_id, suggestions=content, suggested_at=now_iso(),
                               suggest_provider=result.provider, suggest_model=result.model)
            events.write("notebook.suggest.done", item["session_id"], item=item_id, usage=result.usage, options=len(content["options"]))
            return {"item": db.get_notebook(item_id)}

    @app.post("/api/notebook/{item_id}/choose")
    def notebook_choose(item_id: int, body: ChooseIn) -> dict[str, Any]:
        item = need_item(item_id)
        db.update_notebook(item_id, english=clean(body.english, 300), status="learning",
                           due_at=item["due_at"] or now_iso())
        events.write("notebook.choose", item["session_id"], item=item_id)
        return {"item": db.get_notebook(item_id)}

    @app.post("/api/notebook/{item_id}/review")
    def notebook_review(item_id: int, body: ReviewIn) -> dict[str, Any]:
        item = need_item(item_id)
        if not item["english"]:
            raise HTTPException(409, "choose the English version before reviewing this item")
        nxt = schedule_after(item["step"], body.result)
        db.add_notebook_review(item_id, body.result, item["step"], nxt["step"])
        db.update_notebook(item_id, step=nxt["step"], status=nxt["status"], due_at=nxt["due_at"],
                           reviews=item["reviews"] + 1, last_reviewed_at=now_iso())
        events.write("notebook.review", item["session_id"], item=item_id, result=body.result,
                     step_before=item["step"], step_after=nxt["step"])
        return {"item": db.get_notebook(item_id)}

    @app.get("/api/notebook/suggested-targets")
    def suggested_targets(limit: int = 3) -> dict[str, Any]:
        """Items that are due, to try to place in the next free talk or drill. Never offered during baseline tests."""
        items = [i for i in db.list_notebook(due_before=now_iso()) if i["english"]][:max(1, min(limit, 6))]
        return {"items": items}

    @app.post("/api/sessions/{sid}/targets", status_code=201)
    def add_targets(sid: str, body: TargetsIn) -> dict[str, Any]:
        s = need_session(sid)
        if s["kind"] == "baseline":
            raise HTTPException(409, "no help is allowed in baseline tests: it would change the measurement")
        if s["status"] != "created":
            raise HTTPException(409, "targets must be set before the recording is uploaded")
        for item_id in body.item_ids:
            item = need_item(item_id)
            if not item["english"]:
                raise HTTPException(409, f"item {item_id} has no English version yet")
            db.add_target(sid, item_id, body.source)
        events.write("targets.set", sid, items=body.item_ids, source=body.source)
        return {"targets": db.targets_for(sid)}

    @app.get("/api/sessions/{sid}/uses")
    def target_uses(sid: str) -> dict[str, Any]:
        s = need_session(sid)
        out = []
        for t in db.targets_for(sid):
            used, matched = None, None
            if s["status"] == "done" and s["transcript_text"]:
                matched = find_use(t["english"], s["transcript_text"])
                used = matched is not None
                db.save_use(sid, t["item_id"], used, matched)
            out.append({**t, "used": used, "matched": matched})
        return {"uses": out}

    @app.post("/api/notebook/{item_id}/archive")
    def notebook_archive(item_id: int) -> dict[str, Any]:
        item = need_item(item_id)
        db.update_notebook(item_id, status="archived")
        events.write("notebook.archive", item["session_id"], item=item_id)
        return {"ok": True}

    def all_metrics() -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
        sessions = db.list_sessions()
        return sessions, {s["id"]: db.metrics_for(s["id"], METRICS_VERSION) for s in sessions if s["status"] == "done"}

    @app.get("/api/drills")
    def drills(include_mock: bool = False) -> list[dict[str, Any]]:
        sessions, metrics = all_metrics()
        return build_drills(sessions, metrics, include_mock=include_mock)

    @app.get("/api/drills/{group_id}")
    def drill(group_id: str, include_mock: bool = True) -> dict[str, Any]:
        sessions, metrics = all_metrics()
        for g in build_drills(sessions, metrics, include_mock=include_mock):
            if g["group_id"] == group_id:
                return g
        raise HTTPException(404, "drill not found")

    @app.get("/api/export")
    def export(detail: bool = False) -> Response:
        payload = {"exported_at": utcnow(), "app_version": APP_VERSION, "metrics_version": METRICS_VERSION, **db.export_all()}
        if detail:
            # per session: every word with its timing, every pause, and the quality flags
            for s in payload["sessions"]:
                tr = load_transcript(s)
                if tr:
                    computed = compute_metrics(tr["words"])
                    s["words"] = tr["words"]
                    s["pauses"] = computed["pauses"]
                    s["flags"] = computed["flags"]
        events.write("export", None, sessions=len(payload["sessions"]), detail=detail)
        name = "fluency-export-detailed.json" if detail else "fluency-export.json"
        return Response(json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/events.jsonl")
    def events_file() -> Response:
        # The log keeps growing while it is being sent (this very request is logged), so a
        # streamed file response would declare a length and then send more. Send a snapshot.
        body = events.path.read_bytes()
        return Response(body, media_type="application/x-ndjson",
                        headers={"Content-Disposition": 'attachment; filename="events.jsonl"'})

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        events.write("server.error", None, path=request.url.path, error=str(exc), trace=traceback.format_exc(limit=8))
        return JSONResponse({"detail": "internal error"}, status_code=500)

    return app
