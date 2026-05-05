"""Signal collectors — one module per user-facing feature.

Each collector returns a per-driver dict of normalized facts.  The
service layer merges them and feeds the result to the engine.
"""
