from supabase import create_client, Client
from config.settings import SUPABASE_URL, SUPABASE_KEY


supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


def get_supabase() -> Client:
    return supabase