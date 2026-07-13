"""Supabase client helper for the Streamlit app."""

import os

from dotenv import load_dotenv
from supabase import create_client


load_dotenv()


def get_supabase_client():
    """Create and return a Supabase client from environment variables."""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing Supabase environment variables")

    return create_client(supabase_url, supabase_key)
