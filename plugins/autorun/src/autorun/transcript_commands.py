"""Bounded transcript scanning for fallback autorun prompt commands."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Container, Iterable
from dataclasses import dataclass
from typing import Any

from .core import canonicalize_command_prompt

DEFAULT_TRANSCRIPT_COMMAND_SCAN_BYTES = 64 * 1024


@dataclass(frozen=True)
class TranscriptCommand:
    """An autorun command recovered from a user transcript entry."""

    prompt: str
    canonical_prompt: str
    command: str
    marker: str


@dataclass(frozen=True)
class _TranscriptPrompt:
    text: str
    marker: str


def latest_transcript_command(
    path: str | None,
    *,
    cli_type: str | None,
    command_names: Container[str],
    max_bytes: int = DEFAULT_TRANSCRIPT_COMMAND_SCAN_BYTES,
) -> TranscriptCommand | None:
    """Return the newest transcript user prompt that is an allowed autorun command.

    The scan is bounded to the JSONL tail and only accepts the first non-empty
    line of the newest user prompt when that line canonicalizes to one of
    ``command_names``. Free-form approval text is ignored.
    """
    if not path:
        return None

    for prompt in _iter_user_prompts_from_tail(path, max_bytes=max_bytes):
        command_line = _first_non_empty_line(prompt.text)
        canonical = canonicalize_command_prompt(command_line, cli_type)
        command = canonical.split(maxsplit=1)[0] if canonical else ""
        if command not in command_names:
            return None
        return TranscriptCommand(
            prompt=command_line,
            canonical_prompt=canonical,
            command=command,
            marker=prompt.marker,
        )
    return None


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _iter_user_prompts_from_tail(path: str, *, max_bytes: int) -> Iterable[_TranscriptPrompt]:
    for offset, line in reversed(_read_tail_lines(path, max_bytes=max_bytes)):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        marker = _line_marker(obj, line=line, offset=offset)
        for text in _extract_user_texts(obj):
            text = text.strip()
            if text:
                yield _TranscriptPrompt(text=text, marker=marker)


def _read_tail_lines(path: str, *, max_bytes: int) -> list[tuple[int, str]]:
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size <= 0:
        return []

    start = max(0, size - max_bytes)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            blob = f.read()
    except OSError:
        return []

    if start > 0:
        first_newline = blob.find(b"\n")
        if first_newline == -1:
            return []
        start += first_newline + 1
        blob = blob[first_newline + 1:]

    lines: list[tuple[int, str]] = []
    offset = start
    for raw_line in blob.splitlines(keepends=True):
        stripped = raw_line.rstrip(b"\r\n")
        if stripped:
            lines.append((offset, stripped.decode("utf-8", errors="replace")))
        offset += len(raw_line)
    return lines


def _line_marker(obj: dict[str, Any], *, line: str, offset: int) -> str:
    timestamp = _first_string(obj, ("timestamp", "time", "created_at", "createdAt", "ts"))
    payload = obj.get("payload")
    if isinstance(payload, dict):
        timestamp = timestamp or _first_string(
            payload, ("timestamp", "time", "created_at", "createdAt", "ts")
        )

    line_hash = hashlib.sha256(line.encode("utf-8", errors="replace")).hexdigest()
    if timestamp:
        source = f"{timestamp}:{line_hash}"
    else:
        source = f"offset:{offset}:{line_hash}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _first_string(obj: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_user_texts(obj: dict[str, Any]) -> Iterable[str]:
    payload = obj.get("payload")
    if isinstance(payload, dict):
        yield from _extract_user_texts(payload)

    entry_type = str(obj.get("type") or "").lower()
    role = str(obj.get("role") or "").lower()

    if entry_type == "event_msg" and isinstance(payload, dict):
        # Top-level Codex event_msg entries are handled by recursing into payload.
        return

    if entry_type == "user_message" or role == "user":
        for key in ("message", "prompt", "text"):
            value = obj.get(key)
            if isinstance(value, str):
                yield value
        content = obj.get("content")
        text = _content_text(content)
        if text:
            yield text

    if entry_type == "response_item" and isinstance(payload, dict):
        # Top-level Codex response_item entries are handled by recursing into payload.
        return


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "message", "prompt"):
            value = content.get(key)
            if isinstance(value, str):
                return value
        return _content_text(content.get("content"))
    if isinstance(content, list):
        parts = [_content_text(item) for item in content]
        return "\n".join(part for part in parts if part)
    return ""
