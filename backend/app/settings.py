"""Centralized application settings using Pydantic BaseSettings.

This provides a single import point for configuration instead of scattering
`os.getenv` calls across the codebase. Existing legacy direct env access
remains for backward compatibility; new code should prefer Settings.
"""
from functools import lru_cache
from pydantic import Field
from typing import Optional
try:
    # Pydantic v2 requires separate package
    from pydantic_settings import BaseSettings
except ImportError:  # pragma: no cover
    # Fallback for environments still on pydantic v1 (deprecated path)
    from pydantic import BaseSettings  # type: ignore


class Settings(BaseSettings):
    # Core
    app_name: str = "DinnerHopping Backend"
    environment: str = Field("development", alias="ENVIRONMENT")
    debug: bool = False

    # Database
    mongo_uri: str = Field("mongodb://mongo:27017/dinnerhopping", alias="MONGO_URI")
    mongo_db: str = Field("dinnerhopping", alias="MONGO_DB")

    # Auth / Security
    jwt_secret: str = Field("change-me", alias="JWT_SECRET")
    token_pepper: str = Field("", alias="TOKEN_PEPPER")
    access_token_bytes: int = Field(32, alias="ACCESS_TOKEN_BYTES")

    # Email / SMTP
    smtp_host: Optional[str] = Field(None, alias="SMTP_HOST")
    smtp_port: Optional[int] = Field(None, alias="SMTP_PORT")
    smtp_user: Optional[str] = Field(None, alias="SMTP_USER")
    smtp_pass: Optional[str] = Field(None, alias="SMTP_PASS")
    smtp_from: str = Field("info@acrevon.fr", alias="SMTP_FROM_ADDRESS")
    smtp_use_tls: bool = Field(True, alias="SMTP_USE_TLS")
    smtp_timeout_seconds: int = Field(10, alias="SMTP_TIMEOUT_SECONDS")
    smtp_max_retries: int = Field(2, alias="SMTP_MAX_RETRIES")

    # URLs / CORS
    backend_base_url: str = Field("https://localhost:8000", alias="BACKEND_BASE_URL")
    allowed_origins: str = Field("*", alias="ALLOWED_ORIGINS")
    cors_allow_credentials: bool = Field(True, alias="CORS_ALLOW_CREDENTIALS")

    # Payments
    stripe_api_key: Optional[str] = Field(None, alias="STRIPE_API_KEY")
    stripe_webhook_secret: Optional[str] = Field(None, alias="STRIPE_WEBHOOK_SECRET")
    paypal_client_id: Optional[str] = Field(None, alias="PAYPAL_CLIENT_ID")
    paypal_client_secret: Optional[str] = Field(None, alias="PAYPAL_CLIENT_SECRET")
    paypal_env: Optional[str] = Field("sandbox", alias="PAYPAL_ENV")
    payment_currency: str = Field("EUR", alias="PAYMENT_CURRENCY")

    # Features & Flags
    enforce_https: bool = Field(True, alias="ENFORCE_HTTPS")
    chat_enabled: bool = Field(True, alias="ENABLE_CHAT")

    # Email verification / tokens
    email_verification_expires_hours: int = Field(48, alias="EMAIL_VERIFICATION_EXPIRES_HOURS")

    class Config:
        case_sensitive = False
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()  # type: ignore[arg-type]


__all__ = ["Settings", "get_settings"]
