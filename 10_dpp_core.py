"""
dpp_core.py
===========
Core Dynamic Password Protocol (DPP) engine.

Standalone module — no internal imports, no framework dependencies.

Origin : PHP/Python prototype built in 2019.
Paper  : "Dynamic Password Protocol for User Authentication"
         H. Channabasava & S. Kanthimathi — CompCom 2019, Springer Nature.

How DPP works
-------------
A password has two parts:
    Static  — characters the user remembers (e.g. 'Botnet')
    Dynamic — positions filled by a live parameter (e.g. UTC time 'HHMM')

Registration : 'Botxxnetxx' with placeholder 'x'
               → parameter map  : '0001100011'
               → static part    : 'Botnet'  (Argon2id hashed)

Login at 21:30 UTC : user enters 'Bot21net30'
               → dynamic extracted : '2130' == current UTC time ✅
               → static  extracted : 'Botnet' == stored hash    ✅

Security upgrades over original prototype
-----------------------------------------
    bcrypt          → Argon2id (memory-hard, OWASP 2024)
    string compare  → hmac.compare_digest (constant-time)
    hard-coded TZ   → UTC normalised
    specific errors → generic messages (prevents enumeration)
"""

import hmac
from datetime import datetime, timezone
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

# ---------------------------------------------------------------------------
# Argon2id hasher — OWASP 2024 recommended settings
# ---------------------------------------------------------------------------
_HASHER = PasswordHasher(
    time_cost=3,        # iterations
    memory_cost=65_536, # 64 MB — raises GPU attack cost significantly
    parallelism=4,
    hash_len=32,
)

DEFAULT_PLACEHOLDER: str = "x"


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RegistrationPayload:
    """Data to persist after successful registration."""
    static_hash:   str  # Argon2id hash of static characters
    parameter_map: str  # Binary string — '1'=dynamic, '0'=static


@dataclass(frozen=True)
class AuthResult:
    """Result of an authentication attempt."""
    success: bool
    reason:  str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_parameter_map(password: str, placeholder: str) -> str:
    """Build binary map — '1' where placeholder, '0' elsewhere."""
    return "".join("1" if ch == placeholder else "0" for ch in password)


def _extract_static_part(password: str, parameter_map: str) -> str:
    """Collect characters at '0' (static) positions."""
    return "".join(ch for ch, flag in zip(password, parameter_map) if flag == "0")


def _extract_dynamic_part(password: str, parameter_map: str) -> str:
    """Collect characters at '1' (dynamic) positions."""
    return "".join(ch for ch, flag in zip(password, parameter_map) if flag == "1")


def _get_current_time_parameter() -> str:
    """Current UTC time as HHMM — the default dynamic parameter."""
    return datetime.now(tz=timezone.utc).strftime("%H%M")


def _secure_compare(a: str, b: str) -> bool:
    """Constant-time comparison — prevents timing side-channel attacks."""
    return hmac.compare_digest(a.encode(), b.encode())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def register(
    password:    str,
    placeholder: str = DEFAULT_PLACEHOLDER,
) -> RegistrationPayload:
    """
    Process a registration password.

    Extracts the static part, hashes it with Argon2id,
    and returns the hash + parameter map for storage.

    Raises ValueError for invalid inputs.
    """
    if not password:
        raise ValueError("Password must not be empty.")
    if len(placeholder) != 1:
        raise ValueError("Placeholder must be exactly one character.")
    if all(ch == placeholder for ch in password):
        raise ValueError("Password must contain at least one static character.")

    parameter_map = _build_parameter_map(password, placeholder)
    static_part   = _extract_static_part(password, parameter_map)
    static_hash   = _HASHER.hash(static_part)

    return RegistrationPayload(static_hash=static_hash, parameter_map=parameter_map)


def authenticate(
    input_password:   str,
    stored_hash:      str,
    parameter_map:    str,
    expected_dynamic: str | None = None,
) -> AuthResult:
    """
    Two-stage DPP authentication.

    Stage 1 — Dynamic : extracted chars must match live UTC time (HHMM).
    Stage 2 — Static  : extracted chars must match stored Argon2id hash.

    Both stages must pass. Always returns generic failure message.
    """
    _FAIL = AuthResult(success=False, reason="Invalid credentials.")

    if len(input_password) != len(parameter_map):
        return _FAIL

    # Stage 1 — Dynamic
    dynamic_part = _extract_dynamic_part(input_password, parameter_map)
    live_dynamic = expected_dynamic or _get_current_time_parameter()
    if not _secure_compare(dynamic_part, live_dynamic):
        return _FAIL

    # Stage 2 — Static
    static_part = _extract_static_part(input_password, parameter_map)
    try:
        _HASHER.verify(stored_hash, static_part)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return _FAIL

    return AuthResult(success=True, reason="Authentication successful.")
