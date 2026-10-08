from supabase import Client

# One shared Supabase client for the whole backend.
from database.connection import supabase


def get_supabase() -> Client:
    return supabase
