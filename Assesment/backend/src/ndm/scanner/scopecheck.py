"""The scanner's own scope check: the last barrier before a packet is sent
(FR-PRJ-7, architecture section 6.6).

The scanner trusts nothing but what it can check itself. Before the first
probe of a chunk it tests every target against

- the scope carried in the chunk,
- its own table of non-public address space, and
- its own copy of the never-scan list, from its own environment.

It fails closed: a missing scope or a scope that cannot be read rejects the
whole chunk. Nothing is sent for a rejected chunk.
"""

from collections.abc import Sequence

from ndm.core import scope as scope_rules
from ndm.core.messages import Chunk


def check_chunk(
    chunk: Chunk, *, never_scan: Sequence[scope_rules.ScopeEntry], allow_non_public: bool
) -> str | None:
    """Why the chunk must be rejected, or None if every target may be probed."""
    if not chunk.scope:
        return "no_scope"
    if not chunk.targets:
        return "no_targets"
    scope = []
    for start, end in chunk.scope:
        if start > end:
            return "invalid_scope"
        scope.append(scope_rules.ScopeEntry("range", f"{start}-{end}", start, end))

    for address in chunk.targets:
        target = scope_rules.ScopeEntry("address", str(address), address, address)
        try:
            scope_rules.check_target(
                target, scope=scope, never_scan=never_scan, allow_non_public=allow_non_public
            )
        except scope_rules.ScopeError as error:
            return error.code
    return None
