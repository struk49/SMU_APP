import os


BASE_DIR = os.path.abspath(os.path.dirname(__file__))


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
    LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID", "").strip()
    LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "").strip()
    LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI", "").strip()
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
