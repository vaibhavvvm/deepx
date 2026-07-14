"""Surgical code editing tools: replace_in_file and read_file_outline.

MiMo-Code insight: models that rewrite entire files > 200 lines will
inevitably truncate or hallucinate the sections outside their active
attention span.  The fix is two complementary tools:

1. **replace_in_file**  — Search-and-replace block editing (like Cursor/Aider).
   The model provides an exact ``search_block`` it found via read_file/grep,
   and a ``replace_block``.  We do a precise string replacement so only the
   targeted lines change and nothing else is touched.

2. **read_file_outline** — Code skeletonisation.  Reads a file but strips all
   function bodies, returning only imports, class names, and signatures.
   Token cost of 1 full read, but gives structural overview of 10 files.
   Prevents context exhaustion when answering "how does X work?" questions.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# 1.  Surgical patch-based editing
# ---------------------------------------------------------------------------

@tool
def replace_in_file(filepath: str, search_block: str, replace_block: str) -> str:
    """Surgically edit a file by replacing an exact block of text.

    Use this instead of write_file when editing an EXISTING file.
    write_file is for creating NEW files only.

    Rules:
    - ``search_block`` MUST be an exact copy of the lines you want to replace,
      including the correct indentation and surrounding context lines so it
      is unique in the file.
    - ``replace_block`` is the new code that replaces it.
    - If ``search_block`` is not found exactly, the edit is refused and you
      must re-read the file to get the correct text.
    - Never use this to rewrite more than 80 lines at once. Break large
      changes into multiple smaller replace_in_file calls.

    Args:
        filepath: Absolute path to the file inside /workspace.
        search_block: The EXACT block of text to find (copy it from read_file output).
        replace_block: The new code to put in its place.

    Returns:
        A confirmation message with the number of lines changed, or an error.
    """
    path = Path(filepath)
    if not path.exists():
        return f"Error: file not found: {filepath}"

    original = path.read_text(encoding="utf-8")

    if search_block not in original:
        # Try stripping trailing whitespace on each line (common model mistake)
        normalised_orig = "\n".join(line.rstrip() for line in original.splitlines())
        normalised_search = "\n".join(line.rstrip() for line in search_block.splitlines())
        if normalised_search in normalised_orig:
            # Whitespace-normalised match — apply on normalised version
            new_content = normalised_orig.replace(normalised_search, replace_block, 1)
            path.write_text(new_content, encoding="utf-8")
            orig_lines = len(search_block.splitlines())
            new_lines = len(replace_block.splitlines())
            return (
                f"✓ Applied (whitespace-normalised match). "
                f"Replaced {orig_lines} lines with {new_lines} lines in {filepath}."
            )
        return (
            f"Error: search_block not found in {filepath}.\n"
            "You must copy the exact text from a recent read_file call. "
            "Re-read the file and try again."
        )

    count = original.count(search_block)
    if count > 1:
        return (
            f"Error: search_block appears {count} times in {filepath}. "
            "Provide more surrounding context lines to make it unique."
        )

    new_content = original.replace(search_block, replace_block, 1)
    path.write_text(new_content, encoding="utf-8")

    orig_lines = len(search_block.splitlines())
    new_lines = len(replace_block.splitlines())
    return (
        f"✓ Applied. Replaced {orig_lines} lines with {new_lines} lines in {filepath}. "
        "Run your tests to verify."
    )


# ---------------------------------------------------------------------------
# 2.  Code skeletonisation — context-efficient structural overview
# ---------------------------------------------------------------------------

# Regex patterns for extracting structure without bodies
_PY_DEF   = re.compile(r"^(\s*(?:async\s+)?def\s+\w+\s*\(.*?\)\s*(?:->\s*[^:]+)?\s*):", re.MULTILINE)
_PY_CLASS = re.compile(r"^(\s*class\s+\w+[^:]*?):", re.MULTILINE)
_JS_FUNC  = re.compile(
    r"^(\s*(?:export\s+)?(?:async\s+)?(?:function\s+\w+\s*\([^)]*\)|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>))",
    re.MULTILINE,
)


def _python_outline(source: str) -> str:
    lines = source.splitlines()
    kept: list[str] = []
    skip_until_dedent: int | None = None  # indentation level to skip body at

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Always keep imports
        if stripped.startswith(("import ", "from ")):
            kept.append(line)
            i += 1
            continue

        # Class / def signatures → keep signature line, skip body
        if stripped.startswith(("class ", "def ", "async def ")):
            kept.append(line)
            # peek ahead: if next non-empty line is indented more, skip body
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                body_indent = len(lines[j]) - len(lines[j].lstrip())
                sig_indent = len(line) - len(stripped)
                if body_indent > sig_indent:
                    skip_until_dedent = sig_indent
            i += 1
            continue

        if skip_until_dedent is not None:
            current_indent = len(line) - len(stripped) if stripped else 999
            if stripped and current_indent <= skip_until_dedent:
                skip_until_dedent = None
                # Don't skip this line — it's back at parent scope
                kept.append(line)
            # else: silently skip body lines
            i += 1
            continue

        kept.append(line)
        i += 1

    return "\n".join(kept)


def _generic_outline(source: str) -> str:
    """For JS/TS/other: strip lines inside braces after function/class headers."""
    lines = source.splitlines()
    kept: list[str] = []
    brace_depth = 0
    in_signature = False

    for line in lines:
        stripped = line.strip()
        opens = line.count("{")
        closes = line.count("}")

        # Detect function/class-like declarations
        is_decl = bool(
            _JS_FUNC.match(line)
            or stripped.startswith(("class ", "interface ", "type ", "export "))
        )

        if is_decl and "{" in line:
            kept.append(line)
            brace_depth += opens - closes
            continue

        if brace_depth == 0:
            kept.append(line)
        else:
            # Inside a body — only keep the closing brace at depth 0
            brace_depth += opens - closes
            if brace_depth == 0:
                kept.append("  // ... body omitted ...")
                kept.append(line)

    return "\n".join(kept)


@tool
def read_file_outline(filepath: str) -> str:
    """Read a file's STRUCTURE only — imports, class names, and function signatures.

    Use this instead of read_file when you need to understand the architecture
    of a file without reading its full implementation.  Token cost is ~10× lower
    than read_file.

    Ideal for:
    - Answering "how does X module work?" without filling the context window.
    - Finding which function to grep/read_file next.
    - Getting the structure of large files (> 300 lines) before surgical edits.

    Args:
        filepath: Absolute path to the file (e.g. /workspace/src/auth.py).

    Returns:
        The file's structural skeleton with bodies stripped.
    """
    path = Path(filepath)
    if not path.exists():
        return f"Error: file not found: {filepath}"

    source = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    try:
        if suffix == ".py":
            outline = _python_outline(source)
        else:
            outline = _generic_outline(source)
    except Exception as exc:
        return f"Error generating outline: {exc}\nFull source:\n{source[:3000]}"

    header = f"# Outline of {filepath} ({source.count(chr(10))+1} total lines)\n\n"
    return header + (outline or "[file appears empty]")
