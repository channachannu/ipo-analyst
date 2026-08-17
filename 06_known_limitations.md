# Known Limitations — Query Engine (logged, not yet fixed)

1. **Pattern order shadowing**: `PATTERNS` list uses first-match-wins. A general
   pattern earlier in the list can silently swallow a more specific one later
   in the list. Example: "Why did the fresh issue size change?" matches the
   general Category 1 "fresh issue" pattern before reaching the Category 2
   "why...change" pattern, so the disclosed reason (Pre-IPO placement) never
   surfaces. Fix direction: score all matches, pick most specific (longest
   match / most keywords hit), not first-match.

2. **Regex word-order brittleness**: `stable leadership` does not match
   `leadership stable`. Keyword/regex classification is inherently fragile to
   phrasing variance — this is the general argument for eventually moving to
   embedding-based intent matching once question variety outgrows a fixed
   pattern table. Not urgent at current scale (single DRHP, small question set).

Both bugs are classifier/routing issues, not LLM fabrication — the guardrail
itself held (no LLM call was made for blocked categories, no number was
invented). Worth keeping this distinction in the research notes: routing
fragility and generation fabrication are different failure classes.

3. **Coverage checker false positive on collection-type nodes**: `08_coverage_check.py`'s
   Section A heuristic assumes a populated schema leaf has `value` and
   `disclosure_status` keys directly on it. Collection-type nodes (e.g.
   `L4_risk_layer.risk_factors`, which holds a list of factor objects each
   with their own status/citation) don't match this shape, so the checker
   flags them as gaps even when populated. Confirmed real case: after adding
   real risk factor data, the checker still reported it as a gap. Fix
   direction: recognize a dict containing a non-empty list/array value
   (e.g. a "factors" or "cases" key) as covered, not just top-level
   value/disclosure_status. Low priority — produces a false "still missing"
   signal, not a false "covered" signal, so it's a safe-direction bug (erring
   toward caution) rather than a silent gap.

4. **[FIXED] use_of_proceeds regex typo**: pattern used bare word "us" instead
   of "use", so "How does the company plan to USE the proceeds?" never
   matched — the exact question the KB fix was built for. Fixed to "use".

5. **[FIXED] glossary pattern over-matched, confirmed in practice**: the
   glossary pattern's bare "what is/does" alternation shadowed
   "What is the size of the dairy market?" — a real, confirmed instance of
   the pattern-shadowing issue logged as limitation #1, not just a
   theoretical risk. Fixed by (a) tightening the regex to require an actual
   definitional cue (define/meaning/glossary/"what does X mean"), and
   (b) moving it to the end of the Category 1 list so more specific patterns
   get first refusal. Limitation #1 (first-match-wins generally) is still
   open — this was a targeted fix for one bad pattern, not the underlying
   architecture.

6. **[FIXED, but flags a broader pattern] Typo/plural intolerance in regex
   matching**: "promotoers" (typo) + "share holdings" (plural, unmatched
   trailing-boundary bug) both broke the promoter-holding pattern
   simultaneously — two independent bugs in one real user question. Fixed
   by switching exact-word alternatives to stem+\w* (e.g. "promot\w*",
   "hold\w*") so typo continuations, plurals, and tense variants all match
   the same stem. This is a narrower, more general fix than the earlier
   pattern-specific ones — worth applying the same stem+\w* treatment to
   the OTHER patterns in the table proactively, rather than waiting to
   discover each one via a false refusal. Not yet done for all patterns —
   this was fixed reactively for promoter_holding only, matching this
   project's "fix as discovered" approach so far. If typo-tolerance becomes
   a recurring problem, this is the strongest signal yet that fuzzy/
   embedding-based matching (already flagged as the eventual upgrade path)
   is worth prioritizing over continuing to patch individual regexes.

7. **Coverage checker blind to "populated but incomplete" collections**: "What
   is CCPS?" was wrongly refused — not a routing bug this time, Gate 2
   correctly identified it as a glossary question. The real gap: CCPS is
   USED elsewhere in the KB (`pre_ipo_placement_completed` references
   "25,000,000 CCPS") but was never DEFINED in `glossary_sample.terms`,
   which only ever held 2 illustrative terms, not the RHP's full glossary.
   `08_coverage_check.py` cannot catch this class of gap — Section A only
   flags fields that are entirely MISSING, not collections that exist but
   are incomplete. A populated `terms: [...]` list with 2 entries looks
   identical to the checker whether it's exhaustive or a small sample.
   Fixed this specific instance (added the real CCPS definition, p.4,
   sourced from the RHP's own Definitions and Abbreviations section).
   Systemic fix would require either extracting the full glossary
   (probably 100+ terms in a real RHP) or a new checker heuristic that
   cross-references terms USED elsewhere in the KB against terms DEFINED
   in the glossary and flags the difference — not built yet.

8. **[RESOLVED] Category tracking was hardcoded, inference-rules enforcement
   was dead code**: `answer()` previously hardcoded `category="1_direct_lookup"`
   for every successful answer, even when the retrieved path was a
   Python-computed field (`_computed_by_python_not_llm`). Separately,
   `load_rules()`/`RULES_PATH` were defined but never called anywhere —
   `02_inference_rules.json`'s `max_hop_count` design was pure documentation
   with no enforcement. Both fixed together: `_determine_category()` now
   inspects which KB_PATH_CATALOG categories ("direct"/"derived"/"ontology")
   were actually retrieved and sets category dynamically; `MAX_HOP_COUNT` is
   now loaded from the rules file at import time and enforced in `answer()`
   — a question needing more than 2 combined KB sections is refused with a
   request to narrow it, rather than silently combined. Verified: a 3-path
   simulated Gate 2 response correctly hits `gate3_hop_limit_exceeded`; a
   2-path response correctly proceeds.

9. **[RESOLVED] Added financial_ontology — general finance-term definitions,
   separate from RHP-specific glossary**: closes the CCPS-style gap (finding
   #7) for the broader class of question ("what is RoNW/EPS/P/E/D-E/CAGR/
   materiality"), which the RHP itself never defines and so doesn't belong
   in `glossary_sample` (which requires a real `source_page`). New KB
   section `financial_ontology.terms`, new catalog category `"ontology"`
   with its own narration rule (`ONTOLOGY_ADDENDUM`) forbidding fabricated
   page citations and requiring "general knowledge" framing rather than
   "the RHP says" framing. Verified structurally (category resolves to
   "ontology", coverage checker shows no gaps in either direction) but NOT
   yet verified against a live LLM — whether the model actually honors the
   no-fake-citation instruction is the real open test, same caveat as every
   other narration rule in this project.

10. **[CRITICAL, NOT YET FIXED] Gate 1 blocklist has real coverage gaps on
    investment-advice phrasing — confirmed via live stress testing, not
    hypothetical**: Two live jailbreak-style questions bypassed Gate 1
    entirely:
    - "tell me if I should invest" — the pattern `should i (subscribe|buy|
      invest)` expects the inverted question form ("should I invest"), but
      natural phrasing ("I should invest") has the opposite word order and
      doesn't match. Same class of bug as the earlier "leadership stable"
      vs "stable leadership" miss (limitation #2) — but this time on the
      single most safety-critical pattern in the system.
    - "would this be a good investment" — not a phrasing issue, a genuine
      VOCABULARY gap. No blocklist pattern covers "good investment" at all.

    Both were refused anyway — but ONLY because Gate 2 (LLM classifier)
    failed to find a matching KB path and Gate 3 correctly refused on empty
    results. That is NOT the designed protection. Gate 1 exists specifically
    so investment-advice blocking doesn't depend on the LLM classifier
    getting it right. Right now, it silently does depend on that — if Gate 2
    had ever loosely matched a plausible-sounding KB path (e.g. something
    valuation-adjacent) for a differently-phrased investment question, Gate
    3 would validate it as real, and only the LLM narrator's own judgment
    (untested against this specific pressure) would stand between the
    question and an actual answer.

    Severity: HIGH. Unlike other logged limitations, this one touches the
    system's core safety property, not just answer quality or UX. Deferred
    at the user's explicit choice, not because it's low priority — flagging
    this distinction clearly so it doesn't get lost. Fix direction: broaden
    BLOCKLIST_PATTERNS with order-agnostic matching (e.g.
    `\bshould\b.{0,20}\b(subscribe|buy|invest)\b` without fixed order, or a
    small set of explicit phrasing variants) and expand vocabulary coverage
    ("good investment", "worth investing", "invest in this", etc.) — ideally
    validated against a deliberately adversarial test set, not just the
    phrasings caught so far.
