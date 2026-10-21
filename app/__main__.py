import uvicorn

from .config import load_settings
from .main import create_app

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    settings = load_settings()
    if settings.host not in LOOPBACK and not settings.app_password_hash:
        raise SystemExit(
            f"Refusing to listen on {settings.host}: other devices could then open the app and read your recordings and keys.\n"
            "Set a password first: start the app once with HOST=127.0.0.1, open Settings, and use \"Access\".\n"
            "Then set HOST in .env again (see docs/remote-access.md)."
        )
    tls = bool(settings.tls_cert and settings.tls_key)
    app = create_app(settings)
    print(f"Fluency Trainer on {'https' if tls else 'http'}://{settings.host}:{settings.port}  (speech provider: {settings.stt_provider})")
    print(f"Data folder: {settings.data_dir}")
    if settings.app_password_hash:
        print("Access: protected by a password.")
    elif settings.host in LOOPBACK:
        print("Access: this computer only, no password.")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info",
                ssl_certfile=settings.tls_cert or None, ssl_keyfile=settings.tls_key or None)


if __name__ == "__main__":
    main()
