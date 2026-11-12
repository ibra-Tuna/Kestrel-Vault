"""Password strength estimation and generation. No Qt imports."""
from __future__ import annotations

import math
import secrets
import string
from typing import NamedTuple

try:  # optional, much better than any hand-rolled heuristic
    from zxcvbn import zxcvbn as _zxcvbn
except Exception:  # pragma: no cover - depends on environment
    _zxcvbn = None

HAVE_ZXCVBN = _zxcvbn is not None

SYMBOLS = "!@#$%^&*()_+-=[]{}<>?"
AMBIGUOUS = "O0Il1"

LABELS = ["Very weak", "Weak", "Fair", "Strong", "Excellent"]
COLORS = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#16a34a"]
MIN_MASTER_LENGTH = 10
MIN_MASTER_SCORE = 2


class Strength(NamedTuple):
    score: int      # -1 for empty, otherwise 0..4
    label: str
    color: str
    bits: float
    hint: str

    @property
    def pct(self) -> int:
        return 0 if self.score < 0 else (self.score + 1) * 20


EMPTY = Strength(-1, "", "#888888", 0.0, "")

_COMMON = (
    "password", "passw0rd", "qwerty", "qwertz", "azerty", "asdfgh", "zxcvbn", "qazwsx",
    "letmein", "welcome", "admin", "login", "iloveyou", "monkey", "dragon", "football",
    "baseball", "master", "sunshine", "princess", "trustno1", "shadow", "superman",
    "batman", "abc123", "123456", "654321", "111111", "000000", "abcdef", "changeme",
)
_LEET = str.maketrans({"@": "a", "4": "a", "3": "e", "1": "i", "!": "i", "|": "l",
                       "0": "o", "5": "s", "$": "s", "7": "t", "+": "t"})


def _pool_size(pw: str) -> int:
    pool = 0
    if any(c.islower() for c in pw): pool += 26
    if any(c.isupper() for c in pw): pool += 26
    if any(c.isdigit() for c in pw): pool += 10
    if any(c in string.punctuation or c == " " for c in pw): pool += 33
    if any(ord(c) > 127 for c in pw): pool += 64
    return max(pool, 1)


def _fallback_bits(pw: str) -> tuple[float, str]:
    """Conservative estimate used when zxcvbn is not installed."""
    norm = pw.lower().translate(_LEET)
    used = [False] * len(pw)
    bits = 0.0
    penalised = False
    for word in _COMMON:
        i = norm.find(word)
        while i != -1:
            if not any(used[i:i + len(word)]):
                for k in range(i, i + len(word)):
                    used[k] = True
                bits += 10.0
                penalised = True
            i = norm.find(word, i + 1)
    rest = [c for c, u in zip(pw, used) if not u]
    eff = 0.0
    prev = None
    prev_delta = None
    for c in rest:
        if prev is None:
            eff += 1.0
        else:
            delta = ord(c) - ord(prev)
            if delta == 0 or (abs(delta) == 1 and delta == prev_delta):
                eff += 0.15  # repeats and straight runs add almost nothing
                penalised = True
            else:
                eff += 1.0
            prev_delta = delta
        prev = c
    bits += eff * math.log2(_pool_size(pw))
    hint = "Avoid common words and predictable patterns." if penalised else (
        "Use a longer passphrase." if len(pw) < 12 else "")
    return bits, hint


def estimate(pw: str) -> Strength:
    if not pw:
        return EMPTY
    bits: float | None = None
    hint = ""
    if _zxcvbn is not None:
        try:
            r = _zxcvbn(pw[:72])
            bits = math.log2(max(float(r["guesses"]), 1.0))
            fb = r.get("feedback") or {}
            hint = fb.get("warning") or (fb.get("suggestions") or [""])[0]
        except Exception:
            bits = None
    if bits is None:
        bits, hint = _fallback_bits(pw)
    if len(pw) < 8:
        bits = min(bits, 29.0)
    score = 0 if bits < 30 else 1 if bits < 45 else 2 if bits < 60 else 3 if bits < 75 else 4
    return Strength(score, LABELS[score], COLORS[score], bits, hint)


def check_master_password(pw: str) -> str | None:
    """Return a human-readable reason the password is unacceptable, or None."""
    if len(pw) < MIN_MASTER_LENGTH:
        return f"Use at least {MIN_MASTER_LENGTH} characters."
    s = estimate(pw)
    if s.score < MIN_MASTER_SCORE:
        return s.hint or "That password is too easy to guess."
    return None


# ---------------------------------------------------------------- generation
def build_classes(upper: bool, lower: bool, digits: bool, symbols: bool,
                  exclude_ambiguous: bool = False) -> list[str]:
    skip = AMBIGUOUS if exclude_ambiguous else ""
    out: list[str] = []
    if upper: out.append("".join(c for c in string.ascii_uppercase if c not in skip))
    if lower: out.append("".join(c for c in string.ascii_lowercase if c not in skip))
    if digits: out.append("".join(c for c in string.digits if c not in skip))
    if symbols: out.append(SYMBOLS)
    return out


def generate_password(length: int, classes: list[str]) -> str:
    classes = [c for c in classes if c]
    if not classes:
        return ""
    if length < len(classes):
        return "".join(secrets.choice(c) for c in classes)[:length]
    chosen = [secrets.choice(c) for c in classes]
    pool = "".join(classes)
    chosen += [secrets.choice(pool) for _ in range(length - len(chosen))]
    secrets.SystemRandom().shuffle(chosen)
    return "".join(chosen)


# ---------------------------------------------------------------- word list
_WORDS: list[str] | None = None


def _parse_wordlist_text(text: str) -> list[str]:
    """Parse a diceware wordlist file: lines look like '11111<TAB>word'."""
    words: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word = line.split()[-1]          # last whitespace-separated token
        if word:
            words.append(word)
    return words


def _load_wordlist_from_package() -> list[str] | None:
    """Return words from a bundled/installed en_eff list, or None if not found."""
    from pathlib import Path

    # 1) Bundled copy next to this module (also works when frozen).
    try:
        local = Path(__file__).resolve().parent / "assets" / "wordlist_en_eff.txt"
        if local.is_file():
            return _parse_wordlist_text(local.read_text(encoding="utf-8"))
    except Exception:
        pass

    # 2) importlib.resources — the correct way to read package data (Py 3.9+).
    try:
        import importlib.resources as resources
        ref = resources.files("diceware") / "wordlists" / "wordlist_en_eff.txt"
        if ref.is_file():
            return _parse_wordlist_text(ref.read_text(encoding="utf-8"))
    except Exception:
        pass

    # 3) Locate the package directory on disk and read the file directly.
    try:
        import diceware
        p = (Path(diceware.__file__).resolve().parent
             / "wordlists" / "wordlist_en_eff.txt")
        if p.is_file():
            return _parse_wordlist_text(p.read_text(encoding="utf-8"))
    except Exception:
        pass

    return None


def wordlist() -> list[str]:
    """Load the diceware word list on first use."""
    global _WORDS
    if _WORDS is not None:
        return _WORDS

    words = _load_wordlist_from_package()

    if words is None:
        # Last resort: the old, private diceware API (version-dependent).
        try:
            from diceware.wordlist import get_wordlist
        except ImportError as exc:
            import sys
            raise RuntimeError(
                "The 'diceware' package is not importable by the Python "
                f"running this script ({sys.executable}). Install it there:\n"
                f'  "{sys.executable}" -m pip install diceware'
            ) from exc
        try:
            wl = get_wordlist("en_eff")
        except Exception as exc:
            raise RuntimeError(
                f"Could not load the diceware word list: {exc!r}"
            ) from exc
        words = list(wl.values()) if hasattr(wl, "values") else list(wl)
        words = [w for w in words if isinstance(w, str) and w]

    if not words:
        raise RuntimeError("The diceware word list is empty.")

    _WORDS = words
    return _WORDS


def generate_passphrase(words: int, sep: str, capitalize: bool, number: bool,
                        source: list[str] | None = None) -> str:
    pool = source if source is not None else wordlist()
    picked = [secrets.choice(pool) for _ in range(words)]
    if capitalize:
        picked = [w.capitalize() for w in picked]
    phrase = sep.join(picked)
    if number:
        phrase += sep + str(secrets.randbelow(100))
    return phrase


def entropy_random(pool_size: int, length: int) -> float:
    return length * math.log2(pool_size) if pool_size > 1 else 0.0


def entropy_passphrase(words: int, list_size: int, number: bool) -> float:
    bits = words * math.log2(list_size)
    return bits + (math.log2(100) if number else 0.0)


def entropy_label(bits: float) -> tuple[str, str]:
    if bits < 50: return f"{bits:.0f} bits · Weak", COLORS[0]
    if bits < 70: return f"{bits:.0f} bits · Fair", COLORS[2]
    if bits < 90: return f"{bits:.0f} bits · Strong", COLORS[3]
    return f"{bits:.0f} bits · Excellent", COLORS[4]