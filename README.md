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

The session screen lists the longest silences in the middle of a sentence (0.7 s or more) with the words around them, so you do not have to hunt for where you got stuck: click one, say what you wanted to say, or ask the model to guess from the surrounding words. The feedback request also receives the transcript with those long silences marked in place (for example `died at 30 [pause 1.4s] years old`), so it can point at where you searched for words. Still only text goes to the provider, never audio.

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

Playful, colourful interface: cream paper with a dot grid, thick outlines, chunky buttons, a colour per tab, and a light or dark switch (top right, remembered in the browser). The Outfit font is bundled in `app/static/fonts` (SIL Open Font License, text included), so nothing is loaded from the internet. Decorative shapes and speech bubbles appear in the margins on wide screens only. Small animations (floating shapes, cards that pop in, confetti when a session or a review ends) are switched off when your device asks for reduced motion, and nothing moves during a recording. Every decorative shape floats gently. At load the app also measures its own frame rate for two seconds; below 45 frames per second it switches the decorative motion off ("calm" mode) and logs a `client.perf_check` event with the measured rate and browser, so you can see in the log where it was slow. Tested in Chromium only: Firefox and Safari were not available here.

## Talking with the partner

- **Who decides that you have finished.** Default: you press "I'm done" (the button is always there). Or "Auto", which you can switch to at any time: it answers when your sentence is finished, using AssemblyAI's end-of-turn signal (it listens to the words and the sound) plus a real voice detector, and it waits longer when you hesitate (after "uh", "and", "because"...). A plain silence timer cut off people who search for their words, so it is only a fallback now.
- **Two independent lanes.** What the partner says goes out through one lane (the app measures exactly what it plays); what you say comes in through another, captured as raw audio all the time with a memory of the last 2.5 s. The recording of your turn, the live transcription and the voice detector all read from that one input lane. When you start talking, even over the partner, your turn is cut starting a little before you were noticed, so the first words are not lost, and the file sent contains your microphone only. Each turn is saved as a 16 kHz wav (not webm). Checked by having a model listen to the saved file after an interruption: the whole sentence was there, from its first word, without the partner's voice.
- **Voice detector.** Silero VAD, a small neural network that runs in your browser (files in `app/static/vendor/vad`, MIT licensed, about 14 MB, nothing is downloaded at run time). It tells speech from noise, breathing and echo; a loud noise does not count as speech. If it cannot load, the app falls back to a loudness threshold and logs `client.vad_unavailable`.
- **You can interrupt, with or without headphones.** Talk over the partner, or press "Interrupt" (always there), and it stops and listens. Like an assistant on a phone, the app knows the exact sound it is playing, learns in the first moments of each reply how much of it comes back into the microphone, and treats the microphone as you only when it is clearly louder than that echo predicts and sounds like speech. If it fires without you speaking twice, it switches itself off for that conversation (`client.conv_barge_disabled` in the log) and the button remains. Tested with clear synthetic voices and a simulated echo, not yet with real speakers and a real room. It does not work with the browser fallback voice, which the app cannot measure: only the button then.
- The Auto end-of-turn logic was tested for connection and with a simulated microphone, not with a real person talking: tune it with the "patient / natural / quick" choice.

## Hearing it

- **Click to hear.** Words and phrases with a dotted underline can be clicked (a small speaker appears on hover): the words in the pronunciation lists, the lesson, the "Better:" examples and phrases in the feedback, the expressions in your notebook, and what the partner said. The app's voice makes each text once (`/api/say`, saved in `data/say`), so a word you hear again is instant and free. If the voice is not set up or the daily cap (`TTS_MAX_CHARS_PER_DAY`) is reached, the browser voice is used.
- **Model and You.** Every weak word has two buttons: the app's voice, and yourself, cut out of your own recording at that word. Compare them with your ear.
- **Short words and half words are left out** of the lists to work on. Small words are said in a weak form in fast English ("didn't" becomes "din"), and the first and last word of each piece of audio sent to Azure can be cut in two. The scores still count every word.
- **Pronunciation lesson.** A button on the pronunciation card: the language model reads the measured weak sounds and words and writes a short lesson on one or two sounds, for a speaker of `NATIVE_LANGUAGE` (default French): what goes wrong, how to place the mouth and tongue, minimal pairs, and three practice sentences built from your own words and topics. About a cent. Every word it says comes from your session is checked against the measured list. The scores are measured; the explanation is a model's and can be wrong.

## Pronunciation over time

The Progress tab ends with a pronunciation section built from every scored session (conversations count too): the three scores per session with a small curve, the weakest sounds with their own curve, and the words that are weak in several sessions, each with Model and You buttons (your last recording of it). A direction (better, worse, steady) is only given for a sound after three sessions, with at least 8 words on each side of the comparison, and only when the change is 8 points or more: below that, a score moves just because the words or the topic changed. Sessions with fewer than 30 words are ignored.

**Practise later.** Every weak word (in the pronunciation card, in the lesson, and in the list of words that keep coming back) has a "+ Notebook" button. It adds the word to your notebook as a card, reviewed on the same widening schedule as your expressions, with the word clickable to hear. A word already in the notebook is not added twice.

## Pronunciation from the audio

A speech-to-text transcript tends to guess the word you meant, so it hides mispronunciations. This part scores the audio itself.

- **Azure Speech** (pronunciation assessment, no script needed) scores every word and every sound, plus flow and intonation. Set `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` in `.env`. The free tier (F0) is enough: about 5 hours of audio a month.
- **An audio model** (default `google/gemini-3.1-flash-lite` through OpenRouter, `AUDIO_COACH_MODEL`) then listens to a few short clips around your weakest words and says what it hears and how to fix it. It reuses your OpenRouter key. It is less reliable than the scores and can be wrong, and the screen says so.
- Scoring starts as soon as the audio is saved, in the background, at the same time as the transcription; in a conversation each of your turns is scored while the partner answers. Expect about 35 s for 2 minutes of solo audio on the free tier, which handles one request at a time (`PRONUNCIATION_CONCURRENCY`, raise it on a paid tier). The session page fills in by itself.
- The feedback model receives the measured scores and the audio model's notes, so its pronunciation advice comes from the audio. Without them it is told it cannot judge pronunciation from text.
- Scores compare you to a standard accent: a different accent is not an error.

## Tests

```
python -m pytest -q
```

## Next

Trends in the pronunciation scores across sessions.
