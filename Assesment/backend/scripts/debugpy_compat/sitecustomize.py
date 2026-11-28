"""Lets debugpy start "python -m <module>" child processes on Python 3.14.

debugpy (2026.6) finds the module of a "-m" child with pkgutil.get_loader,
which Python 3.14 removed; it then reports "No module named ..." and the child
does not run. Python imports this file at startup when its directory is on
PYTHONPATH, which only the debug overlay of the dev stack does
(deploy/compose.debug.yaml). It puts back the function as it was. Delete this
directory once debugpy no longer needs it.
"""

import importlib.util
import pkgutil

if not hasattr(pkgutil, "get_loader"):

    def get_loader(module_or_name: object) -> object:
        name = getattr(module_or_name, "__name__", module_or_name)
        spec = importlib.util.find_spec(str(name))
        return spec.loader if spec is not None else None

    setattr(pkgutil, "get_loader", get_loader)  # noqa: B010 (the module has no such name)
