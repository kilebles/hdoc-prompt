"""python -m promptgen.api — веб-API на HOST:PORT (по умолчанию 0.0.0.0:8040)."""

import uvicorn

from promptgen.api.app import create_app
from promptgen.api.settings import Settings


def main() -> None:
    settings = Settings()
    print(f"[api] /api/prompts на {settings.host}:{settings.port}, токен {'задан' if settings.api_token else 'НЕ задан'}",
          flush=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
