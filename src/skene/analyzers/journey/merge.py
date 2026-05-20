"""Step 3 — deterministic merge of schema-side and code-side candidates.

No LLM. Pure function so we can hammer it with unit tests.

Rules, in order:
1. Exact ``proposed_id`` match → merge. Evidence is concatenated, the
   higher-confidence candidate's name/description wins.
2. Fuzzy name match (normalize: lowercase, strip punctuation, sort tokens)
   → merge. Same evidence-union behaviour.
3. Otherwise keep both.
"""

from __future__ import annotations

import re

from skene.analyzers.journey.candidate import CandidateMilestone
from skene.analyzers.journey.models import Evidence

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _normalize(name: str) -> str:
    tokens = sorted(t for t in _TOKEN_SPLIT.split(name.lower()) if t)
    return " ".join(tokens)


def _union_evidence(a: list[Evidence], b: list[Evidence]) -> list[Evidence]:
    seen: set[tuple] = set()
    out: list[Evidence] = []
    for ev in [*a, *b]:
        key = (ev.source, ev.path, ev.table, ev.reason)
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def _merge_pair(a: CandidateMilestone, b: CandidateMilestone) -> CandidateMilestone:
    primary, secondary = (a, b) if a.confidence >= b.confidence else (b, a)
    return CandidateMilestone(
        proposed_id=primary.proposed_id,
        name=primary.name,
        description=primary.description,
        evidence=_union_evidence(primary.evidence, secondary.evidence),
        tracked_event=primary.tracked_event or secondary.tracked_event,
        # Average confidence — both sources seeing the same milestone is a
        # boost, not a drop, but we don't want to blindly use the higher one.
        confidence=round((primary.confidence + secondary.confidence) / 2, 4),
        stage_id=primary.stage_id or secondary.stage_id,
    )


def merge_candidates(
    schema_candidates: list[CandidateMilestone],
    code_candidates: list[CandidateMilestone],
) -> list[CandidateMilestone]:
    """Deduplicate the two streams into a single list."""
    merged: list[CandidateMilestone] = []
    by_id: dict[str, int] = {}
    by_norm_name: dict[str, int] = {}

    for cm in [*schema_candidates, *code_candidates]:
        existing_idx: int | None = by_id.get(cm.proposed_id)
        if existing_idx is None:
            existing_idx = by_norm_name.get(_normalize(cm.name))
        if existing_idx is None:
            merged.append(cm)
            idx = len(merged) - 1
            by_id[cm.proposed_id] = idx
            by_norm_name[_normalize(cm.name)] = idx
            continue

        combined = _merge_pair(merged[existing_idx], cm)
        merged[existing_idx] = combined
        # Index under both ids/names in case they differed.
        by_id[cm.proposed_id] = existing_idx
        by_id[combined.proposed_id] = existing_idx
        by_norm_name[_normalize(cm.name)] = existing_idx
        by_norm_name[_normalize(combined.name)] = existing_idx
    return merged
