# Fluency Trainer, version 0.1

A personal tool to measure and improve spoken English fluency. This first version is the measurement spine: it records you, gets a faithful transcript with word timestamps, computes fluency metrics, and keeps a complete trace of everything. The conversation partner and the timed repetition mode come next, on top of this.

## What it does

- Runs the 30 day protocol: a day 0 baseline, a day 2 baseline (to estimate your natural variability), and retests on days 15 and 30 with new topics plus one repeated topic. Each test is 30 seconds of preparation and 2 minutes of speaking, on three kinds of topic (personal, opinion, describe).
- Free monologues, measured the same way but kept out of the checkpoint comparison.
- Metrics per session: words per minute, articulation rate, phonation ratio, mean length of run, pauses of 250 ms or more (total, mid-clause, clause-boundary, over 1 second), fillers, repetitions, time to first word.
- A progress table that tells you whether a change is larger than your own noise.
- A transcript view with every pause marked, plus audio playback.

## Run it

```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your AssemblyAI key in it
python -m app
```

Open http://127.0.0.1:8765 in Chrome or Firefox and allow the microphone. To try the app without any key, set `STT_PROVIDER=mock` in `.env`. Mock transcripts are invented and are excluded from the progress view.

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

## Tests

```
python -m pytest -q
```

## Next

Conversation partner with patient turn-taking, the 4/3/2 mode, delayed feedback, and a memory of your recurring errors.
