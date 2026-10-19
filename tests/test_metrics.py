from app.metrics import compute_metrics, count_syllables, is_filler, METRICS_VERSION


def w(text, start, end, conf=0.9):
    return {"text": text, "start": start, "end": end, "confidence": conf}


def build(spec):
    """spec: list of (text, duration_ms, gap_before_ms). Returns words with timestamps."""
    t, out = 1000, []
    for text, dur, gap in spec:
        t += gap
        out.append(w(text, t, t + dur))
        t += dur
    return out


def test_pause_counts_and_types():
    words = build([
        ("I", 200, 0), ("went", 300, 50), ("to", 150, 50),
        ("the", 150, 400),            # mid-clause pause 400 ms
        ("shop,", 300, 50),
        ("and", 200, 600),            # boundary pause (after comma, before conjunction) 600 ms
        ("bought", 300, 50), ("bread", 300, 1500),   # long mid-clause pause 1500 ms
    ])
    r = compute_metrics(words)
    v = r["values"]
    assert r["version"] == METRICS_VERSION
    assert v["pauses"] == 3
    assert v["mid_clause_pauses"] == 2
    assert v["long_pauses"] == 1
    kinds = [p["type"] for p in r["pauses"]]
    assert kinds == ["mid_clause", "boundary", "mid_clause"]
    assert v["pauses_per_100_words"] == round(3 * 100 / 8, 2)
    assert v["max_pause_ms"] == 1500.0


def test_gap_just_below_threshold_is_not_a_pause():
    words = build([("one", 200, 0), ("two", 200, 249), ("three", 200, 250)])
    r = compute_metrics(words)
    assert r["values"]["pauses"] == 1  # only the 250 ms gap counts


def test_fillers_are_separated_from_content():
    words = build([("so", 200, 0), ("um", 300, 100), ("we", 150, 100), ("uh", 250, 100), ("left", 300, 100)])
    r = compute_metrics(words)
    v = r["values"]
    assert v["content_words"] == 3
    assert v["fillers"] == 2
    assert v["fillers_per_100_words"] == round(2 * 100 / 3, 2)
    assert is_filler("Um,") and not is_filler("umbrella")


def test_words_per_minute_uses_span_not_audio_length():
    # 60 content words spread over exactly 30 seconds of speech => 120 wpm
    words, t = [], 2000
    for i in range(60):
        words.append(w(f"word{i}", t, t + 300))
        t += 500
    r = compute_metrics(words)
    span = (words[-1]["end"] - words[0]["start"]) / 1000
    assert r["values"]["words_per_minute"] == round(60 / (span / 60), 1)
    assert r["values"]["onset_latency_s"] == 2.0


def test_mean_length_of_run():
    words = build([
        ("a", 100, 0), ("b", 100, 50), ("c", 100, 50),       # run of 3
        ("d", 100, 500), ("e", 100, 50),                      # run of 2
    ])
    r = compute_metrics(words)
    assert r["values"]["mean_length_of_run_words"] == 2.5


def test_repetitions():
    words = build([("the", 100, 0), ("the", 100, 50), ("big", 100, 50), ("dog", 100, 50)])
    assert compute_metrics(words)["values"]["repetitions_per_100_words"] == 25.0


def test_phonation_ratio_between_zero_and_one():
    words = build([("a", 300, 0), ("b", 300, 800), ("c", 300, 800)])
    v = compute_metrics(words)["values"]
    assert 0 < v["phonation_ratio"] < 1


def test_too_short_and_bad_input():
    assert compute_metrics([])["flags"] == ["too_short"]
    assert compute_metrics([w("hi", 0, 10)])["flags"] == ["too_short"]
    junk = [{"text": "x"}, {"start": 1, "end": 2}, w("ok", 5, 3)]  # all unusable
    assert compute_metrics(junk)["flags"] == ["too_short"]
    only_fillers = build([("um", 100, 0), ("uh", 100, 100)])
    assert compute_metrics(only_fillers)["flags"] == ["too_short"]


def test_unsorted_input_is_sorted():
    words = list(reversed(build([("a", 100, 0), ("b", 100, 400), ("c", 100, 50)])))
    assert compute_metrics(words)["values"]["pauses"] == 1


def test_syllable_heuristic_sanity():
    assert count_syllables("cat") == 1
    assert count_syllables("table") == 2
    assert count_syllables("make") == 1
    assert count_syllables("2025") == 2
    assert count_syllables("beautiful") >= 3
