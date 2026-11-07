import shutil
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AFDS_", env_file=ROOT / ".env", extra="ignore")
    data_dir: Path = ROOT / "data"
    database_url: str = "sqlite:///./data/afds.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    lease_seconds: int = 30
    media_timeout: int = 180
    max_job_attempts: int = 2

    def model_post_init(self, context):
        self.data_dir = (ROOT / self.data_dir).resolve()
        if self.database_url.startswith("sqlite:///./"):
            path = (ROOT / self.database_url.removeprefix("sqlite:///./")).resolve()
            self.database_url = "sqlite:///" + path.as_posix()
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("v0.1 supports a local SQLite database")

    def binary(self, name: str) -> str:
        configured = getattr(self, name)
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        candidates = sorted((ROOT / ".tools" / "ffmpeg").glob(f"**/{name}.exe"))
        if candidates:
            return str(candidates[0])
        raise FileNotFoundError(f"{name} unavailable. Install FFmpeg or set AFDS_{name.upper()}.")
