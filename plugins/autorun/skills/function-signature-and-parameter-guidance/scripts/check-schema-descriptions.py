#!/usr/bin/env python3
"""Check the mechanically decidable passes against a JSON Schema.

Reads an MCP `tools/list` response, a single JSON Schema, or a list of either, and
reports on the five passes that are string or structure facts. It deliberately does
NOT attempt the passes that need semantics — see the coverage banner it prints.

SCOPE — read before relying on this.

Handles: JSON Schema and MCP `tools/list`, any project exposing either. Because it
reads protocol output rather than source, the server's implementation language is
irrelevant — verified against a C-implemented MCP server (61 parameters, findings
confirmed by hand against the served schema). OpenAPI parameter schemas work too,
being JSON Schema.

Does NOT handle: clap / argparse / click / cobra definitions, `--help` output,
YAML or TOML config schemas, docstrings, function signatures. For those surfaces
use the grep recipes in the passes themselves — the reference gives one per check.

The word lists below are English-only and deliberately short: they favour precision
over recall, so a clean run means "none of these exact patterns", not "no problems".
Three of the five checks are the reference's grep recipes with JSON walking added;
the two that are genuinely easier here are the length statistics and the
`minimum`-absence heuristic.

Usage:
    aise mcp serve <<< '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \\
        | python3 check-schema-descriptions.py -
    python3 check-schema-descriptions.py schema.json

Pin the artifact. An installed binary usually predates the branch you are reviewing, so
findings may describe a schema you already fixed. Check the build date against your last
commit before acting on any result, or run the freshly built binary.
"""

import json
import re
import statistics
import sys

# Bounds stated as absences. Double negatives invert under paraphrase.
NEGATION = re.compile(
    r"\b(no negative|not negative|non-?negative|must not|cannot be|don't pass)\b", re.I
)
# Guidance shaped like a fact, carrying no threshold.
VAGUE = re.compile(r"\b(reasonable|appropriate|as needed|properly|sensible)\b", re.I)
# A signed-looking integer whose text never says what negatives do.
SIGN_WORDS = re.compile(r"\bnegative\b", re.I)


def walk(schema, tool, path=()):
    """Yield (tool, param_path, name, spec) for every leaf property, recursing into
    nested objects — a container is a namespace, so its members are parameters too."""
    for name, spec in (schema.get("properties") or {}).items():
        here = path + (name,)
        yield tool, ".".join(here), name, spec
        if not isinstance(spec, dict):
            continue
        # Recurse on anything carrying nested properties. Checking `type == "object"` alone
        # misses union types like ["object", "null"] and schemas that declare properties
        # without a type — both common, and both would silently skip every nested parameter.
        declared = spec.get("type")
        types = declared if isinstance(declared, list) else [declared]
        if "object" in types or "properties" in spec:
            yield from walk(spec, tool, here)


def parse(raw):
    """Parse one JSON document, or find the tools/list reply inside JSON-RPC stdio
    output. A stdio server emits one object per line, so a whole-input parse fails
    with 'Extra data' — scan lines and take the one carrying tool definitions."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")) or '"tools"' not in line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        # `initialize` also carries a "tools" key — capabilities.tools is an object,
        # while tools/list returns a non-empty array. Require the array.
        found = doc.get("result", doc) if isinstance(doc, dict) else doc
        if isinstance(found, dict) and isinstance(found.get("tools"), list):
            return doc
    sys.exit(
        "No JSON schema found. Pass a JSON Schema file, a tools/list response, or pipe\n"
        "stdio server output containing a line with a \"tools\" array."
    )


def load(raw):
    """Accept a tools/list response, a bare tool array, or one schema."""
    doc = parse(raw)
    if isinstance(doc, dict) and "result" in doc:
        doc = doc["result"]
    if isinstance(doc, dict) and "tools" in doc:
        return [(t.get("name", "?"), t.get("inputSchema") or {}) for t in doc["tools"]]
    if isinstance(doc, list):
        return [(t.get("name", "?"), t.get("inputSchema") or t) for t in doc]
    return [(doc.get("title", "schema"), doc)]


def tool_level_findings(raw):
    """Checks that need the whole tool list, not one parameter: protocol contract
    completeness, and one name carrying different meanings across tools."""
    out = []
    doc = parse(raw)
    tools = (doc.get("result", doc) if isinstance(doc, dict) else {}).get("tools")
    if not isinstance(tools, list):
        return out  # a bare schema has no tool-level contract to check

    enums, descs = {}, {}
    for t in tools:
        name = t.get("name", "?")
        schema = t.get("inputSchema") or {}
        # Unknown keys are silently dropped unless the schema forbids them.
        if schema.get("additionalProperties") is not False:
            out.append(("permissive-schema", name,
                        "additionalProperties is not false: a misspelled argument is "
                        "accepted and ignored"))
        # Without an output schema the caller cannot predict the response shape.
        if "outputSchema" not in t:
            out.append(("no-output-schema", name,
                        "no outputSchema: the response shape is unguessable before calling"))
        # Absent annotations do not leave the effect class unknown — the MCP defaults
        # assert it. destructiveHint defaults true and openWorldHint defaults true, so
        # omitting the block declares the tool destructive, non-idempotent, and
        # open-world. A read-only tool pays for that silence.
        annotations = t.get("annotations")
        if not isinstance(annotations, dict):
            out.append(("no-annotations", name,
                        "no annotations: the defaults assert destructive, non-idempotent, "
                        "and open-world. A read-only tool must declare readOnlyHint or a "
                        "conforming client treats it like a delete"))
        elif annotations.get("readOnlyHint") is True:
            # destructiveHint and idempotentHint are meaningful only when readOnlyHint
            # is false, so declaring them alongside it states a contradiction.
            contradictory = [k for k in ("destructiveHint", "idempotentHint")
                             if annotations.get(k) is True]
            if contradictory:
                out.append(("contradictory-annotations", name,
                            f"readOnlyHint is true, so {' and '.join(contradictory)} "
                            f"cannot apply — the schema scopes them to readOnlyHint == false"))
        for pname, spec in (schema.get("properties") or {}).items():
            if isinstance(spec, dict):
                if "enum" in spec:
                    enums.setdefault(pname, {})[name] = tuple(spec["enum"])
                descs.setdefault(pname, {})[name] = spec.get("description", "")

    for pname, per_tool in sorted(enums.items()):
        if len(set(per_tool.values())) > 1:
            shown = "; ".join(f"{k}={list(v)}" for k, v in per_tool.items())
            out.append(("overloaded-name", pname,
                        f"same name, different value space per tool: {shown}"))
    for pname, per_tool in sorted(descs.items()):
        texts = {d for d in per_tool.values() if d}
        if len(per_tool) > 1 and len(texts) > 1:
            out.append(("divergent-description", pname,
                        f"described differently on {len(per_tool)} tools "
                        f"({len(texts)} distinct texts) — verify they mean the same thing"))
    return out


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "-"
    raw = sys.stdin.read() if source == "-" else open(source).read()

    findings, lengths = [], []
    total = 0
    findings.extend(tool_level_findings(raw))

    for tool, schema in load(raw):
        for tool, path, name, spec in walk(schema, tool):
            if not isinstance(spec, dict):
                continue
            total += 1
            text = spec.get("description", "")
            where = f"{tool}.{path}"

            if not text:
                findings.append(("no-description", where, "no description"))
                continue
            lengths.append(len(text))

            if m := NEGATION.search(text):
                findings.append(
                    ("negation", where, f"bound stated as an absence: {m.group(0)!r}")
                )
            if m := VAGUE.search(text):
                findings.append(
                    ("vague-word", where, f"vague qualitative word: {m.group(0)!r}")
                )
            if "default" in spec and "default" not in text.lower():
                findings.append(
                    ("undocumented-default", where, f"declared default {spec['default']!r} absent from text")
                )
            if "enum" in spec:
                low = text.lower()
                missing = [v for v in spec["enum"] if str(v).lower() not in low]
                if missing:
                    findings.append(
                        ("undocumented-enum-value", where,
                         f"accepted values absent from the description: {missing}")
                    )
            # Integer with no `minimum` accepts negatives; the text must say what they do.
            if spec.get("type") == "integer" and "minimum" not in spec:
                if not SIGN_WORDS.search(text):
                    findings.append(
                        ("unstated-sign", where, "accepts negatives, text does not say what they select")
                    )

    # Zero parameters means the input did not parse as expected, NOT that the schema is
    # clean. Reporting "no findings" here would be this skill's own failure #19: an empty
    # result indistinguishable from a genuine pass.
    if total == 0:
        sys.exit(
            "Found 0 parameters — the input parsed but exposed no `properties`.\n"
            "This is an input problem, not a clean result. Check that the JSON is a\n"
            "tools/list reply, a tool array, or a schema with a `properties` object."
        )

    # Severity order matches this skill's thesis: silent-success defects first, then what
    # the caller cannot know, then confirmed text defects, then candidates needing judgment.
    TIERS = [
        ("1. SILENT — accepted and ignored, no error reaches the caller",
         {"permissive-schema"}),
        ("2. UNDECLARED — the caller cannot know this before calling",
         {"no-output-schema", "no-annotations"}),
        ("3. TEXT DEFECT — confirmed, the description omits a stated fact",
         {"negation", "undocumented-default", "undocumented-enum-value",
          "unstated-sign", "no-description", "contradictory-annotations"}),
        ("4. CANDIDATE — needs judgment, may be a working convention",
         {"overloaded-name", "divergent-description", "vague-word"}),
    ]
    print(f"Checked {total} parameters across the mechanically decidable checks.\n")
    confirmed = 0
    for title, codes in TIERS:
        rows = [f for f in findings if f[0] in codes]
        if not rows:
            continue
        print(f"{title}")
        for code, where, why in rows:
            print(f"  {code}  {where}\n        {why}")
        print()
        if not title.startswith("4."):
            confirmed += len(rows)

    if any(f[0] in TIERS[3][1] for f in findings):
        print("Tier 4 is a candidate list, not a defect list. A shared name whose values carry a\n"
              "consistent meaning across tools is a working convention — read the descriptions\n"
              "before changing anything. Tier 4 does not affect the exit code.\n")
    if not findings:
        print("  No findings from any mechanical check.")

    if lengths:
        print(
            f"\nlength  median {int(statistics.median(lengths))} chars, "
            f"max {max(lengths)} — justify each outlier by hand."
        )

    print(
        "\nNOT CHECKED — these need judgment and are not attempted here:\n"
        "  availability, semantic duplication, grammatical attachment, full sign\n"
        "  completeness, silent ignore, schema drift, composition, examples-execute,\n"
        "  zero-result honesty, cross-surface parity, unknown-name handling,\n"
        "  unstated assumptions\n"
        "A clean run here is NOT a clean review: the unchecked passes catch the defects that\nreturn success, which are the ones that matter most."
    )
    return 1 if confirmed else 0


if __name__ == "__main__":
    sys.exit(main())
