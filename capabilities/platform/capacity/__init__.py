"""Deprecated import path. The package moved to ``system.capacity`` — the system
layer, above the customer layers instead of inside them.

This package object is deliberately NOT swapped for the new one: a
swapped parent resolves submodule imports through the NEW directory and
builds a second module under the old name — a copy with its own state.
Instead every module file here aliases ITSELF to the same object at the
new path (``sys.modules`` identity), which is what the identity test
asserts. Delete once nothing imports it — ``tests/test_layer_boundaries.py``
says who still does."""
