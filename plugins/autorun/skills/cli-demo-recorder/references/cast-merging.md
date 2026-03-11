# Cast File Merging

Techniques for combining multiple `.cast` files into a single recording.

## asciinema cat (Simple Concatenation)

asciinema provides a built-in `cat` command for concatenating recordings:

```bash
# Concatenate two recordings
asciinema cat part1.cast part2.cast > combined.cast

# Concatenate with a pause between parts
asciinema cat part1.cast - part2.cast > combined.cast
# (the `-` reads from stdin — pipe in a separator cast if needed)
```

**Limitation**: `asciinema cat` requires matching terminal sizes across all input files. Mismatched sizes produce rendering artifacts.

## Python Script for Programmatic Merging

For more control over timing, transitions, and mismatched terminal sizes:

```python
#!/usr/bin/env python3
"""Merge multiple .cast files with timestamp adjustment and transitions."""
import json
from pathlib import Path


def merge_casts(
    input_files: list[str],
    output_file: str,
    gap_seconds: float = 2.0,
) -> None:
    """Merge cast files with a gap between each."""
    merged_events = []
    current_offset = 0.0
    header = None

    for cast_path in input_files:
        lines = Path(cast_path).read_text().strip().split("\n")

        # First line is the header (JSON object)
        file_header = json.loads(lines[0])
        if header is None:
            header = file_header
        else:
            # Use the largest terminal size
            header["width"] = max(header["width"], file_header["width"])
            header["height"] = max(header["height"], file_header["height"])

        # Parse events — format depends on version
        for line in lines[1:]:
            event = json.loads(line)
            # v2 format: [timestamp, type, data]
            timestamp = event[0] + current_offset
            merged_events.append([timestamp, event[1], event[2]])

        # Offset next file by last timestamp + gap
        if merged_events:
            current_offset = merged_events[-1][0] + gap_seconds

    # Write merged output
    with open(output_file, "w") as f:
        f.write(json.dumps(header) + "\n")
        for event in merged_events:
            f.write(json.dumps(event) + "\n")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: merge_casts.py output.cast input1.cast input2.cast ...")
        sys.exit(1)
    merge_casts(sys.argv[2:], sys.argv[1])
```

## Adding Transition Text Between Parts

Insert a "title card" between merged recordings:

```python
def add_title_card(
    events: list,
    offset: float,
    text: str,
    duration: float = 3.0,
) -> list:
    """Insert a title card at the given offset."""
    # Clear screen
    events.append([offset, "o", "\033[2J\033[H"])
    # Print centered title
    events.append([offset + 0.1, "o", f"\n\n    {text}\n\n"])
    # Hold for duration
    events.append([offset + duration, "o", "\033[2J\033[H"])
    return events
```

## Use Cases

### Combining Multi-Shell Demos

Canal records separate `.cast` files per shell. Merge them into a showcase:

```bash
python merge_casts.py demos/canal-all-shells.cast \
    demos/canal-interactive-zsh.cast \
    demos/canal-interactive-bash.cast \
    demos/canal-interactive-fish.cast
```

### Combining Feature Demos into Showcase Reel

```bash
python merge_casts.py demos/showcase.cast \
    demos/feature-safety-blocks.cast \
    demos/feature-plan-mode.cast \
    demos/feature-autorun.cast
```

### Combining CLI + TUI Recordings

```bash
python merge_casts.py demos/full-demo.cast \
    demos/cli-quick-commands.cast \
    demos/tui-interactive-session.cast
```

## Cast Format Reference

### v2 (asciicast v2)

```json
{"version": 2, "width": 160, "height": 48, "timestamp": 1234567890}
[0.5, "o", "$ command output\r\n"]
[1.2, "o", "more output\r\n"]
```

### v3

```json
{"version": 3, "width": 160, "height": 48, "timestamp": 1234567890}
[0.5, "o", "$ command output\r\n"]
```

The main difference: v3 supports additional event types and metadata. Both use the same `[timestamp, type, data]` event format for output events.

## Validation After Merging

```bash
# Play merged cast to verify timing
asciinema play combined.cast

# Check header
head -1 combined.cast | python3 -m json.tool

# Verify terminal size matches expectations
head -1 combined.cast | python3 -c "import json,sys; h=json.load(sys.stdin); print(f'{h[\"width\"]}x{h[\"height\"]}')"
```
