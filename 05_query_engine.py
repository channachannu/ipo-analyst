"""
DRHP Query/Answer Engine
========================

Core principle (same as the Sun Pharma project): Python decides WHAT can be
answered and WHAT the numbers are. The LLM only narrates. It never:
  - retrieves its own context (Python selects only the relevant KB slice)
  - decides if a question is answerable (Python checks the rule engine first)
  - computes a number (Python already computed it, or refuses)

THREE-GATE PIPELINE (Gate 1 regex, Gate 2 LLM, Gate 3 Python validation):

  GATE 1 — deterministic blocklist (regex, no LLM call, runs first, always).
           Should-I-subscribe / fair-value / future-projection / etc. get
           blocked here, unconditionally. This is authoritative: nothing
           downstream can override a Gate 1 block. Kept as regex on purpose
           — a probabilistic classifier is the wrong tool for the one gate
           whose entire job is "never let this category through."

  GATE 2 — LLM classification (only runs on what survives Gate 1). Given an
           enumerated catalog of valid KB paths (not free text), the LLM
           picks the best-matching path(s), or says none apply. This is
           where typo/plural/phrasing robustness comes from — replacing the
           fragile regex routing table that kept breaking on real questions
           ("promotoers", "share holdings", "what is" swallowing other
           questions, etc.)

  GATE 3 — Python validates Gate 2's answer. If the LLM names a KB path that
           doesn't actually exist, that's treated as a failed classification
           (refuse), never as a free-text answer. The LLM's classification
           output is data to be checked, not a decision to be trusted.

  Then, same as before: retrieve() pulls only the validated KB slice, and
  narrate() (a SEPARATE LLM call, strictly grounded) produces the answer.

Run standalone to see the full pipeline. narrate() and gate2 both degrade
gracefully if the LLM backend is unreachable.
"""

import json
import re
import requests
from dataclasses import dataclass, field
from typing import Optional


import os
from dotenv import load_dotenv
load_dotenv()  # reads .env from the current working directory by default

LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama")  # "ollama" or "anthropic"
ANTHROPIC_MODEL = "claude-sonnet-5"



KB_PATH = "03_milky_mist_kb_sample.json"
RULES_PATH = "02_inference_rules.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_TIMEOUT_SECONDS = 180  # CPU-only inference on 8GB RAM can be slow, especially
                               # on the first call (model load into memory). Raise
                               # further if you still see timeouts.
MODEL_NAME = "ipo-analyst"  # built from 07_Modelfile (`ollama create ipo-analyst -f 07_Modelfile`)


# ---------------------------------------------------------------------------
# Data model for a classified question
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    question: str
    category: str                      # "1_direct_lookup" | "2_derived_inference" | "3_out_of_scope"
    kb_paths: list = field(default_factory=list)
    context: Optional[dict] = None
    refusal_reason: Optional[str] = None
    answer: Optional[str] = None
    gate: Optional[str] = None         # "gate1_blocked" | "gate2_no_match" | "gate3_invalid_path" | "answered"


# ---------------------------------------------------------------------------
# GATE 1 — deterministic blocklist. Regex on purpose: this is the one gate
# where determinism matters more than phrasing robustness. Runs first,
# always, and is authoritative — nothing downstream overrides a Gate 1 block.
# ---------------------------------------------------------------------------

BLOCKLIST_PATTERNS = [
    (r"\b(should i (subscribe|buy|invest))\b",
     "Investment recommendations are out of scope — the DRHP presents facts, not advice."),
    (r"\b(fair value|fairly priced|target price|is it (over|under)valued)\b",
     "Valuation opinions require market judgment beyond what's disclosed in the DRHP."),
    (r"\b(stable leadership|management stability|is (the |)management (stable|good))\b",
     "'Stability' is a judgment call — the DRHP discloses raw KMP tenure only, not an assessment."),
    (r"\b(future|next year|forecast|projected|will.*(grow|increase|decline))\b",
     "The DRHP discloses historical and current figures only; projecting forward isn't something I can ground in this document."),
    (r"\bcompar(e|ison).*(competitor|peer)\b",
     "I can only compare against peers the DRHP itself names in its comparison table, if any."),
]


def gate1_blocked(question: str) -> tuple[bool, Optional[str]]:
    """Deterministic check. No LLM call. Returns (is_blocked, reason)."""
    q = question.lower()
    for pattern, reason in BLOCKLIST_PATTERNS:
        if re.search(pattern, q):
            return True, reason
    return False, None


# ---------------------------------------------------------------------------
# GATE 2 — LLM classification. The LLM chooses from this ENUMERATED catalog
# only — it cannot invent a path. Kept hand-maintained alongside the KB for
# now (same drift risk as PATTERNS had — see 06_known_limitations.md if this
# needs revisiting once the KB grows further).
#
# Each entry carries a "category":
#   "direct"   — raw fact disclosed verbatim in the RHP, needs a source_page
#   "derived"  — computed by Python from other KB facts (e.g. ratios) —
#                still needs a source_page for the underlying data, but the
#                figure itself is Python's calculation, not a quote
#   "ontology" — general financial/accounting knowledge (what RoNW means,
#                how P/E is calculated) — NOT an RHP disclosure, must never
#                get a fake page citation, must be framed as general
#                knowledge rather than something the RHP "says"
# ---------------------------------------------------------------------------

KB_PATH_CATALOG = [
    {"path": "L0_offer_snapshot", "category": "direct", "description": "Offer details: fresh issue amount, offer for sale amount, total offer size, face value, pre/post-offer share counts, exchange listing"},
    {"path": "L0_offer_snapshot.price_band", "category": "direct", "description": "IPO price band (floor/cap price per share)"},
    {"path": "L0_offer_snapshot.lot_size", "category": "direct", "description": "Minimum bid lot size"},
    {"path": "L0_offer_snapshot.equity_shares_pre_offer", "category": "direct", "description": "Number of equity shares outstanding before the offer"},
    {"path": "L0_offer_snapshot.use_of_proceeds", "category": "direct", "description": "How the company plans to use/deploy/utilise IPO proceeds — objects of the offer, repayment of debt, capex, general corporate purposes"},
    {"path": "L0_offer_snapshot.pre_ipo_placement_completed", "category": "direct", "description": "Details of the pre-IPO placement completed before RHP filing, and why the fresh issue size changed"},
    {"path": "L2_financial_core.fiscal_years", "category": "direct", "description": "Balance sheet figures: total assets, total equity, total liabilities, borrowings, trade payables, by fiscal year"},
    {"path": "L2_financial_core._computed_by_python_not_llm", "category": "derived", "description": "Precomputed ratios: debt-to-equity by fiscal year and its trend"},
    {"path": "L1_business_fundamentals.industry_market_size", "category": "direct", "description": "Dairy / value-added dairy products (VADP) market size and growth data"},
    {"path": "L3_valuation.peer_comparison", "category": "direct", "description": "Listed industry peer comparison: P/E, EPS, RoNW, Net Worth, NAV for the company and its peers"},
    {"path": "L4_risk_layer.promoter_holding", "category": "direct", "description": "Promoter names, pre-offer and post-offer shareholding percentages, dilution"},
    {"path": "L4_risk_layer.litigation", "category": "direct", "description": "Litigation and material-creditor materiality thresholds set by the company"},
    {"path": "L4_risk_layer.risk_factors", "category": "direct", "description": "Curated key risk factors with disclosed cash-flow or brand impact"},
    {"path": "glossary_sample.terms", "category": "direct", "description": "Definitions of DRHP-specific terms (e.g. Bulk Milk Cooler, Milk Chilling Centres, CCPS) as defined BY THE RHP ITSELF, page-cited"},
    {"path": "financial_ontology.terms", "category": "ontology", "description": "General financial/accounting term definitions (RoNW, EPS, P/E, D/E, CAGR, materiality) — standard knowledge, NOT specific to or disclosed by this RHP. Use for 'what does X mean' questions about financial jargon, as opposed to RHP-specific terms."},
]

CLASSIFIER_SYSTEM_PROMPT = """You are a routing classifier for a DRHP knowledge base. You will be given a
question and a fixed catalog of available KB paths with descriptions.

Your ONLY job: pick which catalog path(s), if any, would contain the answer.

Rules:
- You MUST choose only from the "path" values given in the catalog. Never
  invent a path that isn't listed.
- If multiple paths are relevant, list all of them.
- If NO path in the catalog would answer the question, return an empty list.
  Do not guess or pick the closest-sounding one — an empty list is the
  correct answer when nothing fits.
- Respond with ONLY a JSON object, no other text, no markdown fences:
  {"kb_paths": ["path1", "path2"]}
  or
  {"kb_paths": []}
"""


def gate2_llm_classify(question: str, catalog: list = KB_PATH_CATALOG) -> list:
    """Ask the LLM to pick from the enumerated catalog. Returns a list of
    claimed paths — NOT yet validated against the real KB (that's Gate 3).
    Returns [] on any failure — fail closed, same philosophy as the rest
    of this pipeline (unmatched/uncertain = refuse, not guess)."""
    catalog_text = "\n".join(f"- {c['path']}: {c['description']}" for c in catalog)
    user_content = f"Catalog:\n{catalog_text}\n\nQuestion: {question}\n\nJSON response:"

    raw = None
    try:
        if LLM_BACKEND == "anthropic":
            import anthropic
            client = anthropic.Anthropic()
            resp = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=300,
                system=CLASSIFIER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text_blocks = [block.text for block in resp.content if block.type == "text"]
            raw = "\n".join(text_blocks).strip()
        else:
            prompt = f"{CLASSIFIER_SYSTEM_PROMPT}\n\n{user_content}"
            resp = requests.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "prompt": prompt, "stream": False},
                timeout=OLLAMA_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
    except Exception:
        return []  # LLM unreachable/failed -> fail closed, Gate 3 will refuse

    if not raw:
        return []

    # Defensive parsing: strip markdown fences if the model added them anyway
    cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        paths = parsed.get("kb_paths", [])
        return paths if isinstance(paths, list) else []
    except (json.JSONDecodeError, AttributeError):
        return []  # malformed JSON -> fail closed


# ---------------------------------------------------------------------------
# GATE 3 — Python validates Gate 2's claimed paths against the REAL KB.
# A path the LLM named but that doesn't actually exist is dropped, not
# trusted. If nothing survives validation, that's a refusal.
# ---------------------------------------------------------------------------

def gate3_validate(kb: dict, claimed_paths: list) -> list:
    """Returns only the claimed paths that actually resolve to real KB data."""
    valid = []
    for path in claimed_paths:
        if get_nested(kb, path) is not None:
            valid.append(path)
    return valid


# ---------------------------------------------------------------------------
# Knowledge base access
# ---------------------------------------------------------------------------

def load_kb(path: str = KB_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def load_rules(path: str = RULES_PATH) -> dict:
    with open(path) as f:
        return json.load(f)


def get_nested(d: dict, dotted_path: str):
    """Walk a dotted path like 'L4_risk_layer.promoter_holding' through the KB."""
    node = d
    for part in dotted_path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def retrieve(kb: dict, kb_paths: list) -> dict:
    """Pull ONLY the requested slices — never the whole KB into the prompt."""
    context = {}
    for path in kb_paths:
        node = get_nested(kb, path)
        if node is not None:
            context[path] = node
    return context


# ---------------------------------------------------------------------------
# LLM narration (runs only after Gates 1-3 pass — context is pre-validated)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a DRHP financial analyst assistant. You will be given:
1. A user question
2. A JSON context block containing the ONLY facts you are allowed to use

Rules, no exceptions:
- Use ONLY the values in the context block. Never introduce a number, date, name,
  or percentage that isn't in the context.
- Every factual claim must reference its source_page from the context.
- If a field's disclosure_status is "PENDING_DISCLOSURE", say plainly that it
  is not yet disclosed. Never estimate a plausible value for it.
- Do NOT perform arithmetic. All numbers (ratios, percentages, deltas) are
  already computed in the context. Only narrate them.
- Do NOT infer causation unless the context contains an explicit disclosed
  reason (e.g. a "_change_note" or similarly named field). If no reason is
  given, state that the DRHP does not explain the change.
- Do NOT use the word "stable", "risky", "attractive", "cheap", "expensive",
  or any other evaluative judgment. Report facts and their direction only.
- Start your answer with the first fact itself. No lead-in sentence of any
  kind — not "Based on...", not "According to the DRHP...", not "The
  company plans to...". The first words of your answer must already be
  part of the answer.
  BAD:  "Based on the 'Objects of the Offer' section (page 125), the
         company plans to raise gross proceeds of..."
  GOOD: "Gross proceeds from the Fresh Issue: INR 14,280.0 million (p.125)."
- Cite page numbers inline in parentheses right after each fact, not as a
  separate list or explanation at the end.
- For multi-part answers, use a compact list — each line is a fact with
  its citation, not a sentence introducing what the list is about.
- No closing summary sentence either. End on the last fact.
"""

ONTOLOGY_ADDENDUM = """
ADDITIONAL RULE FOR THIS ANSWER — the context includes general financial/
accounting term definitions (financial_ontology), NOT RHP disclosures:
- These entries have NO source_page — they are standard finance knowledge,
  not something the RHP states. NEVER invent a page number for them.
- Explicitly frame these as general knowledge, not an RHP disclosure —
  e.g. "RoNW (Return on Net Worth) is a standard financial ratio measuring
  ..." not "The RHP defines RoNW as...". The RHP did not define it; you are
  supplying the general definition.
- If the context ALSO contains RHP-specific data using that term (e.g. the
  company's actual RoNW value elsewhere in context), that part DOES need
  its normal source_page citation — only the definition itself is citation-free.
"""


def narrate(question: str, context: dict, category: str = "1_direct_lookup") -> str:
    """Call the configured LLM backend. Falls back gracefully if unreachable —
    this lets the retrieval/guardrail logic be tested without the LLM running.
    category="ontology" appends a rule set forbidding fake page citations for
    general financial-term definitions (as opposed to RHP disclosures)."""
    system_prompt = SYSTEM_PROMPT + (ONTOLOGY_ADDENDUM if category == "ontology" else "")
    prompt = f"{system_prompt}\n\nQuestion: {question}\n\nContext:\n{json.dumps(context, indent=2)}\n\nAnswer:"

    if LLM_BACKEND == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
            resp = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1000,
                system=system_prompt,
                messages=[{"role": "user", "content": f"Question: {question}\n\nContext:\n{json.dumps(context, indent=2)}\n\nAnswer:"}],
            )
            text_blocks = [block.text for block in resp.content if block.type == "text"]
            return "\n".join(text_blocks).strip()
        except Exception as e:
            return f"[Anthropic API call failed: {e}]"
    ### continues with Ollama
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.Timeout:
        return (f"[LLM call timed out after {OLLAMA_TIMEOUT_SECONDS}s — Ollama is running and "
                f"connected, it just didn't finish generating in time. This is expected on "
                f"CPU-only/8GB hardware, especially on the first call while the model loads "
                f"into memory. Try again (warm model is faster), or raise OLLAMA_TIMEOUT_SECONDS.]"
                f"\n\nGrounded context that WOULD have been sent:\n{json.dumps(context, indent=2)}")
    except requests.exceptions.RequestException as e:
        return f"[LLM unreachable — Ollama not running or model '{MODEL_NAME}' not found: {e}]\n\nGrounded context that WOULD have been sent:\n{json.dumps(context, indent=2)}"

# ---------------------------------------------------------------------------
# Orchestration — three gates, in order, Gate 1 authoritative
# ---------------------------------------------------------------------------

_CATALOG_CATEGORY_MAP = {entry["path"]: entry.get("category", "direct") for entry in KB_PATH_CATALOG}

# Revives 02_inference_rules.json — previously dead code (load_rules() had
# no caller). max_hop_count is the one piece of that file's design that
# still maps cleanly onto the flat KB_PATH_CATALOG architecture: if a
# question needs more than this many distinct KB sections combined, that's
# treated as the LLM constructing a narrative rather than doing a lookup,
# per the original hop-count rationale.
try:
    _RULES = load_rules()
    MAX_HOP_COUNT = _RULES.get("max_hop_count", 2)
except (FileNotFoundError, json.JSONDecodeError):
    MAX_HOP_COUNT = 2  # safe fallback if the rules file is missing/broken


def _determine_category(valid_paths: list) -> str:
    """A question is 'ontology' if ANY retrieved path is ontology (narration
    must not cite a fake page for it), else 'derived' if ANY path is a
    Python-computed field, else 'direct'. Ontology takes priority over
    derived, which takes priority over direct, since narration rules for
    the riskier category (no-fabrication requirements) must apply whenever
    that category is present at all, even mixed with simpler paths."""
    categories = {_CATALOG_CATEGORY_MAP.get(p, "direct") for p in valid_paths}
    if "ontology" in categories:
        return "ontology"
    if "derived" in categories:
        return "2_derived_inference"
    return "1_direct_lookup"


def answer(question: str, kb: dict) -> QueryResult:
    # GATE 1 — deterministic, always runs first, always wins
    blocked, reason = gate1_blocked(question)
    if blocked:
        return QueryResult(
            question=question, category="3_out_of_scope", gate="gate1_blocked",
            refusal_reason=reason, answer=f"I can't answer that: {reason}",
        )

    # GATE 2 — LLM picks from the enumerated catalog only
    claimed_paths = gate2_llm_classify(question)

    # GATE 3 — Python validates the LLM's claim against the real KB
    valid_paths = gate3_validate(kb, claimed_paths)

    if not valid_paths:
        reason = ("No matching information found in the knowledge base for this question "
                   "— either it isn't disclosed in the DRHP, or it's outside what this KB "
                   "currently covers.")
        gate = "gate3_invalid_path" if claimed_paths else "gate2_no_match"
        return QueryResult(
            question=question, category="3_out_of_scope", gate=gate,
            refusal_reason=reason, answer=f"I can't answer that: {reason}",
        )

    # HOP-COUNT ENFORCEMENT (revived from 02_inference_rules.json) — too many
    # combined KB sections for one question is a signal the LLM is building
    # a narrative, not doing a lookup. Refuse and ask for a narrower question
    # rather than silently combining everything Gate 2 proposed.
    if len(valid_paths) > MAX_HOP_COUNT:
        reason = (f"This question needs {len(valid_paths)} different sections of the "
                  f"knowledge base to answer, which is more than I combine in one go "
                  f"(limit: {MAX_HOP_COUNT}). Try breaking it into narrower questions.")
        return QueryResult(
            question=question, category="3_out_of_scope", gate="gate3_hop_limit_exceeded",
            kb_paths=valid_paths, refusal_reason=reason, answer=f"I can't answer that as one question: {reason}",
        )

    category = _determine_category(valid_paths)
    context = retrieve(kb, valid_paths)
    return QueryResult(
        question=question, category=category, gate="answered",
        kb_paths=valid_paths, context=context, answer=narrate(question, context, category),
    )


# ---------------------------------------------------------------------------
# Demo / self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    kb = load_kb()

    test_questions = [
        "What is the fresh issue size?",                                   # Gate 2/3, direct lookup
        "What is the price band?",                                         # Gate 2/3, PENDING
        "Who are the promotoers, and what's their pre/post share holdings?",  # typo test
        "What is the debt to equity ratio trend?",                         # derived inference
        "Why did the fresh issue size change?",                            # has disclosed reason
        "How does the company plan to use the IPO proceeds?",              # was the false-refusal bug
        "What is the size of the dairy market?",
        "How do peers compare on P/E ratio?",
        "What are the key risk factors?",
        "Should I subscribe to this IPO?",                                 # Gate 1, blocked
        "Is the leadership stable?",                                       # Gate 1, blocked
        "How will revenue grow next year?",                                # Gate 1, blocked
        "What is the CEO's favorite color?",                               # Gate 2/3, no match -> refuse
    ]

    for q in test_questions:
        r = answer(q, kb)
        print("=" * 70)
        print(f"Q: {q}")
        print(f"Gate: {r.gate} | Category: {r.category}")
        if r.refusal_reason:
            print(f"REFUSED: {r.refusal_reason}")
        else:
            print(f"KB paths retrieved: {r.kb_paths}")
        print(f"Answer:\n{r.answer}\n")

