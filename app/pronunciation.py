"""Pronunciation scores from the audio itself, using Azure Speech's pronunciation assessment.

A speech-to-text transcript tends to guess the word the speaker meant, so it hides mispronunciations. Azure scores
how each word and each sound was actually said. The speech has no script, so the assessment is unscripted
(empty reference text): it reports what it heard and how well each phoneme was pronounced.

The short-audio REST call takes at most 30 s, so longer audio is cut into windows of about 28 s at the quietest
moment, and the windows are assessed concurrently. Only the audio is needed, so it starts as soon as the audio is saved. Everything else here is plain data handling and has
no network access, so it can be tested with a recorded answer.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from typing import Any

import httpx

MAX_CHUNK_S = 28.0
MIN_CHUNK_S = 0.8
WORD_WEAK_BELOW = 80.0  # a word scored under this is worth showing
PHONEME_WEAK_BELOW = 70.0
RATE = 16000
PRON_VERSION = "1"


class PronunciationError(RuntimeError):
    pass


def split_windows(pcm: bytes, max_s: float = MAX_CHUNK_S) -> list[tuple[float, float]]:
    """Cut the audio into windows of at most max_s, each cut at the quietest 50 ms in the last 40 % of the window,
    so a word is rarely split. Needs only the audio, so scoring can start before the transcript exists."""
    from array import array
    total = len(pcm) / (2 * RATE)
    if total <= max_s:
        return [(0.0, total)]
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    frame = RATE // 20
    energy = [sum(x * x for x in samples[i:i + frame]) for i in range(0, len(samples) - frame, frame)]
    out, cur = [], 0.0
    while total - cur > max_s:
        lo, hi = int((cur + max_s * 0.6) * 20), int((cur + max_s) * 20)
        hi = min(hi, len(energy))
        best = min(range(lo, hi), key=lambda i: energy[i]) if lo < hi else hi
        cut = (best + 0.5) / 20
        out.append((cur, cut))
        cur = cut
    out.append((cur, total))
    return out


def decode_pcm16k(path: str) -> bytes:
    """Whole file as 16 kHz mono 16-bit PCM. PyAV bundles ffmpeg, so webm, ogg, m4a, mp3 and wav all work."""
    try:
        import av
    except ImportError as exc:
        raise PronunciationError("The 'av' package is missing: run pip install -r requirements.txt") from exc
    pcm = bytearray()
    try:
        with av.open(path) as inp:
            rs = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            for frame in inp.decode(audio=0):
                for f in rs.resample(frame):
                    pcm += bytes(f.planes[0])[: f.samples * 2]
            for f in rs.resample(None):
                pcm += bytes(f.planes[0])[: f.samples * 2]
    except Exception as exc:  # noqa: BLE001 - any decoding problem is reported the same way
        raise PronunciationError(f"Could not decode the audio: {exc}") from exc
    return bytes(pcm)


def to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


async def assess_wav(client: httpx.AsyncClient, wav: bytes, key: str, region: str, locale: str) -> dict[str, Any]:
    cfg = {"ReferenceText": "", "GradingSystem": "HundredMark", "Granularity": "Phoneme", "Dimension": "Comprehensive",
           "EnableMiscue": False, "EnableProsodyAssessment": True}
    headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={RATE}",
               "Pronunciation-Assessment": base64.b64encode(json.dumps(cfg).encode()).decode(), "Accept": "application/json"}
    url = (f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
           f"?language={locale}&format=detailed")
    last = ""
    for attempt in range(8):
        try:
            r = await client.post(url, content=wav, headers=headers)
        except httpx.HTTPError as exc:
            last = f"network error: {exc}"
        else:
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (401, 403):
                raise PronunciationError(f"Azure refused the key or region ({last})")
            if r.status_code not in (429, 500, 502, 503, 504):
                raise PronunciationError(f"Azure: {last}")
        await asyncio.sleep(min(1.0 + attempt, 4.0))  # the free tier allows one request at a time and answers 429 otherwise
    raise PronunciationError(f"Azure: {last}")


# Small words are said in a weak form in fast English ("didn't" becomes "din", "for" becomes "fer"): a low score on
# them is normal speech, not a mistake to practise, so they are left out of the lists of words and sounds to work on.
FUNCTION_WORDS = set("""a an the and or but so if as at by for from in into of off on onto out over to up with without about
after again all also am any are aren't be been being both can can't could couldn't did didn't do does doesn't don't each
else even ever few for get got had has hasn't have haven't he her here hers him his how i i'd i'll i'm i've is isn't it
it's its just like me more most my no nor not now one only other our ours she should shouldn't some such than that that's
the their them then there there's these they they're this those too us very was wasn't we we're were weren't what when
where which who whom whose why will won't would wouldn't yeah yes you you're your yours um uh oh""".split())


def summarize_chunk(d: dict[str, Any], offset_s: float, cut_start: bool = False, cut_end: bool = False) -> dict[str, Any]:
    """Keep what is useful from one Azure answer: the scores and, for every word, its sounds.
    The first word of a window that was cut out of longer audio, and the last one, may be half a word: they are marked."""
    if d.get("RecognitionStatus") != "Success":
        return {"words": [], "scores": {}}
    nb = (d.get("NBest") or [{}])[0]
    words = []
    raw = nb.get("Words") or []
    for i, w in enumerate(raw):
        phonemes = [{"p": p.get("Phoneme"), "s": p.get("AccuracyScore")} for p in w.get("Phonemes") or []]
        words.append({"w": w.get("Word"), "acc": w.get("AccuracyScore"), "err": w.get("ErrorType") or "None",
                      "t": round(offset_s + (w.get("Offset") or 0) / 1e7, 2), "d": round((w.get("Duration") or 0) / 1e7, 2),
                      "ph": phonemes, "edge": (i == 0 and cut_start) or (i == len(raw) - 1 and cut_end)})
    scores = {k: nb.get(k) for k in ("AccuracyScore", "FluencyScore", "ProsodyScore", "PronScore")}
    return {"words": words, "scores": scores}


async def assess_audio(path: str, key: str, region: str, locale: str,
                       gate: asyncio.Semaphore | None = None) -> dict[str, Any]:
    """Assess one audio file. Returns {"seconds", "chunks": [summarize_chunk, ...]}."""
    pcm = await asyncio.to_thread(decode_pcm16k, path)
    total = len(pcm) / (2 * RATE)
    windows = [w for w in await asyncio.to_thread(split_windows, pcm) if w[1] - w[0] >= MIN_CHUNK_S]
    if not windows:
        return {"seconds": round(total, 1), "chunks": []}
    gate = gate or asyncio.Semaphore(2)

    async def one(client: httpx.AsyncClient, a: float, b: float) -> dict[str, Any]:
        wav = to_wav(pcm[int(a * RATE) * 2: int(b * RATE) * 2])
        async with gate:
            d = await assess_wav(client, wav, key, region, locale)
        out = summarize_chunk(d, a, cut_start=a > 0.01, cut_end=b < total - 0.01)
        out["seconds"] = round(b - a, 2)
        return out

    async with httpx.AsyncClient(timeout=60.0) as client:
        chunks = await asyncio.gather(*(one(client, a, b) for a, b in windows))
    return {"seconds": round(total, 1), "chunks": list(chunks)}


def _wavg(pairs: list[tuple[float, float]]) -> float | None:
    tot = sum(w for _, w in pairs)
    return round(sum(v * w for v, w in pairs) / tot, 1) if tot else None


def aggregate(units: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine every assessed audio file of a session into one summary: scores, weakest words, weakest sounds."""
    scores: dict[str, list[tuple[float, float]]] = {}
    all_words: list[dict[str, Any]] = []
    seconds = 0.0
    for u in units:
        seconds += u.get("seconds", 0.0)
        chunks = u.get("chunks", [])
        for ci, c in enumerate(chunks):
            for wi, w in enumerate(c["words"]):  # scored before the cut marker existed: where a cut was made, the edge words are suspect
                if "edge" not in w:
                    w["edge"] = (wi == 0 and ci > 0) or (wi == len(c["words"]) - 1 and ci < len(chunks) - 1)
            n = len(c["words"])
            for k, v in (c.get("scores") or {}).items():
                if isinstance(v, (int, float)) and n:
                    scores.setdefault(k, []).append((float(v), float(n)))
            for w in c["words"]:
                all_words.append({**w, "unit": u.get("unit")})
    for w in all_words:
        w["skip"] = bool(w.get("edge")) or (w["w"] or "").lower() in FUNCTION_WORDS or len(w["w"] or "") < 3
    all_words_all, all_words = all_words, [w for w in all_words if not w["skip"]]  # scores keep every word, the lists do not
    weak_words = [w for w in all_words if isinstance(w["acc"], (int, float)) and (w["acc"] < WORD_WEAK_BELOW or w["err"] != "None")]
    weak_words.sort(key=lambda w: w["acc"])
    seen: set[str] = set()
    worst = []
    for w in weak_words:  # one entry per word form, the worst occurrence, with how often it was weak
        key = (w["w"] or "").lower()
        if key in seen:
            continue
        seen.add(key)
        times = sum(1 for x in weak_words if (x["w"] or "").lower() == key)
        bad = [p for p in w["ph"] if isinstance(p["s"], (int, float)) and p["s"] < PHONEME_WEAK_BELOW]
        worst.append({"word": w["w"], "score": w["acc"], "times": times, "error": w["err"], "weak_sounds": bad,
                      "sounds": w["ph"], "t": w["t"], "d": w.get("d") or 1.0, "unit": w["unit"]})
        if len(worst) >= 12:
            break
    per_ph: dict[str, list[float]] = {}
    examples: dict[str, list[str]] = {}
    for w in all_words:
        for p in w["ph"]:
            if p["p"] and isinstance(p["s"], (int, float)):
                per_ph.setdefault(p["p"], []).append(p["s"])
                if p["s"] < PHONEME_WEAK_BELOW and w["w"] not in examples.setdefault(p["p"], []) and len(examples[p["p"]]) < 3:
                    examples[p["p"]].append(w["w"])
    weak_sounds = []
    for p, vals in per_ph.items():
        mean = sum(vals) / len(vals)
        if len(vals) >= 2 and mean < 80:
            weak_sounds.append({"sound": p, "mean": round(mean, 1), "count": len(vals),
                                "weak_count": sum(1 for v in vals if v < PHONEME_WEAK_BELOW), "words": examples.get(p, [])})
    weak_sounds.sort(key=lambda s: s["mean"])
    sounds = {p: {"mean": round(sum(v) / len(v), 1), "n": len(v)} for p, v in per_ph.items()}  # every sound, for the trends
    return {"version": PRON_VERSION, "seconds": round(seconds, 1), "n_words": len(all_words_all),
            "scores": {k: _wavg(v) for k, v in scores.items()}, "worst_words": worst, "weak_sounds": weak_sounds[:6], "sounds": sounds}


def for_coach(summary: dict[str, Any] | None) -> str:
    """The measurements as a few plain lines for the feedback prompt."""
    if not summary or not summary.get("n_words"):
        return ""
    s = summary["scores"]
    lines = [f"Pronunciation measured from the audio by a phoneme scorer (0-100, higher is better): "
             f"accuracy {s.get('AccuracyScore')}, fluency {s.get('FluencyScore')}, prosody {s.get('ProsodyScore')}."]
    for w in summary["worst_words"][:8]:
        bad = ", ".join(f"/{p['p']}/ {p['s']:.0f}" for p in w["weak_sounds"]) or "no single weak sound"
        lines.append(f"- \"{w['word']}\" scored {w['score']:.0f}" + (f" (weak {w['times']} times)" if w["times"] > 1 else "") + f": {bad}")
    if summary["weak_sounds"]:
        lines.append("Weakest sounds overall: " + "; ".join(
            f"/{s['sound']}/ avg {s['mean']:.0f} in {s['count']} words" + (f" (e.g. {', '.join(s['words'])})" if s["words"] else "")
            for s in summary["weak_sounds"][:4]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------- the ear: a model that listens

EAR_SYSTEM = """You listen to short clips of an adult learner speaking English and say what the learner's pronunciation really sounds like. Each clip has a target word and the result of a phoneme scorer for it (0-100 per sound).
For every clip: "heard" is what the target word actually sounds like in the audio (write it as an English respelling, or say you cannot tell), "issue" is what is off (a sound, the word stress, the rhythm), "fix" is one concrete tip for the mouth, tongue or stress, with a minimal pair or a stressed-syllable marking when useful. If the word sounds fine, say so in "issue" and leave "fix" empty. Be honest and specific, no flattery, no comment on grammar or vocabulary.
Then "overall": one or two sentences on word stress, rhythm and intonation across the clips.
Answer with one JSON object and nothing else: {"clips": [{"word": "...", "heard": "...", "issue": "...", "fix": "..."}], "overall": "..."}"""
CLIP_BEFORE_S, CLIP_AFTER_S = 1.5, 3.5
MAX_CLIPS = 4


def pick_clips(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """The weakest words, each with a few seconds of audio around it; clips that overlap in the same file are merged."""
    clips: list[dict[str, Any]] = []
    for w in summary.get("worst_words", []):
        a, b = max(0.0, w["t"] - CLIP_BEFORE_S), w["t"] + CLIP_AFTER_S
        for c in clips:
            if c["unit"] == w["unit"] and a < c["b"] and b > c["a"]:  # overlaps an earlier clip: share it
                c["a"], c["b"] = min(a, c["a"]), max(b, c["b"])
                c["words"].append(w)
                break
        else:
            if len(clips) < MAX_CLIPS:
                clips.append({"unit": w["unit"], "a": a, "b": b, "words": [w]})
    return clips


def _describe(words: list[dict[str, Any]]) -> str:
    out = []
    for w in words:
        bad = ", ".join(f"/{p['p']}/ {p['s']:.0f}" for p in w["weak_sounds"]) or "no single weak sound"
        out.append(f"\"{w['word']}\" scored {w['score']:.0f} ({bad})")
    return "; ".join(out)


async def listen(clips: list[dict[str, Any]], pcm_by_unit: dict[str, bytes], key: str, model: str,
                 base: str = "https://openrouter.ai/api/v1") -> dict[str, Any]:
    """Send the clips to an audio-capable model and return its notes. Raises PronunciationError on any failure."""
    content: list[dict[str, Any]] = []
    for i, c in enumerate(clips, 1):
        pcm = pcm_by_unit[c["unit"]]
        wav = to_wav(pcm[int(c["a"] * RATE) * 2: int(c["b"] * RATE) * 2])
        content.append({"type": "text", "text": f"Clip {i}. Target: {_describe(c['words'])}."})
        content.append({"type": "input_audio", "input_audio": {"data": base64.b64encode(wav).decode(), "format": "wav"}})
    body = {"model": model, "max_tokens": 900, "temperature": 0.2,
            "messages": [{"role": "system", "content": EAR_SYSTEM}, {"role": "user", "content": content}]}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise PronunciationError(f"audio model: network error: {exc}") from exc
    if r.status_code >= 400:
        raise PronunciationError(f"audio model: HTTP {r.status_code}: {r.text[:300]}")
    try:
        raw = r.json()["choices"][0]["message"]["content"]
        data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise PronunciationError(f"audio model: unusable answer: {r.text[:300]}") from exc
    out = []
    for c in (data.get("clips") or [])[: len(clips) * 3]:
        if isinstance(c, dict) and isinstance(c.get("word"), str):
            out.append({k: str(c.get(k) or "")[:300] for k in ("word", "heard", "issue", "fix")})
    return {"model": model, "clips": out, "overall": str(data.get("overall") or "")[:500]}


def ear_for_coach(ear: dict[str, Any] | None) -> str:
    if not ear or not ear.get("clips"):
        return ""
    lines = ["What an audio model heard when it listened to the weakest words:"]
    for c in ear["clips"]:
        lines.append(f"- \"{c['word']}\": sounds like {c['heard']}. {c['issue']}" + (f" Tip: {c['fix']}" if c["fix"] else ""))
    if ear.get("overall"):
        lines.append("Overall rhythm, stress and intonation: " + ear["overall"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------- progress over several sessions

TREND_MIN_WORDS = 30  # a session shorter than this says too little about the sounds
SOUND_MIN_PER_SESSION = 3
SOUND_MIN_TOTAL = 12
TREND_STEP = 8.0  # points of average score: smaller changes are called steady
TREND_MIN_WORDS_PER_SIDE = 8  # the early part and the recent part of the comparison each need this many words with the sound


def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def trends(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Pronunciation across sessions, oldest first. Each item: {"id", "date", "kind", "summary": aggregate(...)}.
    Reports the three scores per session, the weakest sounds with whether they get better or worse, and the words that
    are weak again and again."""
    from .lesson import IPA
    used = [s for s in sessions if (s["summary"] or {}).get("n_words", 0) >= TREND_MIN_WORDS]
    score_series: dict[str, list[dict[str, Any]]] = {"AccuracyScore": [], "FluencyScore": [], "ProsodyScore": []}
    per_sound: dict[str, list[dict[str, Any]]] = {}
    words: dict[str, dict[str, Any]] = {}
    for s in used:
        sm = s["summary"]
        for k, series in score_series.items():
            v = (sm.get("scores") or {}).get(k)
            if isinstance(v, (int, float)):
                series.append({"id": s["id"], "date": s["date"], "v": v})
        for p, st in (sm.get("sounds") or {}).items():
            if st["n"] >= SOUND_MIN_PER_SESSION:
                per_sound.setdefault(p, []).append({"id": s["id"], "date": s["date"], "mean": st["mean"], "n": st["n"]})
        for w in sm.get("worst_words", []):
            e = words.setdefault(w["word"].lower(), {"word": w["word"], "sessions": 0, "scores": [], "last": None})
            e["sessions"] += 1
            e["scores"].append(w["score"])
            e["last"] = {"id": s["id"], "unit": w["unit"], "t": w["t"], "d": w.get("d") or 1.0}
    sounds = []
    for p, pts in per_sound.items():
        if len(pts) < 2 or sum(x["n"] for x in pts) < SOUND_MIN_TOTAL:
            continue
        means = [x["mean"] for x in pts]
        k = max(1, len(means) // 3)
        early, recent = pts[:k], pts[-k:]
        wavg = lambda g: sum(x["mean"] * x["n"] for x in g) / sum(x["n"] for x in g)  # noqa: E731 - weighted by words
        enough = len(pts) >= 3 and sum(x["n"] for x in early) >= TREND_MIN_WORDS_PER_SIDE and sum(x["n"] for x in recent) >= TREND_MIN_WORDS_PER_SIDE
        delta = round(wavg(recent) - wavg(early), 1) if enough else None
        trend = None if delta is None else "improving" if delta >= TREND_STEP else "declining" if delta <= -TREND_STEP else "steady"
        sounds.append({"sound": p, "ipa": IPA.get(p, p), "mean": round(_avg(means), 1), "latest": means[-1], "delta": delta,
                       "trend": trend, "sessions": len(pts), "points": pts})
    sounds.sort(key=lambda x: x["mean"])
    recurring = [{"word": e["word"], "sessions": e["sessions"], "mean": round(_avg(e["scores"]), 1), "last": e["last"]}
                 for e in words.values() if e["sessions"] >= 2]
    recurring.sort(key=lambda e: (-e["sessions"], e["mean"]))
    return {"n_sessions": len(used), "scores": score_series, "sounds": sounds[:8], "recurring_words": recurring[:10]}
