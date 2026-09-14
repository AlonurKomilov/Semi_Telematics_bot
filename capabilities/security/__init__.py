"""Deprecated import path. The package moved to ``system.security`` —
the system layer, which sits above the customer layers instead of
inside them. Each module here is an alias to the SAME object at the
new path (``sys.modules`` identity, never a copy: a copy would carry
its own caches, and a hold cleared through one name would still be
cached under the other). Delete this package once nothing imports it —
``tests/test_layer_boundaries.py`` says who still does.
"""
