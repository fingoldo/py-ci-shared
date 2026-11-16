"""Canary of workflow_installs_pinned (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "workflow_installs_pinned",
    lambda d: _gate("workflow_installs_pinned").assert_workflow_installs_pinned(d / ".github" / "workflows"),
    token="seed.yml",
    tokens=("`git clone`", "installs from git"),
    parses=False,
)
