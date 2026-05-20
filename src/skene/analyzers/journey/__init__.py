"""Agentic journey-map generation pipeline.

Two parallel agents (schema + code) emit candidate milestones, which are
merged, classified into seven canonical stages, and assembled into a
validated :class:`skene.analyzers.journey.models.Journey`.
"""
