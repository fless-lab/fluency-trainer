# Inviting people to test the app

The app can have several people, each with a space of their own. You (the owner) invite them; you pay for what they use, since
everybody uses your keys. This is meant for a few people you know, to test the app and give you data and feedback.

## How it works

- **People tab** (you only): type a first name, create their code, send it with the address of the app. The code looks like
  `K7QM-4TXD-92PA-X8WC`. It is shown **once**; the app keeps only a scrambled version, so a lost code means making a new one.
  It is long enough that nobody can guess it (about 78 bits), wrong tries are limited (5, then a 5 minute wait), and it can be typed
  in lower case with or without the dashes.
- **Their space**: each person has their own folder (`data/users/<id>/`) with their own database, recordings, transcripts,
  notebook, progress and event log. Your own data stays exactly where it was. Nothing of one person can be reached from
  another's session: the folder and the database are chosen from the login cookie at every request, and an id from someone else
  is simply "not found". This is covered by tests (two people working at the same moment, background jobs, restarts).
- **What they see**: Today, Notebook, History, Progress, Data, **Me**. Not Settings, not People, no keys, no backups.
- **What you see**: for each person, the last login, how many sessions, and the quantities you are charged for over the last 7
  days (model calls, voice characters, minutes of speech transcribed and of pronunciation scored), plus the feedback they send.
  You do not see their recordings or results in the app.
- **First login**: they read a short notice (their voice is stored on your computer, sent to speech and language services, you can
  see their usage and feedback, and could in principle open their files) and accept it. Say it to them in person as well.
- **Me tab**: their name and first language (lessons use it), a box to send you feedback, a download of all their data (zip), a
  button to delete their account and everything of theirs, and log out.

## Managing

- **New code**: the old one stops working and they are logged out (use it if a code was shared by mistake).
- **Block / Unblock**: they are cut off at once; their data stays. Unblocking does not bring the old login back: they sign in again.
- **Delete**: removes the person and all their files. You must type their name.
- **You at this computer** are the owner without a password (as before). If you set an owner password in Settings, Access, you log
  in with it like anyone else, and from another device too (but Settings, People, backups and restart still only work at the
  computer itself, never through a proxy).
- **Backups** (Data tab) include every person, under `users/`. The restore command puts the same folders back.

## What is not there yet

- A spending limit per person (the daily caps in `.env` apply to each person separately, so three people can use three times the
  cap). The usage table shows what to watch.
- Making a person's data private from you: you own the computer, so you can read the files. The notice says so.
- Changing a person's code yourself without logging them out: a new code always logs them out.

To let people reach the app from their own devices, follow `docs/remote-access.md`.
