import ipaddress
import os
import shutil
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AFDS_", env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )
    data_dir: Path = ROOT / "data"
    database_url: str = Field(default="sqlite:///./data/afds.db", repr=False)
    database_url_file: Path | None = None
    production: bool = False
    public_origin: str = ""
    trusted_proxy: str = ""
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    lease_seconds: int = 30
    media_timeout: int = 180
    max_job_attempts: int = 2
    sandbox_image_id: str = ""
    mode: Literal["local", "team"] = "local"
    secure_cookies: bool = True
    session_hours: int = 8
    worker_cache_dir: Path | None = None

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        sources = (init_settings, env_settings)
        if os.environ.get("AFDS_DESKTOP_RUNTIME") != "1":
            sources += (dotenv_settings,)
        return (*sources, file_secret_settings)

    def model_post_init(self, context):
        if self.database_url_file is not None:
            try:
                if self.database_url_file.stat().st_size > 65536:
                    raise ValueError("Database secret file is too large")
                self.database_url = self.database_url_file.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise ValueError("Database secret file is unavailable") from error
        self.data_dir = (ROOT / self.data_dir).resolve()
        if self.worker_cache_dir is not None:
            self.worker_cache_dir = (ROOT / self.worker_cache_dir).resolve()
        if self.database_url.startswith("sqlite:///./"):
            path = (ROOT / self.database_url.removeprefix("sqlite:///./")).resolve()
            self.database_url = "sqlite:///" + path.as_posix()
        if not self.database_url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("Use SQLite or PostgreSQL with the Psycopg driver")
        if self.production:
            origin = urlsplit(self.public_origin)
            if (
                self.mode != "team"
                or not self.secure_cookies
                or not self.database_url.startswith("postgresql+psycopg://")
                or origin.scheme != "https"
                or not origin.hostname
                or origin.username
                or origin.password
                or origin.path not in {"", "/"}
                or origin.query
                or origin.fragment
            ):
                raise ValueError(
                    "Production requires team mode, PostgreSQL, secure cookies and an HTTPS origin"
                )
            try:
                network = ipaddress.ip_network(self.trusted_proxy, strict=False)
                if network.prefixlen == 0:
                    raise ValueError("Unrestricted proxy trust")
            except ValueError as error:
                raise ValueError("Configure a specific trusted proxy address or subnet") from error
            self.cors_origins = self.public_origin.rstrip("/")

    def binary(self, name: str) -> str:
        configured = getattr(self, name)
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        candidates = sorted((ROOT / ".tools" / "ffmpeg").glob(f"**/{name}.exe"))
        if candidates:
            return str(candidates[0])
        raise FileNotFoundError(f"{name} unavailable. Install FFmpeg or set AFDS_{name.upper()}.")

    @property
    def cache_root(self) -> Path:
        return self.worker_cache_dir or self.data_dir / "cache"
