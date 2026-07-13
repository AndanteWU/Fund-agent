"""Quick Supabase connection smoke test."""

from supabase_client import get_supabase_client


try:
    get_supabase_client()
    print("Supabase connection success")
except Exception as error:
    print(f"Supabase connection failed: {error}")
