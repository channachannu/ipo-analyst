"""
DRHP/RHP IPO Analyst — Streamlit Chat UI
=========================================

Thin UI layer over 05_query_engine.py. Deliberately one-shot: each question
is classified and answered independently, with no memory of prior turns in
this session (a project decision, not a technical limitation — see README).

Gated behind DAF (Dynamic Auth Framework) login. Uses the real DAF logic
directly — 10_dpp_core.py (canonical DPP engine) + 11_db.py/12_auth.py
(Supabase, same daf_users table as the Mind Map app) — rather than calling
a separately-running FastAPI service. Simpler to run, and accounts are
shared across both apps (same Supabase project).

Run this app with:
    streamlit run 09_streamlit_app.py

Requires 05_query_engine.py, 10_dpp_core.py, 11_db.py, 12_auth.py,
03_milky_mist_kb_sample.json, and your .env (ANTHROPIC_API_KEY, LLM_BACKEND,
SUPABASE_URL, SUPABASE_KEY) in the same directory.
"""

import streamlit as st
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util

spec = importlib.util.spec_from_file_location("query_engine", os.path.join(os.path.dirname(os.path.abspath(__file__)), "05_query_engine.py"))
qe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qe)

_spec_auth = importlib.util.spec_from_file_location("daf_auth", os.path.join(os.path.dirname(os.path.abspath(__file__)), "12_auth.py"))
daf_auth = importlib.util.module_from_spec(_spec_auth)
_spec_auth.loader.exec_module(daf_auth)


st.set_page_config(page_title="IPO Analyst — Milky Mist DRHP/RHP", page_icon="📄", layout="centered")


# ---------------------------------------------------------------------------
# DAF login gate — nothing below this runs until daf_auth.is_authenticated().
# ---------------------------------------------------------------------------

if not daf_auth.is_authenticated():
    daf_auth.show_auth_page()
    st.stop()


# ---------------------------------------------------------------------------
# Everything below only executes after a successful DAF login.
# ---------------------------------------------------------------------------

daf_user = daf_auth.get_current_user()

@st.cache_resource
def get_kb():
    return qe.load_kb()


kb = get_kb()

st.title("📄 IPO Analyst")
st.caption("Milky Mist Dairy Food Limited — grounded to the RHP only. No investment advice, no projections.")

with st.sidebar:
    st.success(f"Logged in as **{daf_user['username']}**")
    if st.button("Log out"):
        daf_auth.logout()
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("Settings")
    backend = st.radio("LLM backend", ["anthropic", "ollama"], index=0 if qe.LLM_BACKEND == "anthropic" else 1)
    if backend != qe.LLM_BACKEND:
        qe.LLM_BACKEND = backend  # override for this session
        st.info(f"Backend set to {backend} for this session.")

    show_internals = st.toggle("Show pipeline internals", value=False,
                                help="See which gate handled the question, which KB paths were retrieved, and the raw grounded context.")

    st.divider()
    st.caption("This assistant answers only from the RHP's own disclosures. "
               "Questions about investment advice, fair value, or future "
               "projections are declined by design (Gate 1).")

    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("internals"):
            with st.expander("Pipeline internals"):
                st.json(msg["internals"])

question = st.chat_input("Ask about the offer, financials, promoters, risks...")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Checking guardrails and knowledge base..."):
            result = qe.answer(question, kb)

        st.markdown(result.answer)

        internals = {
            "gate": result.gate,
            "category": result.category,
            "kb_paths_used": result.kb_paths,
        }
        if result.refusal_reason:
            internals["refusal_reason"] = result.refusal_reason

        if show_internals:
            with st.expander("Pipeline internals", expanded=True):
                st.json(internals)
                if result.context:
                    st.caption("Grounded context sent to the LLM:")
                    st.json(result.context)

    st.session_state.messages.append({
        "role": "assistant",
        "content": result.answer,
        "internals": internals,
    })
