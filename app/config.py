"""Runtime configuration, read from environment variables."""
import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
        self.upload_dir: Path = Path(os.getenv("UPLOAD_DIR", "./data/uploads"))
        self.max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
        # Zip-bomb protection: cap what an archive may expand to.
        self.max_uncompressed_bytes: int = int(os.getenv("MAX_UNCOMPRESSED_MB", "300")) * 1024 * 1024
        self.max_zip_entries: int = int(os.getenv("MAX_ZIP_ENTRIES", "200"))


settings = Settings()
