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