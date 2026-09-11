"""
turn_guard — stop an LLM from speaking for the user.

Some models occasionally fail to stop at the end of their turn and keep going,
writing the *next speaker's* line: a literal `user` (or `Human:`) label followed
by text in the user's voice. Agents then sometimes act on that invented
instruction — in one public report, inside a single generation, all the way to
`git push` (anthropics/claude-code #85215).

Three rules, enforced outside the model (the model cannot be relied on to police itself):
  1. Stop sequences  — put line-start speaker labels in stop_sequences so the fake
                       turn is cut the moment it starts.
  2. Inspection      — if a reply's text still contains a speaker-label line, cut the
                       text there and execute NONE of the tool calls in that reply.
                       Store only the cut version, so the fake line never re-enters
                       the context as if the user had said it.
  3. Roles           — decide who spoke from the API message structure only.
                       Never parse a speaker out of model text.
                       (That rule lives in your loop; see example_anthropic.py.)

Standard library only. Python 3.9+. Model- and vendor-independent.
License: MIT.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# ------------------------------------------------------------ 1. stop sequences
# "prose": chat / role-play / writing. A line starting with "user" never occurs legitimately.
STOP_PROSE = ["\nuser", "\nUser", "\nHuman:", "\nAssistant:"]
# "code": agents that write code. `user = ...` is normal code, so don't stop on it; rule 2 still inspects.
STOP_CODE = ["\nHuman:", "\nAssistant:"]
# Chat-template control strings that other vendors' / local models sometimes leak.
TEMPLATE_TOKENS = ["<|im_start|>", "<|im_end|>", "<|user|>", "<|assistant|>",
                   "<|start_header_id|>", "<|eot_id|>", "<start_of_turn>", "[INST]"]
PROFILES = ("prose", "code")


def stop_sequences(profile: str = "prose", max_n: Optional[int] = None) -> List[str]:
    """Stop sequences for a profile. max_n = the API's limit (OpenAI-style APIs allow 4)."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    base = STOP_PROSE if profile == "prose" else STOP_CODE
    seq = list(base) + [t for t in TEMPLATE_TOKENS if t not in base]
    return seq[:max_n] if max_n else seq


# ------------------------------------------------------------ 2. inspection
_LABELS = r"(?:user|User|USER|human|Human|HUMAN|assistant|Assistant|system|System)"
_CODEISH = re.compile(r"[=(){}\[\];<>`$\\]")
_RULES = [
    ("label_colon", re.compile(rf"^[ \t]*{_LABELS}[ \t]*[:：]")),             # "User: ..." / "Human:"
    ("label_alone", re.compile(rf"^[ \t]*{_LABELS}[ \t]*$")),                 # a line that is just "user"
    ("label_fused", re.compile(r"^[ \t]*(?:user|User|Human)[ \t]*(?=[^\x00-\x7F])")),  # "userすばらしい…" / "user 今日は…"
]
_TEMPLATE_RE = re.compile("|".join(re.escape(t) for t in TEMPLATE_TOKENS))
_FUSED_ASCII = re.compile(r"^[ \t]*user[ A-Za-z]")                           # "usergitに commit", "username hurt…", "user let's stop"
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


@dataclass
class Violation:
    rule: str
    block_index: int
    offset: int
    line: str


@dataclass
class Verdict:
    ok: bool
    blocks: list                                   # what you may store as the assistant message
    violations: List[Violation] = field(default_factory=list)
    dropped_tool_calls: list = field(default_factory=list)
    removed_text: str = ""


def _is_fused_ascii(line: str) -> bool:
    if not _FUSED_ASCII.match(line) or _CODEISH.search(line):
        return False
    return any(ord(c) > 0x7F for c in line) or len(line.split()) >= 3


def scan_text(text: str) -> Optional[Tuple[str, int, str]]:
    """Return (rule, offset, line) for the first fake-turn line, or None.
    Lines inside code fences and quoted lines ("> ...") are ignored.
    Template control strings count anywhere."""
    m = _TEMPLATE_RE.search(text)
    first_tpl = (m.start(), m.group(0)) if m else None
    in_fence, offset, hit = False, 0, None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and not line.lstrip().startswith(">"):
            for name, rx in _RULES:
                if rx.match(line):
                    hit = (name, offset, line[:200])
                    break
            if hit is None and _is_fused_ascii(line):
                hit = ("label_fused_ascii", offset, line[:200])
        if hit:
            break
        offset += len(raw) + 1
    if first_tpl and (hit is None or first_tpl[0] < hit[1]):
        return ("template_token", first_tpl[0], first_tpl[1])
    return hit


def inspect(blocks: list) -> Verdict:
    """Inspect Anthropic-style content blocks (text / tool_use / thinking ...).
    On a hit: keep text up to the fake line, drop everything after it,
    and drop EVERY tool_use block in the reply, wherever it sits."""
    for i, b in enumerate(blocks):
        if b.get("type") != "text":
            continue
        found = scan_text(b.get("text", ""))
        if not found:
            continue
        rule, off, line = found
        kept = [dict(p) for p in blocks[:i] if p.get("type") == "text"]
        head = b["text"][:off].rstrip()
        if head:
            kept.append({"type": "text", "text": head})
        removed = b["text"][off:] + "".join("\n" + x.get("text", "") for x in blocks[i + 1:] if x.get("type") == "text")
        dropped = [dict(x) for x in blocks if x.get("type") == "tool_use"]
        return Verdict(False, kept, [Violation(rule, i, off, line)], dropped, removed)
    return Verdict(True, [dict(b) for b in blocks])
