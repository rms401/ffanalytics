"""Exact re-implementations of the R semantics the port depends on.

Everything in here exists because a naive Python/NumPy equivalent would give a
*slightly* different answer than R: rank flavours, quantile type, the MAD
constant, the seeded RNG, first-match lookup in vectors with duplicate names.
Keeping them in one place makes those choices reviewable.
"""

from . import rng, stats

__all__ = ["rng", "stats"]
