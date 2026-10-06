import re
from typing import List, Optional


def split_local_names(value: Optional[str]) -> List[str]:
    """Comma-separated local names as a clean list, without blanks or repeats."""
    out: List[str] = []
    for n in (value or "").split(","):
        n = n.strip()
        if n and n.lower() not in (o.lower() for o in out):
            out.append(n)
    return out


def compose_name(name: str, local_names: Optional[str], previous_local_names: Optional[str] = None) -> str:
    """'Spring Leaves' + 'patta' -> 'Spring Leaves / Patta'. Safe to run again: old local-name suffixes are removed first."""
    parts = [p.strip() for p in re.split(r"\s+/\s+", (name or "").strip()) if p.strip()]
    current = split_local_names(local_names)
    known = {n.lower() for n in current + split_local_names(previous_local_names)}
    while len(parts) > 1 and parts[-1].lower() in known:
        parts.pop()
    return " / ".join([" / ".join(parts)] + [n[:1].upper() + n[1:] for n in current])
