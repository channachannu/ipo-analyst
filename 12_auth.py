"""
12_auth.py
==========
DAF (Dynamic Password Protocol) authentication for the IPO Analyst app.

Imports the canonical 10_dpp_core.py directly — no reimplementation.
Talks to the shared daf_users table (Supabase) — any app using DAF reads
and writes the same table by convention, so one account works across all
of them. This file has no code dependency on any other project.

Reference:
  "Dynamic Password Protocol for User Authentication"
  H. Channabasava & S. Kanthimathi, CompCom 2019, Springer Nature
"""

import os
import sys
import importlib.util
from datetime import datetime, timezone

import streamlit as st

# Load 10_dpp_core.py by path (filename starts with a digit, not import-safe directly)
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("dpp_core", os.path.join(_here, "10_dpp_core.py"))
dpp_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dpp_core)

_spec_db = importlib.util.spec_from_file_location("daf_db", os.path.join(_here, "11_db.py"))
daf_db = importlib.util.module_from_spec(_spec_db)
_spec_db.loader.exec_module(daf_db)


# A precomputed dummy hash used only to keep login timing constant whether
# or not the username exists — closes a username-enumeration timing
# side-channel (same class of fix applied wherever DAF is integrated).
_DUMMY_HASH = dpp_core._HASHER.hash("dummy-static-part-for-timing-parity")


# ── Supabase DB operations (shared daf_users table) ───────────────────────────

def db_user_exists(username: str) -> bool:
    supabase = daf_db.get_supabase()
    result = supabase.table("daf_users").select("id").eq("username", username).execute()
    return len(result.data) > 0


def db_create_user(username: str, static_hash: str, parameter_map: str, placeholder: str):
    supabase = daf_db.get_supabase()
    supabase.table("daf_users").insert({
        "username":      username,
        "static_hash":   static_hash,
        "parameter_map": parameter_map,
        "placeholder":   placeholder,
        "is_active":     True,
    }).execute()


def db_get_user(username: str) -> dict | None:
    supabase = daf_db.get_supabase()
    result = supabase.table("daf_users").select("*").eq("username", username).execute()
    return result.data[0] if result.data else None


# ── Session helpers ───────────────────────────────────────────────────────────

def is_authenticated() -> bool:
    return st.session_state.get("daf_authenticated", False)


def get_current_user() -> dict | None:
    return st.session_state.get("daf_current_user", None)


def login(user: dict):
    st.session_state["daf_authenticated"] = True
    st.session_state["daf_current_user"] = user


def logout():
    for key in ["daf_authenticated", "daf_current_user"]:
        st.session_state.pop(key, None)


def _attempt_login(username: str, password: str) -> dict | None:
    """Timing-safe: runs a real Argon2id verify whether or not the user
    exists, so response time doesn't reveal username validity."""
    user = db_get_user(username)

    if user is None or not user.get("is_active"):
        dpp_core._HASHER.verify(_DUMMY_HASH, "dummy-static-part-for-timing-parity")
        return None

    result = dpp_core.authenticate(
        input_password=password,
        stored_hash=user["static_hash"],
        parameter_map=user["parameter_map"],
    )
    return user if result.success else None


# ── Auth UI ───────────────────────────────────────────────────────────────────

def show_auth_page():
    """Render login/register UI. Blocks app rendering until authenticated."""
    st.title("🔐 IPO Analyst — Login")
    st.caption("Secured by DAF (Dynamic Auth Framework) — Dynamic Password Protocol. "
               "Same account works across any app using DAF.")

    utc_now = datetime.now(tz=timezone.utc)
    hhmm = utc_now.strftime("%H%M")
    st.info(f"**Current UTC time: `{utc_now.strftime('%H:%M')}`** "
            f"— fill your dynamic (`x`) positions with `{hhmm}`.")

    tab_login, tab_register = st.tabs(["Login", "Register"])

    with tab_login:
        with st.form("daf_login_form"):
            username = st.text_input("Username")
            password = st.text_input(
                "Password (static part + live UTC time, e.g. 'Bot21net30')",
                type="password",
            )
            submitted = st.form_submit_button("Log in")

        if submitted:
            if not username or not password:
                st.error("Enter both username and password.")
            else:
                with st.spinner("Verifying..."):
                    user = _attempt_login(username.strip(), password)
                if user:
                    login(user)
                    st.rerun()
                else:
                    st.error("Invalid credentials.")  # generic — same message regardless of failure reason

    with tab_register:
        st.caption("Static characters + placeholder positions, e.g. `Botxxnetxx` (x = dynamic).")
        with st.form("daf_register_form"):
            reg_username = st.text_input("Choose a username")
            reg_password = st.text_input("Choose a password pattern (include placeholder chars)")
            reg_placeholder = st.text_input("Placeholder character", value="x", max_chars=1)
            reg_submitted = st.form_submit_button("Register")

        if reg_submitted:
            if not reg_username or not reg_password:
                st.error("Enter both username and password pattern.")
            else:
                with st.spinner("Creating account..."):
                    try:
                        uname = reg_username.strip()
                        if db_user_exists(uname):
                            st.error("That username is already taken.")
                        else:
                            payload = dpp_core.register(reg_password, reg_placeholder)
                            db_create_user(uname, payload.static_hash, payload.parameter_map, reg_placeholder)
                            st.success(
                                f"Registered. Your parameter map is `{payload.parameter_map}` "
                                f"— dynamic positions get filled with the live UTC HHMM at login."
                            )
                    except ValueError as e:
                        st.error(str(e))

    st.stop()
