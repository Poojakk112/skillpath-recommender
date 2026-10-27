"""Deterministic, zero-padded, immutable IDs.

LRN-000001, CRS-000001, SKL-000001, CNT-000001, ATT-<learner-id>-000001,
PATH-<year>-000001. Other tables use <PREFIX>-<8 digits>.
"""


def learner(n: int) -> str:
    return f"LRN-{n:06d}"


def course(n: int) -> str:
    return f"CRS-{n:06d}"


def skill(n: int) -> str:
    return f"SKL-{n:06d}"


def content(n: int) -> str:
    return f"CNT-{n:06d}"


def attempt(learner_id: str, n: int) -> str:
    """Attempt counter is per learner and follows the learner's chronological order."""
    return f"ATT-{learner_id}-{n:06d}"


def path(year: int, n: int) -> str:
    """Year = year the path was created; counter is global across years."""
    return f"PATH-{year}-{n:06d}"


def seq(prefix: str, n: int) -> str:
    return f"{prefix}-{n:08d}"


def number(id_: str) -> int:
    """Trailing counter of any ID, e.g. 'ATT-LRN-000001-000042' -> 42."""
    return int(id_.rsplit("-", 1)[1])


PATTERNS = {
    "learner": r"^LRN-\d{6}$",
    "course": r"^CRS-\d{6}$",
    "skill": r"^SKL-\d{6}$",
    "content": r"^CNT-\d{6}$",
    "attempt": r"^ATT-LRN-\d{6}-\d{6}$",
    "path": r"^PATH-\d{4}-\d{6}$",
    "path_event": r"^PEV-\d{8}$",
    "result": r"^ASR-LRN-\d{6}-\d{6}-[12]$",
    "snapshot": r"^MSN-\d{8}$",
    "recommendation": r"^REC-\d{8}$",
    "decision": r"^DEC-\d{8}$",
}
