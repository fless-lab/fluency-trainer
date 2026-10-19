"""Compare checkpoints against a baseline and against natural variability.

Natural variability is estimated from the gap between the day 0 and day 2 baselines.
With three topics per checkpoint and two baselines, this is a crude estimate: it is
there to stop you from reading noise as progress, not to prove anything.
"""

from __future__ import annotations

from typing import Any

from .protocols import CHECKPOINTS

# name -> (label, better direction)
METRIC_INFO: dict[str, tuple[str, str]] = {
    "words_per_minute": ("Words per minute", "higher"),
    "articulation_rate_syll_per_s": ("Articulation rate (syllables/s, estimated)", "higher"),
    "phonation_ratio": ("Phonation ratio", "higher"),
    "mean_length_of_run_words": ("Mean length of run (words)", "higher"),
    "pauses_per_100_words": ("Pauses of 250 ms or more per 100 words", "lower"),
    "mid_clause_pauses_per_100_words": ("Mid-clause pauses per 100 words (heuristic)", "lower"),
    "boundary_pauses_per_100_words": ("Clause-boundary pauses per 100 words (heuristic)", "info"),
    "long_pauses_per_100_words": ("Pauses over 1 s per 100 words", "lower"),
    "repetitions_per_100_words": ("Repetitions per 100 words", "lower"),
    "fillers_per_100_words": ("Fillers per 100 words", "info"),
    "onset_latency_s": ("Time to first word (s)", "info"),
}

REPEAT_METRICS = ("words_per_minute", "pauses_per_100_words", "mid_clause_pauses_per_100_words")


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 3) if values else None


def verdict(delta: float | None, noise: float | None, better: str) -> str:
    if delta is None:
        return "no data"
    if noise is None:
        return "no noise estimate yet"
    if abs(delta) <= noise:
        return "within noise"
    if better == "info":
        return "changed"
    return "improved" if (delta > 0) == (better == "higher") else "worse"


def _latest_per_topic(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for r in sorted(rows, key=lambda x: x["created_at"]):
        latest[r["topic_id"]] = r
    return list(latest.values())


def build_progress(
    sessions: list[dict[str, Any]],
    metrics: dict[str, dict[str, float]],
    include_mock: bool = False,
) -> dict[str, Any]:
    """sessions: rows from the DB. metrics: session id -> {metric name: value}."""
    checkpoints: dict[str, Any] = {}
    for cp in CHECKPOINTS:
        rows = [
            s for s in sessions
            if s["checkpoint"] == cp["id"]
            and s["status"] == "done"
            and (include_mock or s["provider"] != "mock")
            and s["id"] in metrics
        ]
        main_rows = _latest_per_topic([r for r in rows if not r["is_repeat"]])
        repeat_rows = _latest_per_topic([r for r in rows if r["is_repeat"]])
        means = {
            name: _mean([metrics[r["id"]].get(name) for r in main_rows]) for name in METRIC_INFO
        }
        checkpoints[cp["id"]] = {
            "label": cp["label"],
            "n_topics": len(main_rows),
            "topics_expected": len(cp["topics"]),
            "means": means,
            "session_ids": [r["id"] for r in main_rows],
            "repeat_session_ids": [r["id"] for r in repeat_rows],
        }

    base0, base2 = checkpoints["base0"], checkpoints["base2"]
    baseline_mean: dict[str, float | None] = {}
    noise: dict[str, float | None] = {}
    for name in METRIC_INFO:
        a, b = base0["means"][name], base2["means"][name]
        have = [v for v in (a, b) if v is not None]
        baseline_mean[name] = _mean(have)
        noise[name] = round(abs(a - b), 3) if (a is not None and b is not None) else None

    comparisons: dict[str, Any] = {}
    for cp_id in ("day15", "day30"):
        comp = {}
        for name, (_label, better) in METRIC_INFO.items():
            cur, base = checkpoints[cp_id]["means"][name], baseline_mean[name]
            delta = round(cur - base, 3) if (cur is not None and base is not None) else None
            comp[name] = {"delta": delta, "verdict": verdict(delta, noise[name], better)}
        comparisons[cp_id] = comp

    # repeated topics: same topic as day 0 vs now
    originals = {r["topic_id"]: r for r in _latest_per_topic([
        s for s in sessions
        if s["checkpoint"] == "base0" and s["status"] == "done" and not s["is_repeat"]
        and (include_mock or s["provider"] != "mock") and s["id"] in metrics
    ])}
    repeats = []
    for cp_id in ("day15", "day30"):
        for sid in checkpoints[cp_id]["repeat_session_ids"]:
            row = next(s for s in sessions if s["id"] == sid)
            orig = originals.get(row["topic_id"])
            if not orig:
                continue
            repeats.append({
                "checkpoint": cp_id,
                "topic_id": row["topic_id"],
                "metrics": {
                    m: {"day0": metrics[orig["id"]].get(m), "now": metrics[sid].get(m)}
                    for m in REPEAT_METRICS
                },
            })

    return {
        "metrics": {k: {"label": v[0], "better": v[1]} for k, v in METRIC_INFO.items()},
        "checkpoints": checkpoints,
        "baseline_mean": baseline_mean,
        "noise": noise,
        "comparisons": comparisons,
        "repeats": repeats,
        "notes": [
            "Each checkpoint averages three topics. Read changes against the noise column, not on their own.",
            "If a metric improves on new topics but not on the repeated topic, that is normal. If it only improves on the repeated topic, you have memorised, not become more fluent.",
            "Clause-boundary versus mid-clause classification and syllable counts are heuristics.",
        ],
    }
