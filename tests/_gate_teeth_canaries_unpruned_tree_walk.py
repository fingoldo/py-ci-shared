"""Canary of unpruned_tree_walk (loaded by tests/_gate_teeth_canaries_more.py: the glob covers this name)."""

from __future__ import annotations

from _gate_teeth_canaries import Canary, _gate

CANARY = Canary(
    "unpruned_tree_walk",
    lambda d: _gate("unpruned_tree_walk").assert_unpruned_tree_walk(d, use_git=False),
    tokens=("`find_sources`", "`repo_root`"),
)
