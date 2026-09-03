import os


BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_positive_int(name, default):
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def normalise_database_url(database_url):
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql://", 1)

    return database_url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    DATABASE_URL = normalise_database_url(os.getenv("DATABASE_URL", "").strip())
    SQLALCHEMY_DATABASE_URI = (
        DATABASE_URL or f"sqlite:///{os.path.join(BASE_DIR, 'posts.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SMU_SCHEDULER_ENABLED = env_flag("SMU_SCHEDULER_ENABLED", True)
    SMU_SCHEDULER_LEASE_SECONDS = env_positive_int(
        "SMU_SCHEDULER_LEASE_SECONDS",
        90,
    )
    SMU_SCHEDULER_RENEW_SECONDS = env_positive_int(
        "SMU_SCHEDULER_RENEW_SECONDS",
        15,
    )
    LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
    LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
    LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "").strip()
    ZERNIO_API_KEY = os.getenv("ZERNIO_API_KEY", "").strip()
    ZERNIO_BASE_URL = os.getenv(
        "ZERNIO_BASE_URL",
        "https://zernio.com/api/v1",
    ).strip()
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
    STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    STRIPE_PRICE_STARTER = os.getenv("STRIPE_PRICE_STARTER", "").strip()
    STRIPE_PRICE_PRO = os.getenv("STRIPE_PRICE_PRO", "").strip()
    STRIPE_PRICE_BUSINESS = os.getenv("STRIPE_PRICE_BUSINESS", "").strip()
    STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "").strip()
    REGISTRATION_MODE = os.getenv("REGISTRATION_MODE", "subscription").strip().lower()
    SMU_MONTHLY_PRICE_DISPLAY = os.getenv(
        "SMU_MONTHLY_PRICE_DISPLAY",
        "Monthly subscription",
    ).strip()
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": 5,
        "max_overflow": 2,
    }
    SMU_ADMIN_EMAILS = {
        email.strip().lower()
        for email in os.getenv("SMU_ADMIN_EMAILS", "").split(",")
        if email.strip()
    }
