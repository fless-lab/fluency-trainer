# Fluency Trainer, version 0.1

A personal tool to measure and improve spoken English fluency. This first version is the measurement spine: it records you, gets a faithful transcript with word timestamps, computes fluency metrics, and keeps a complete trace of everything. The conversation partner and the timed repetition mode come next, on top of this.

## What it does

- Runs the 30 day protocol: a day 0 baseline, a day 2 baseline (to estimate your natural variability), and retests on days 15 and 30 with new topics plus one repeated topic. Each test is 30 seconds of preparation and 2 minutes of speaking, on three kinds of topic (personal, opinion, describe).
- Free monologues, measured the same way but kept out of the checkpoint comparison.
- Metrics per session: words per minute, articulation rate, phonation ratio, mean length of run, pauses of 250 ms or more (total, mid-clause, clause-boundary, over 1 second), fillers, repetitions, time to first word.
- A progress table that tells you whether a change is larger than your own noise.
- A 4/3/2 drill: the same story told in 4, then 3, then 2 minutes. Each round is recorded and transcribed like a test, and a table compares the rounds (speed, pauses, mid-clause pauses, fillers, repetitions). Drills are kept out of the checkpoint comparison, because retelling a story is partly rehearsal.
- Feedback after a session (optional, needs a language model): a few patterns worth fixing with your own quotes and a better version, phrases to reuse, and one thing to focus on next time. Every quote is checked against the transcript and flagged if the model invented it.
- A transcript view with every pause marked, plus audio playback.
- A detailed export (Data tab, or `/api/export?detail=true`) with every word and its timing, every pause and the quality flags.
- A detailed export (Data tab, or `/api/export?detail=true`) with every word and its timing, every pause and the quality flags.

## Run it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your AssemblyAI key in it
python -m app
```

Open http://127.0.0.1:8765 in Chrome or Firefox and allow the microphone. To try the app without any key, set `STT_PROVIDER=mock` in `.env`. Mock transcripts are invented and are excluded from the progress view.

## Feedback with a language model

Put these in `.env`, then restart:

```
LLM_PROVIDER=openrouter      # or anthropic
LLM_API_KEY=your key
LLM_MODEL=exact model name from your provider
```

`LLM_PROVIDER=mock` shows the screen with placeholder text, with no key and no cost. The feedback button appears in each session; it is never called automatically. Results are stored, so reopening a session costs nothing. `COACH_MAX_PER_DAY` (default 30) caps the requests per 24 hours. The transcript of the session is sent to the provider you choose; the audio is not. Token usage is written to the event log. Both adapters follow the providers' public API shape and are covered by tests with a fake server, but I have not run them against the live services.

## Notebook: the expressions you could not find

Fluency also breaks when you know a word but cannot get to it in time, or lack the natural way to say something. After a session, open it from History and click the word where you stopped or went around something. Write what you wanted to say, in any language. The app stores the moment (the words around it and the length of the pause) and, if a model is configured, proposes one to three natural ways to say it. You keep one, or write your own, and it enters the notebook.

Review is spoken. The Notebook tab shows what you wanted to say, you say it aloud in English, reveal the answer, and report honestly: said it without stopping, hesitated, or could not. The next review moves out (same day, then 1, 3, 7, 14 and 30 days), stays at the same interval, or goes back to the start. Phrases from the feedback can be kept with one click. The notebook works without any model: you can add and choose versions by hand. Every add, suggestion and review is in the event log, and the tables are in the export.

Limits to keep in mind: the model sees only the transcript, so on a short or unclear fragment it may guess your intent wrongly, which is why you write the intent yourself. The review is a self-check, not a pronunciation score. Suggestions count against the same `COACH_MAX_PER_DAY` cap as feedback. Not run against the live providers yet.

## Tracking and safety

- `data/events.jsonl`: append-only log of every action, error, upload, transcription attempt and browser-side event (microphone refusals, recorder errors, upload failures).
- `data/fluency.db`: sessions, metrics (versioned), outside ratings and settings.
- `data/audio/`: every recording, written before any provider is called, never deleted by the app.
- `data/transcripts/`: the raw provider response for each session. Metrics can be recomputed from it if the definitions change (`POST /api/sessions/{id}/recompute`).
- If the provider fails or the key is missing, the audio is kept and the session shows the error; open it in History and retry.
- If the browser cannot upload, the recording is kept on the device (IndexedDB) and retried the next time the page opens.
- If the app stops during transcription, unfinished sessions are picked up again at the next start.
- The server listens on 127.0.0.1 only. Provider keys stay in `.env` on the server, never in the browser.

## Things you should know

- The AssemblyAI adapter follows the public docs for upload, creation, polling and the `disfluencies` option. It has not been run against the live service by me: the first real recording is the real test. If it fails, the exact provider error is shown in the session and written to the event log.
- The model selection field is only sent if you set `ASSEMBLYAI_SPEECH_MODELS`. The docs list filler support for `universal-3-5-pro` and `universal-2`.
- If a transcript contains no fillers at all, the session shows a warning: the provider may have removed them, which would understate your hesitations.
- Mid-clause versus clause-boundary pauses use punctuation and a short list of conjunctions. It is an approximation, not a syntactic analysis. Syllable counts, and so articulation rate, are estimated.
- The research behind this found no study measuring gains at this dose, and most published effects come from learners below your level. The progress table is there so you can judge your own result, not to promise one.

## Look

Playful, colourful interface: cream paper with a dot grid, thick outlines, chunky buttons, a colour per tab, and a light or dark switch (top right, remembered in the browser). The Outfit font is bundled in `app/static/fonts` (SIL Open Font License, text included), so nothing is loaded from the internet. Decorative shapes and speech bubbles appear in the margins on wide screens only. Small animations (floating shapes, cards that pop in, confetti when a session or a review ends) are switched off when your device asks for reduced motion, and nothing moves during a recording. Only transform and opacity are animated so any modern browser can do it on the graphics card. At load the app also measures its own frame rate for two seconds; below 45 frames per second it switches the decorative motion off ("calm" mode) and logs a `client.perf_check` event with the measured rate and browser, so you can see in the log where it was slow. Tested in Chromium only: Firefox and Safari were not available here.

## Tests

```
python -m pytest -q
```

## Next

Recurring error patterns across sessions, a pre-speaking box for the words you expect to need, and then a conversation partner with patient turn-taking.
