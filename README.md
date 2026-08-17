# DRHP/RHP IPO Analysis Assistant

A local, guardrailed Q&A system for analyzing an IPO using only its DRHP/RHP
document — financial fundamentals, not assumptions or recommendations.

Built as a learning project (companion to the Sun Pharma financial-analysis
project) to understand LLM failure modes — fabrication, instruction
compliance, reasoning limits — before building a production RAG system.
Test subject: **Milky Mist Dairy Food Limited** IPO (Draft RHP → RHP →
live listing, tracked as the filing progressed).

## Core principle

**Python decides WHAT can be answered and WHAT the numbers are. The LLM only
narrates.** It never retrieves its own context, decides on its own whether a
question is answerable, or computes a number. This mirrors the "Python
calculates, LLM interprets" pattern from the Sun Pharma project, extended
with disclosure-state handling and stricter citation rules learned from
testing against a real DRHP.

## Architecture: 3-gate query pipeline

```
Question
   │
   ▼
GATE 1 — Deterministic blocklist (regex, no LLM call)
   │      Catches: subscribe/buy advice, fair-value opinions, future
   │      projections, management-quality judgments. AUTHORITATIVE —
   │      nothing downstream can override a Gate 1 block.
   │
   ├── blocked ──────────────────────────────► Refuse, no LLM call at all
   │
   ▼ (survives Gate 1)
GATE 2 — LLM classification
   │      LLM picks from an ENUMERATED catalog of KB paths (KB_PATH_CATALOG)
   │      — it cannot invent a path. Handles typos, plurals, and phrasing
   │      variance that broke the earlier regex-only router.
   │
   ▼
GATE 3 — Python validation
   │      Confirms each path the LLM claimed actually exists in the KB.
   │      A hallucinated path is dropped, not trusted. No valid paths
   │      survive → refuse.
   │
   ▼ (valid paths only)
RETRIEVE — pull only the relevant KB slice (never the whole file)
   │
   ▼
NARRATE — separate LLM call, strict system prompt, grounded to that
          slice only. No arithmetic, no evaluative language, no
          preamble, inline citations only.
```

Why Gate 1 stays regex while Gate 2 is an LLM: Gate 1 is the safety-critical
gate whose only job is "never let this category through" — determinism
matters more than phrasing robustness there. Gate 2 is about finding the
right *answerable* data for a legitimate question — phrasing robustness
matters more than determinism there, and Gate 3 catches the cases where the
LLM guesses wrong.

## File manifest

| File | Purpose |
|---|---|
| `01_ontology_schema.json` | Empty template — entity types, mandatory fields per fact node, disclosure-state rules. Built once, not regenerated per filing. |
| `02_inference_rules.json` | Allowed vs. blocked reasoning chains (max 2 hops), refusal templates. |
| `03_milky_mist_kb_sample.json` | Populated knowledge base for this issuer, sourced from the actual RHP with page citations. Single current snapshot (no version history — see Known Limitations). |
| `04_knowledge_base_view.md` | Human-readable render of the KB. Read-only view; JSON is the source of truth. |
| `05_query_engine.py` | The 3-gate pipeline: blocklist, LLM classification, validation, retrieval, narration. Supports both Ollama (local) and Anthropic API backends. |
| `06_known_limitations.md` | Honest log of discovered bugs and design trade-offs, fixed and unfixed. |
| `07_Modelfile` | Ollama Modelfile (`ipo-analyst`) — system prompt rules for local Qwen3:4B narration. |
| `08_coverage_check.py` | Structural completeness checker: schema→KB (extraction gaps) and KB→catalog (Gate 2 routing gaps). Run after every KB or catalog change. |

## Knowledge base structure (ontology)

Top-down, L0 (broad) → L4 (deep-dive):

- **L0** — Offer snapshot: issue size, price band, shares outstanding, use of proceeds
- **L1** — Business fundamentals: industry, market size, revenue segments *(partially populated)*
- **L1b** — Management continuity: raw KMP tenure only, no "stability" judgment *(not yet populated)*
- **L2** — Financial core: balance sheet by fiscal year, Python-computed ratios (D/E)
- **L3** — Valuation: listed peer comparison, implied ratios
- **L4** — Risk layer: promoter holding, litigation materiality, curated risk factors, dependencies

Every fact node carries `value`, `source_page`, `source_section`, and
`disclosure_status` (`DISCLOSED` / `PENDING_DISCLOSURE` / `NOT_APPLICABLE`).
`PENDING_DISCLOSURE` matters specifically for draft filings — price band,
lot size, and post-offer allocations are genuinely undecided at DRHP/RHP
stage, not missing data to estimate.

## Setup

```bash
pip install requests python-dotenv anthropic
```

**.env file:**
```
ANTHROPIC_API_KEY=sk-ant-...
LLM_BACKEND=anthropic   # or "ollama"
```

**For the Ollama backend**, build the local model first:
```bash
ollama create ipo-analyst -f 07_Modelfile
```

**Run:**
```bash
python3 05_query_engine.py          # runs the built-in test question set
```
or in Jupyter:
```python
from importlib import reload
import query_engine as qe
reload(qe)   # required after any edit — modules are cached on import

kb = qe.load_kb()
result = qe.answer("Who are the promoters, and what are their holdings?", kb)
print(result.answer)
```

## Maintaining the knowledge base

After adding a new KB section, always:
1. Add a matching entry to `KB_PATH_CATALOG` in `05_query_engine.py` (Gate 2 can only choose paths that are listed there).
2. Run `python3 08_coverage_check.py` — Section A catches unextracted schema fields, Section B catches KB data with no Gate 2 route to it.
3. Update `_document_metadata.current_document_type` in the KB JSON if the source filing stage changed (Draft RHP → RHP → Prospectus) — this project intentionally does not keep version history, only a single current-state pointer, so this field is the only thing preventing silent staleness.

## Known limitations

See `06_known_limitations.md` for the full, honest log — includes fixed and
still-open issues: pattern-order shadowing (pre-redesign), regex
typo-intolerance (resolved by the Gate 2 LLM redesign), the coverage
checker's own false-positive on collection-type nodes, and the
hand-maintained catalog/schema drift risk that persists even after the
redesign.

## What this project is *not*

- Not a source of investment advice — Gate 1 blocks subscribe/buy/fair-value
  questions unconditionally, by design.
- Not a live market-data tracker — price band, GMP, subscription status, and
  similar post-filing/live-market facts are deliberately out of scope; the
  KB reflects only what the DRHP/RHP itself discloses. (A separate
  monitoring layer for this was scoped but deliberately not built — see
  chat history.)
- Not exhaustive — the risk-factors section is a curated subset (cash-flow/
  brand-relevant only, per the project's original inclusion rule), not all
  ~80 risk factors in the actual RHP.
