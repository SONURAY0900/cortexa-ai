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

# Email one-time-code sign-in. Supabase Auth issues, expires and verifies
# every code: set "Email OTP expiration" to 600 seconds in the Supabase
# Dashboard (Authentication → Sign In / Providers → Email). These values
# are only shown to the user (countdowns) and must match that setting and
# Supabase's resend interval; the browser never decides validity.
EMAIL_OTP_EXPIRY_S = int(os.getenv("EMAIL_OTP_EXPIRY_S", "600"))
EMAIL_OTP_RESEND_S = int(os.getenv("EMAIL_OTP_RESEND_S", "60"))

# Welcome email for new accounts (services/welcome_email.py). Disabled
# unless a provider is configured: "smtp", "resend" or "brevo".
WELCOME_EMAIL_PROVIDER = os.getenv("WELCOME_EMAIL_PROVIDER", "").strip().lower()
EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "").strip()
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
