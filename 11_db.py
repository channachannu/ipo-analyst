"""
11_db.py
========
Single shared Supabase client for the IPO Analyst app.

daf_users lives in a Supabase project shared across any app using DAF
(Dynamic Auth Framework) — one account works everywhere DAF is wired in.
This file has no dependency on any other project's code; it only shares
a database table by convention. Import get_supabase() from here; never
create a client elsewhere.
"""

import os
import streamlit as st
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


@st.cache_resource
def get_supabase() -> Client:
    """
    Create and cache a single Supabase client.
    Streamlit secrets take priority — .env fallback for local dev.
    """
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    except Exception:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")

    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and SUPABASE_KEY not found. "
            "Add them to .env or Streamlit secrets."
        )

    return create_client(url, key)
