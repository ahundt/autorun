#!/usr/bin/env python3
"""Tests for scripts/check-schema-descriptions.py.

Every bug this script has shipped is pinned here as a regression. Two of the three
were the skill's own defects living in the skill's own checker — a false clean bill
on zero input (pass B5) and a value silently skipped (pass B1) — which is why they
are tested rather than merely fixed.

Run: python3 tests/test_check_schema_descriptions.py
"""

import json
import pathlib
import subprocess
import sys
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check-schema-descriptions.py"


def run(payload):
    """Feed the checker on stdin; return (exit_code, stdout+stderr)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "-"],
        input=text, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def tool(name="t", props=None, strict=False, **extra):
    """Build a tool definition. `strict` sets additionalProperties: false, which is
    needed whenever a test wants no tier-1 finding to fire."""
    schema = {"type": "object", "properties": props or {}}
    if strict:
        schema["additionalProperties"] = False
    return {"name": name, "inputSchema": schema, **extra}


def clean_tool(name, props):
    """A tool with nothing for the tool-level checks to report."""
    return tool(name, props, strict=True, outputSchema={}, annotations={})


class Regressions(unittest.TestCase):
    """One test per bug the script has actually shipped."""

    def test_multiline_jsonrpc_stdio_does_not_crash(self):
        """Bug 1: a whole-input json.loads raised 'Extra data' on stdio output.

        A stdio MCP server emits one JSON object per line. The checker must find the
        tools/list reply among them rather than fail to parse the stream.
        """
        stream = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1,
                        "result": {"capabilities": {"tools": {}}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2,
                        "result": {"tools": [tool(props={"a": {"type": "string",
                                                              "description": "x"}})]}}),
        ])
        code, out = run(stream)
        self.assertIn("Checked 1 parameters", out, out)

    def test_initialize_reply_is_not_mistaken_for_tools_list(self):
        """Bug 1b: `initialize` also carries a "tools" key, but as an object.

        Selecting the first line containing "tools" picked capabilities.tools = {},
        yielding zero parameters and a false clean bill.
        """
        stream = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1,
                        "result": {"capabilities": {"tools": {"listChanged": True}}}}),
            json.dumps({"jsonrpc": "2.0", "id": 2,
                        "result": {"tools": [tool(props={"a": {"type": "string",
                                                              "description": "x"}})]}}),
        ])
        code, out = run(stream)
        self.assertIn("Checked 1 parameters", out, out)

    def test_zero_parameters_is_an_error_not_a_clean_result(self):
        """Bug 2: reported "No findings" when it had parsed nothing.

        This is pass B5 applied to the checker: an empty result that looks identical
        to a genuine pass. It must exit non-zero and name the likely cause.
        """
        code, out = run({"tools": []})
        self.assertNotEqual(code, 0, "zero parameters must not exit clean")
        self.assertIn("0 parameters", out)
        self.assertNotIn("No findings", out)

    def test_nested_properties_under_a_type_union_are_walked(self):
        """Bug 3: recursion required type == "object" exactly.

        A nullable object declares type ["object", "null"], so every nested parameter
        was skipped in silence — pass B1 (accepted but never reaching the behavior).
        """
        code, out = run({"tools": [tool(props={
            "opts": {
                "type": ["object", "null"],
                "description": "options",
                "properties": {"inner": {"type": "integer",
                                         "description": "must not be negative"}},
            }
        })]})
        self.assertIn("t.opts.inner", out, "nested parameter under a type union was skipped")
        self.assertIn("negation", out)

    def test_nested_properties_without_a_declared_type_are_walked(self):
        """Bug 3b: schemas may declare `properties` and omit `type` entirely."""
        code, out = run({"tools": [tool(props={
            "opts": {"description": "options",
                     "properties": {"inner": {"type": "string",
                                              "description": "reasonable size"}}}
        })]})
        self.assertIn("t.opts.inner", out)
        self.assertIn("vague-word", out)

    def test_enum_documentation_check_is_case_insensitive(self):
        """A case-sensitive check flagged `toon` while the description said TOON.

        Found as a false positive in the checker's own output during an audit.
        """
        code, out = run({"tools": [tool(props={
            "format": {"type": "string", "enum": ["toon", "json"],
                       "description": "Compact TOON tables by default; json returns objects."}
        })]})
        self.assertNotIn("undocumented-enum-value", out, out)


class Severity(unittest.TestCase):
    """Tier assignment decides what fails CI, so it is pinned."""

    def test_candidates_alone_do_not_fail_ci(self):
        """A shared name with differing enums may be a working convention.

        The `mode`/`full` case proved this: seven differing enums, one consistent
        meaning. Candidates report but must not break a build.
        """
        code, out = run({"tools": [
            clean_tool("a", {"mode": {"type": "string", "enum": ["x"],
                                      "description": "mode x"}}),
            clean_tool("b", {"mode": {"type": "string", "enum": ["y"],
                                      "description": "mode y"}}),
        ]})
        self.assertIn("overloaded-name", out)
        self.assertIn("CANDIDATE", out)
        self.assertEqual(code, 0, "a candidate-only run must exit clean")

    def test_confirmed_defects_fail_ci(self):
        code, out = run({"tools": [tool(props={
            "a": {"type": "string", "description": "must not be empty"}
        })]})
        self.assertIn("negation", out)
        self.assertNotEqual(code, 0)

    def test_silent_tier_reported_before_text_tier(self):
        """Group B outranks Group A, so tier 1 must print above tier 3."""
        code, out = run({"tools": [tool(props={
            "a": {"type": "string", "description": "must not be empty"}
        })]})
        self.assertLess(out.index("1. SILENT"), out.index("3. TEXT DEFECT"), out)

    def test_output_names_the_passes_it_did_not_run(self):
        """A clean run must never read as a clean review."""
        code, out = run({"tools": [clean_tool("t", {"a": {"type": "string",
                                                          "description": "x"}})]})
        self.assertIn("NOT CHECKED", out)
        self.assertIn("clean run here is NOT a clean review", out)


class ToolLevelChecks(unittest.TestCase):
    def test_permissive_schema_is_flagged_as_silent(self):
        code, out = run({"tools": [tool(props={"a": {"type": "string", "description": "x"}})]})
        self.assertIn("permissive-schema", out)
        self.assertLess(out.index("1. SILENT"), out.index("permissive-schema") + 1)

    def test_strict_schema_is_not_flagged(self):
        t = tool(props={"a": {"type": "string", "description": "x"}})
        t["inputSchema"]["additionalProperties"] = False
        code, out = run({"tools": [t]})
        self.assertNotIn("permissive-schema", out)

    def test_readonly_tool_declaring_destructive_is_flagged(self):
        """The schema scopes destructiveHint and idempotentHint to readOnlyHint == false,
        so declaring both states a contradiction the caller cannot resolve."""
        t = clean_tool("t", {"a": {"type": "string", "description": "x"}})
        t["annotations"] = {"readOnlyHint": True, "destructiveHint": True}
        code, out = run({"tools": [t]})
        self.assertIn("contradictory-annotations", out)
        self.assertNotEqual(code, 0, "a stated contradiction is a confirmed defect")

    def test_consistent_annotations_are_not_flagged(self):
        t = clean_tool("t", {"a": {"type": "string", "description": "x"}})
        t["annotations"] = {"readOnlyHint": True}
        code, out = run({"tools": [t]})
        self.assertNotIn("contradictory-annotations", out)
        self.assertNotIn("no-annotations", out)

    def test_missing_annotations_message_names_the_default_not_uncertainty(self):
        """Absent annotations assert destructive rather than leaving it unknown."""
        code, out = run({"tools": [tool(props={"a": {"type": "string", "description": "x"}})]})
        self.assertIn("no-annotations", out)
        self.assertIn("assert destructive", out)

    def test_missing_annotations_and_output_schema_are_flagged(self):
        code, out = run({"tools": [tool(props={"a": {"type": "string", "description": "x"}})]})
        self.assertIn("no-annotations", out)
        self.assertIn("no-output-schema", out)

    def test_bare_schema_input_skips_tool_level_checks(self):
        """A plain JSON Schema has no tool contract to check; it must not be flagged."""
        code, out = run({"properties": {"a": {"type": "string", "description": "x"}}})
        self.assertNotIn("no-annotations", out)
        self.assertIn("Checked 1 parameters", out)


class ParameterChecks(unittest.TestCase):
    def test_integer_without_minimum_is_flagged_unless_sign_is_documented(self):
        code, out = run({"tools": [tool(props={
            "n": {"type": "integer", "description": "a count"}
        })]})
        self.assertIn("unstated-sign", out)

    def test_integer_documenting_negatives_is_not_flagged(self):
        code, out = run({"tools": [tool(props={
            "n": {"type": "integer",
                  "description": "positive keeps the first N, negative keeps the last N, 0 all"}
        })]})
        self.assertNotIn("unstated-sign", out)

    def test_declared_default_absent_from_description_is_flagged(self):
        code, out = run({"tools": [tool(props={
            "n": {"type": "string", "default": "x", "description": "a name"}
        })]})
        self.assertIn("undocumented-default", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
