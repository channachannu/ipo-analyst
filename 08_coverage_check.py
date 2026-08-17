"""
KB & Classifier Coverage Checker
================================

This exists because of one real bug: "How does the company plan to use IPO
proceeds?" was wrongly refused. Root cause had TWO independent parts:
  1. EXTRACTION GAP — the schema didn't even have a use_of_proceeds field
  2. ROUTING GAP — no classifier pattern pointed at it even after adding it

Fixing that one question doesn't prevent the next one. This script checks
BOTH gap types systematically across the whole schema, so gaps get caught
before a user hits them as a false refusal.

Two checks:
  A. SCHEMA -> KB coverage: every field defined in 01_ontology_schema.json —
     does the populated KB (03_...json) have a non-null value or an explicit
     PENDING_DISCLOSURE/NOT_APPLICABLE status for it? A field that's just
     silently missing is an extraction gap.
  B. KB -> CATALOG coverage: every top-level path that actually has data
     in the KB — does at least one entry in query_engine.KB_PATH_CATALOG
     (Gate 2's enumerated list) reference it? A populated KB path missing
     from the catalog means Gate 2 can never choose it, no matter how well
     the question is phrased or how good the LLM is — the LLM can only pick
     from what's in the catalog, same blind spot the old PATTERNS table had.

This is deliberately NOT a semantic check of the catalog descriptions
themselves — it won't tell you a description is misleading or too narrow,
only whether a path is listed at all. That still needs human review. But it
WILL catch "nothing in the catalog points here," which is the same failure
class that caused the original use_of_proceeds bug — just one layer moved,
from regex patterns to catalog entries, after the Gate 2/3 redesign.
"""

import json
import importlib.util
import sys


def load_json(path):
    with open(path) as f:
        return json.load(f)


def flatten_schema_paths(node, prefix=""):
    """Walk the ontology schema and return every leaf field's dotted path.
    A 'leaf' is a dict containing a 'disclosure_status' key (our convention
    for a fact node) OR an empty list (a collection placeholder)."""
    paths = []
    if isinstance(node, dict):
        if "disclosure_status" in node:
            paths.append(prefix.rstrip("."))
        else:
            for key, val in node.items():
                if key.startswith("_"):
                    continue  # skip _schema_notes and similar meta keys
                paths.append(*flatten_schema_paths(val, f"{prefix}{key}."))  if False else None
                paths.extend(flatten_schema_paths(val, f"{prefix}{key}."))
    elif isinstance(node, list) and prefix:
        paths.append(prefix.rstrip("."))
    return paths


def get_nested(d, dotted_path):
    node = d
    for part in dotted_path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def check_schema_to_kb_coverage(schema_path="01_ontology_schema.json", kb_path="03_milky_mist_kb_sample.json"):
    schema = load_json(schema_path)
    kb = load_json(kb_path)

    schema_paths = []
    for top_key, top_val in schema.items():
        if top_key.startswith("_"):
            continue
        schema_paths.extend(flatten_schema_paths(top_val, f"{top_key}."))

    gaps = []
    covered = []
    for path in schema_paths:
        kb_node = get_nested(kb, path)
        if kb_node is None:
            gaps.append((path, "MISSING — not in KB at all"))
        elif isinstance(kb_node, dict) and kb_node.get("value") is None and kb_node.get("disclosure_status") is None:
            gaps.append((path, "NULL with no disclosure_status — ambiguous, should be PENDING_DISCLOSURE or NOT_APPLICABLE explicitly"))
        else:
            covered.append(path)

    return covered, gaps


def check_kb_to_catalog_coverage(kb_path="03_milky_mist_kb_sample.json", engine_module_path="05_query_engine.py"):
    """Replaces the old check_kb_to_classifier_coverage (which checked the
    now-removed PATTERNS table). Checks Gate 2's KB_PATH_CATALOG instead —
    same purpose, updated for the 3-gate LLM-classification redesign."""
    kb = load_json(kb_path)
    top_level_paths = [k for k in kb.keys() if not k.startswith("_")]

    spec = importlib.util.spec_from_file_location("query_engine", engine_module_path)
    qe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qe)

    catalog_prefixes = set()
    for entry in qe.KB_PATH_CATALOG:
        catalog_prefixes.add(entry["path"].split(".")[0])

    gaps = [p for p in top_level_paths if p not in catalog_prefixes]
    covered = [p for p in top_level_paths if p in catalog_prefixes]
    return covered, gaps


if __name__ == "__main__":
    print("=" * 70)
    print("A. SCHEMA -> KB COVERAGE (extraction gaps)")
    print("=" * 70)
    covered, gaps = check_schema_to_kb_coverage()
    print(f"Covered: {len(covered)} fields")
    print(f"Gaps: {len(gaps)} fields\n")
    for path, reason in gaps:
        print(f"  ⚠ {path}\n    -> {reason}")

    print()
    print("=" * 70)
    print("B. KB -> CATALOG COVERAGE (Gate 2 routing gaps)")
    print("=" * 70)
    covered, gaps = check_kb_to_catalog_coverage()
    print(f"Covered top-level sections: {covered}")
    print(f"Gaps: {gaps if gaps else 'none'}")
    if gaps:
        print("\n  These KB sections have data but NO entry in KB_PATH_CATALOG —")
        print("  Gate 2 (the LLM classifier) cannot pick them regardless of how")
        print("  well the question is phrased. Add a catalog entry to fix.")
