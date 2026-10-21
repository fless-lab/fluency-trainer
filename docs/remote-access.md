# Using the app from another device (an iPad, for example)

By default the app only listens on this computer (`127.0.0.1`): nobody else can reach it. To use it from an iPad you need two
things, and the app insists on both.

1. **A password or invited people.** Without either, the app refuses to listen on the network. (To let other people in, see `docs/people.md`.)
2. **https.** A browser only gives the microphone to a secure page. Over plain `http://192.168.x.x` the microphone is refused.

Your keys, recordings and transcripts stay on the computer that runs the app. The iPad is only a screen and a microphone.

## 1. Set the password (once, on the computer)

Open **Settings, Access**, choose a passphrase of at least 12 characters (a few words are easier to remember than symbols) and
save. It is stored only as a salted hash in `.env` (`APP_PASSWORD_HASH`). Setting or changing it logs every other browser out.
Five wrong tries from one address block it for five minutes.

## 2. Reach the app over https

### Option A, recommended: Tailscale (private, no certificate to manage)

Tailscale puts your computer and your iPad on a private network and can give the app a real https address that only your own
devices can open. Nothing is open to the internet. (I could not test this part from where I work: follow Tailscale's own page,
which is the reference: https://tailscale.com/kb/1312/serve)

1. Install Tailscale on the computer and on the iPad, sign in with the same account.
2. In the Tailscale admin page, under DNS, turn on **HTTPS certificates**.
3. On the computer, with the app running, share it: `tailscale serve --bg 8765` (use your own port if you changed `PORT`).
   The command prints an address like `https://my-computer.tailnet-name.ts.net`.
4. Keep `HOST=127.0.0.1` in `.env`: Tailscale reaches the app from the same computer, so the app does not need to listen on the
   network at all.
5. On the iPad, open that address in Safari, log in, then **Share, Add to Home Screen**: it opens full screen with its own icon.

The app recognises that the request came through a proxy, so the Settings screen (keys, restart, backups) stays closed to the
iPad even though the connection looks local.

### Option B: a small rented server with Caddy

Put the app on a server (Linux, a few dollars a month), keep `HOST=127.0.0.1`, and let Caddy add https and the domain:

```
fluency.example.com {
    reverse_proxy 127.0.0.1:8765
}
```

Everything is then on that server: your keys, your recordings, the backups. Set the password before opening the port, use a long one,
and look at `data/events.jsonl` (`auth.failed`, `auth.blocked`) from time to time.

### Option C: the app serves https itself

If you already have a certificate (for example from `tailscale cert`), put it in `.env`:

```
TLS_CERT=/path/to/cert.pem
TLS_KEY=/path/to/key.pem
HOST=0.0.0.0
```

Then the app listens on the network, with https, and refuses to start if no password is set.

## On the iPad

- **Installing**: Safari, Share, Add to Home Screen. The screen stays awake during a recording or a conversation.
- **Microphone**: Safari asks once per site. If you refused by mistake: Settings, Safari, Microphone.
- **Sound**: the first sound needs a tap, which the Start button gives.
- **Not tested**: this was built and checked in a desktop browser. The voice detector, the echo handling and the recording format on
  Safari for iPad are untested. Tell me what you see and I will fix it; this is also what a native app would improve.

## What is protected

With a password, every request to the API needs the login cookie (signed, HttpOnly, SameSite=Lax, `Secure` on https, valid 30 days).
The page and its scripts are served without the cookie (they contain no data); the login form is shown instead of the app. A request
from another website's page is refused with or without a password. Only the computer itself, and not through a proxy, can open
Settings, make backups or restart the app.
