"""Application configuration via environment variables."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    # Default to SQLite for local dev/testing; override with postgresql+psycopg2://...
    database_url: str = "sqlite:///./rollcaller.db"

    # --- Storage ---
    # "filesystem" for dev, "minio" for prod (swappable in this one file)
    storage_backend: str = "filesystem"
    storage_fs_root: str = "./blobstore"
    # MinIO / S3 settings (used only when storage_backend == "minio")
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "rollcaller"
    s3_secure: bool = False

    # --- Auth ---
    # Single seeded organizer
    organizer_username: str = "organizer"
    organizer_password: str = "changeme"
    session_secret: str = "dev-secret-change-in-production"
    session_max_age_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # --- Media ---
    # Base URL for media keys in dev (used by storage.url())
    media_base_url: str = "/media"

    # --- Gemma g2p fallback (optional; prep-clips only, never live) ---
    # OpenAI-compatible base URL + model id + key. Fireworks today; the partner
    # repoints GEMMA_BASE_URL / GEMMA_MODEL at their self-hosted AMD/vLLM
    # endpoint later. If any of the three is unset, gemma_ipa() returns None
    # immediately and the eSpeak floor is used (lets the app run before the
    # AMD box exists).
    gemma_base_url: str | None = None
    gemma_model: str | None = None
    gemma_api_key: str | None = None


settings = Settings()

# Ensure the blobstore root exists for filesystem backend
if settings.storage_backend == "filesystem":
    Path(settings.storage_fs_root).mkdir(parents=True, exist_ok=True)