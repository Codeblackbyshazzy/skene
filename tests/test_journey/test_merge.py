"""Tests for the deterministic merge step (Step 3)."""

from __future__ import annotations

from skene.analyzers.journey.candidate import CandidateMilestone
from skene.analyzers.journey.merge import merge_candidates
from skene.analyzers.journey.models import Evidence


def _code(pid: str, name: str, path: str, confidence: float = 0.8) -> CandidateMilestone:
    return CandidateMilestone(
        proposed_id=pid,
        name=name,
        description=name,
        evidence=[Evidence(source="code", path=path, reason="found")],
        confidence=confidence,
    )


def _db(pid: str, name: str, table: str, confidence: float = 0.8) -> CandidateMilestone:
    return CandidateMilestone(
        proposed_id=pid,
        name=name,
        description=name,
        evidence=[Evidence(source="db", table=table, reason="row")],
        confidence=confidence,
    )


def test_exact_id_match_merges_evidence():
    schema = [_db("account_created", "Account Created", "users", 0.9)]
    code = [_code("account_created", "Account Created", "src/api/signup.ts", 0.8)]
    out = merge_candidates(schema, code)
    assert len(out) == 1
    sources = {ev.source.value for ev in out[0].evidence}
    assert sources == {"db", "code"}


def test_fuzzy_name_match_merges():
    schema = [_db("acct_created", "Account  Created!", "users")]
    code = [_code("account_created", "account created", "src/api/signup.ts")]
    out = merge_candidates(schema, code)
    assert len(out) == 1
    assert len(out[0].evidence) == 2


def test_no_match_keeps_both():
    schema = [_db("account_created", "Account Created", "users")]
    code = [_code("landing_view", "Landing Page View", "src/pages/index.tsx")]
    out = merge_candidates(schema, code)
    assert len(out) == 2
    ids = {cm.proposed_id for cm in out}
    assert ids == {"account_created", "landing_view"}


def test_higher_confidence_wins_name():
    schema = [_db("a", "Signup Completed", "users", confidence=0.6)]
    code = [_code("a", "Account Created", "src/api/signup.ts", confidence=0.95)]
    out = merge_candidates(schema, code)
    assert len(out) == 1
    assert out[0].name == "Account Created"


def test_evidence_deduplicates_within_merge():
    e = Evidence(source="code", path="src/api/signup.ts", reason="found")
    a = CandidateMilestone(
        proposed_id="x",
        name="X",
        description="X",
        evidence=[e],
        confidence=0.8,
    )
    b = CandidateMilestone(
        proposed_id="x",
        name="X",
        description="X",
        evidence=[e],
        confidence=0.8,
    )
    out = merge_candidates([a], [b])
    assert len(out) == 1
    assert len(out[0].evidence) == 1
