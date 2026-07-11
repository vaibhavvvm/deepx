"""Pre-flight secret scan for cloud-bound payloads.

When (and only when) the active provider is a *cloud* provider, text about to
leave the machine is scanned for secret-shaped content and redacted. For local
providers this is skipped entirely -- nothing leaves the machine -- and that
distinction is surfaced in the UI, not just here in a comment (blueprint 7.5).

The scan combines high-signal regexes (known key formats, PEM blocks, ``.env``
assignments) with a Shannon-entropy heuristic for opaque high-entropy tokens.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

REDACTION = "[REDACTED-SECRET]"

# High-signal patterns: the capture group (or whole match) is what gets redacted.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    (
        "pem_private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "bearer_token",
        re.compile(r"(?i)\b(?:authorization|bearer)\b[:=]?\s*[\"']?([A-Za-z0-9._\-]{20,})"),
    ),
    (
        "env_assignment",
        re.compile(
            r"(?im)^\s*[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL)[A-Z0-9_]*\s*=\s*[\"']?([^\s\"'#]+)"
        ),
    ),
]

# Tokens shorter than this are never entropy-flagged (too many false positives).
_ENTROPY_MIN_LEN = 24
_ENTROPY_THRESHOLD = 4.0  # bits/char; opaque base64/hex secrets clear this.
_TOKEN_RE = re.compile(rf"[A-Za-z0-9+/=_\-]{{{_ENTROPY_MIN_LEN},}}")


@dataclass
class Finding:
    kind: str
    preview: str  # first/last few chars only, never the full secret


@dataclass
class ScanResult:
    redacted_text: str
    findings: list[Finding]

    @property
    def had_secrets(self) -> bool:
        return bool(self.findings)


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _preview(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:3]}…{secret[-2:]}"


def scan(text: str, *, entropy: bool = True) -> ScanResult:
    """Redact secret-shaped substrings from ``text``.

    Returns the redacted text plus a list of findings (with only short,
    non-reversible previews) suitable for showing the user.
    """
    findings: list[Finding] = []
    redacted = text

    for kind, pattern in _PATTERNS:

        def _sub(m: re.Match[str], kind: str = kind) -> str:
            # Redact the capture group if the pattern has one, else the match.
            secret = m.group(1) if m.groups() else m.group(0)
            findings.append(Finding(kind=kind, preview=_preview(secret)))
            return m.group(0).replace(secret, REDACTION)

        redacted = pattern.sub(_sub, redacted)

    if entropy:

        def _ent_sub(m: re.Match[str]) -> str:
            token = m.group(0)
            if REDACTION in token:
                return token
            if _shannon_entropy(token) >= _ENTROPY_THRESHOLD:
                findings.append(Finding(kind="high_entropy", preview=_preview(token)))
                return REDACTION
            return token

        redacted = _TOKEN_RE.sub(_ent_sub, redacted)

    return ScanResult(redacted_text=redacted, findings=findings)
