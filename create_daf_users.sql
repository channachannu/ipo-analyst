-- ─────────────────────────────────────────────────────────────────────────────
-- create_daf_users.sql
-- Run ONCE in Supabase SQL Editor before using DAF login/register in any
-- app. This table is shared across any project using DAF — one account
-- works everywhere. This file has no dependency on any other project's
-- schema; run it standalone.
--
-- Matches the columns 12_auth.py reads/writes via the Supabase client.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS daf_users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    static_hash   TEXT NOT NULL,
    parameter_map VARCHAR(256) NOT NULL,
    placeholder   VARCHAR(1) NOT NULL DEFAULT 'x',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_daf_users_username ON daf_users(username);

-- No Supabase Auth in use here, so no auth.uid() to write a real per-row
-- policy against. Lock this table to service_role only — enabled RLS with
-- zero anon/authenticated policies denies those roles by default.
ALTER TABLE daf_users ENABLE ROW LEVEL SECURITY;
