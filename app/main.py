from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import APP_VERSION
from .notebook import KINDS, RESULTS, build_suggest_messages, clean, context_around, now_iso, parse_suggestions, schedule_after
from .coach import COACH_VERSION, MIN_WORDS, build_messages, parse_feedback
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
    kind: Literal["baseline", "free", "drill"]
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
    source: Literal["manual", "coach"] = "manual"


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
    coach_locks: dict[str, asyncio.Lock] = {}
    semaphore = asyncio.Semaphore(2)

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

    @app.get("/api/sessions/{sid}/coach")
    def get_coaching(sid: str) -> dict[str, Any]:
        need_session(sid)
        return {"coaching": db.latest_coaching(sid, COACH_VERSION)}

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
            if db.count_coaching_since(since) + db.count_llm_calls_since(since) >= settings.coach_max_per_day:
                events.write("coach.cap_reached", sid, cap=settings.coach_max_per_day)
                raise HTTPException(429, f"Daily cap of {settings.coach_max_per_day} feedback requests reached (COACH_MAX_PER_DAY).")
            if len(s["transcript_text"].split()) < MIN_WORDS:
                raise HTTPException(422, f"too little speech to give useful feedback (under {MIN_WORDS} words)")
            system, user = build_messages(s, db.metrics_for(sid, METRICS_VERSION), s["transcript_text"])
            events.write("coach.start", sid, provider=llm.name, model=llm.model, version=COACH_VERSION)
            started = time.monotonic()
            try:
                result = await llm.complete(system, user, 1800)
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
