"""Flatten a JSON object to leaf paths (shared by the report and the extraction gate).

Path grammar: object keys join with ``.``; array indices are ``[i]``. So
``{"claimant": {"nino": "X"}, "children": [{"full_name": "Y"}]}`` flattens to
``claimant.nino`` and ``children[0].full_name``. Leaf values are the scalars (str/int/float/
bool/None); empty arrays/objects are dropped. Deterministic key order.
"""

from __future__ import annotations

from typing import Any


def flatten_leaves(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Return ``{leaf_path: scalar_value}`` for ``obj``."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key in obj:
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten_leaves(obj[key], child))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            out.update(flatten_leaves(item, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def resolve_path(obj: Any, path: str) -> Any:
    """Resolve a leaf path (``a.b[0].c``) against ``obj``; return ``None`` if any hop is absent."""
    cur = obj
    for token in path.replace("]", "").replace("[", ".").split("."):
        if token == "":
            continue
        if isinstance(cur, dict):
            if token not in cur:
                return None
            cur = cur[token]
        elif isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur
