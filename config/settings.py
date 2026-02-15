import os

from distutils.util import strtobool

SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = bool(strtobool(os.getenv("FLASK_DEBUG", "false")))
ENABLE_FAULT_INJECTION = bool(
    strtobool(os.getenv("ENABLE_FAULT_INJECTION", "false"))
)

SERVER_NAME = os.getenv(
    "SERVER_NAME", "localhost:{0}".format(os.getenv("PORT", "8000"))
)
# SQLAlchemy.
pg_user = os.getenv("POSTGRES_USER", "hello")
pg_pass = os.getenv("POSTGRES_PASSWORD", "password")
pg_host = os.getenv("POSTGRES_HOST", "postgres")
pg_port = os.getenv("POSTGRES_PORT", "5432")
pg_db = os.getenv("POSTGRES_DB", pg_user)
db = f"postgresql+psycopg://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"
SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", db)
SQLALCHEMY_TRACK_MODIFICATIONS = False

# Redis.
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Celery.
CELERY_CONFIG = {
    "broker_url": REDIS_URL,
    "result_backend": REDIS_URL,
    "include": [],
}

# Backboard RAG.
BACKBOARD_API_KEY = os.getenv("BACKBOARD_API_KEY", "")
BACKBOARD_ASSISTANT_ID = os.getenv("BACKBOARD_ASSISTANT_ID", "")
BACKBOARD_THREAD_ID = os.getenv("BACKBOARD_THREAD_ID", "")
BACKBOARD_BASE_URL = os.getenv(
    "BACKBOARD_BASE_URL", "https://app.backboard.io/api"
)
BACKBOARD_LLM_PROVIDER = os.getenv("BACKBOARD_LLM_PROVIDER", "openai")
BACKBOARD_MODEL_NAME = os.getenv("BACKBOARD_MODEL_NAME", "gpt-4o")
