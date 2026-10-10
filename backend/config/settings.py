import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing in .env file")

if not TAVILY_API_KEY:
    raise ValueError("TAVILY_API_KEY is missing in .env file")

if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL is missing in .env file")

if not SUPABASE_KEY:
    raise ValueError("SUPABASE_KEY is missing in .env file")


def _csv(name: str, default: str = "") -> list[str]:
    return [
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    ]


# Public values the browser needs for Supabase Auth. The publishable
# key is designed to be public; never put a secret/service key here.
SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")

# Admin allowlist (comma-separated emails; not a secret). An account is an
# admin only if its email is listed here AND public.profiles.role = 'ADMIN'.
# Listing an email never grants anything by itself.
ADMIN_EMAILS = [email.lower() for email in _csv("ADMIN_EMAIL")]

# Browser origins allowed to call the API cross-origin. The frontend is
# served by this same app, so none are needed by default.
ALLOWED_ORIGINS = _csv("ALLOWED_ORIGINS")

APP_ENV = os.getenv("APP_ENV", "development")
APP_VERSION = os.getenv("APP_VERSION", "1.2.0")

MAX_PDF_MB = int(os.getenv("MAX_PDF_MB", "25"))
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "10"))
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "25"))

# Per-request timeout (seconds) and retries for every Groq call. The SDK
# default (10 minutes) would let one hung call hold a worker thread.
GROQ_TIMEOUT_S = float(os.getenv("GROQ_TIMEOUT_S", "60"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "2"))
