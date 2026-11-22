"""Code shared by the API, the orchestrator and the scanner.

Nothing in this package may import ``ndm.db`` or ``ndm.api``: the scanner
imports it and has no database or web dependencies (C-3).
"""
