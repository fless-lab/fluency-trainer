import uvicorn

from .config import load_settings
from .main import create_app


def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    print(f"Fluency Trainer on http://{settings.host}:{settings.port}  (speech provider: {settings.stt_provider})")
    print(f"Data folder: {settings.data_dir}")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
