"""The scanner worker: the only component that sends packets to targets.

It has no database and no web dependencies. Nothing in this package may import
``ndm.db`` or ``ndm.api`` (C-3); it uses ``ndm.core`` and the standard library.
"""
