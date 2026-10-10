import httpx
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions

from config.settings import SUPABASE_URL, SUPABASE_KEY


# One HTTP/1.1 connection pool shared by the Supabase auth and database
# clients. supabase-py defaults to HTTP/2, whose single multiplexed
# connection is not safe to share between FastAPI's worker threads: under
# concurrent requests it fails with transport errors such as
# "[WinError 10035] A non-blocking socket operation could not be completed
# immediately" and "ConnectionTerminated". httpx's HTTP/1.1 pool is
# thread-safe.
http_client = httpx.Client(
    http2=False,
    follow_redirects=True,
    timeout=httpx.Timeout(60.0, connect=10.0),
    limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
    options=SyncClientOptions(httpx_client=http_client),
)


def get_supabase() -> Client:
    return supabase
