"""
Convenience script to start the development server.

Usage:
    python run.py
"""
import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.environment == "development",
        reload_includes=["*.py", ".env"],
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
