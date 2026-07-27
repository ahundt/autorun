# Sources

Checked: 2026-07-20

## Requirements as stated

The maintainer's instructions, verbatim and in order, with what each produced. Kept because
several rules here exist only because a specific correction was made, and paraphrasing them
would lose the reason.

1. *"make a skill for parameter naming and the full iterative process and gotchas and
   assumptions and what to avoid and look for and strategies like the checklist make it global
   across this machine (and use the process on the naming of the sections file etc)"*
   - Created the skill; the naming-decision table below applies its own rules to its filenames.
2. *"you're not accounting for the idiosyncracies of properly complex params and failure modes
   … e.g. -5 is also a valid index to the end, where 5 might be the first 5 entries -5 might be
   the last 5 entries, and 0 unlimited"*
   - Part 1 §1, the signed-integer collision. The single most-cited shape in the skill.
3. *"searching 'ambiguous' and 'docstring' and 'param' alongside other relevant words might be
   useful"*
   - Cross-project corroboration via ai-session-search; the `[independent]` markers.
4. *"there is also a skill writing skill you should use (also the eval script … contains some
   bugs like hardcoded strings it insists be present which is a false assumption)"*
   - Applied `claude-skill-builder`; fixed its path handling and warning strings (`a6ec825`).
5. *"you are proliferating a lot of files you need to properly consolidate this skill"*
   - Six files to four; `mistakes-and-fixes.md` folded into the passes it justified.
6. *"shouldnt the checklist and the guides be consolidated in the main skill file too also does
   somewhere say to explore the codebase to ensure conventions are followed and avoid semantic
   duplication"*
   - Checklist promoted to SKILL.md; process step 1 and pass A2 added.
7. *"be careful if you have a checklist it needs to be complete i'm concerned you are causing
   some regressions"*
   - Caught: the checklist had silently dropped 6 questions. Restored to 20.
8. *"be careful about cutting you are often wordy and remember that wordiness reduction without
   losing facts process"*
   - The fact-sheet-then-cut-then-verify loop; pass A8's "never cut" list.
9. *"again don't make it too easy to pass either capture the intent"*
   - Audit warnings must name a substitute before self-crediting.
10. *"ai can be literal anticipate and prevent common errors it will make, e.g. oh we have
    numbers or some really high level object -> pass (thus concept missed entirely) make
    explicit the concepts behind the concepts"*
    - "Generalizing Beyond the Examples" and the composite-parameter recursion rule.
11. *"maybe your parameter naming skill can detect problems too? might get too complex though"*
    - `check-schema-descriptions.py`, deliberately scoped to the mechanical checks only.
12. *"remember tdd"* / *"make sure the tdd is actually robust and covers edge cases too"*
    - Red-first for every code change; the edge-case suite around `nearest_name`.
13. *"hey no ableism! i just saw blind!!! you need to amend that!!!"*
    - Renamed the checker's tier 2 from "CALLER-BLIND" to "UNDECLARED"; commit amended.
14. *"i didn't consent to new commits soft reset that"*
    - Commits are made only when asked.
15. *"are function names mentioned too? should this actually all be called the function
    signature naming skill"* / *"if it can cover both make sure you use both words"*
    - Renamed to `function-signature-and-parameter-guidance` at v3.0.0.
16. *"read the whole thing block by block for all files"* / *"use your context and or actually
    reread the files not just programming tricks"*
    - A full read found nine defects greps had missed, including B8 filed under Group A.
17. *"shouldn't you just do direct real file edits like you usually do"*
    - Scripted string surgery was failing silently; switched to the Edit tool.
18. *"use numbered lists also … looks like you just cut a lot of facts we discussed keeping"*
    - Restored the checker's 11 check names and the CLI frameworks it cannot read.
19. *"would a maintainer accept it as is?"* / *"keep working until it is maintainer ready and
    you need to harshly assess and refine not assume maintainer ready"*
    - Found the blocking gap: skill-builder Step 4 testing had never been run. Added the blind
      triggering test and `tests/` (20 tests).
20. *"do you have other mcp servers active you could try the system and skill on?"*
    - GitKraken audit; closed the selection-bias gap.
21. *"are you sure you are covering real mcp doc based criteria is destructiveHint
    standardized?"*
    - Verified against `schema.ts`. Corrected the annotation-defaults claim: absent annotations
      *assert* destructive rather than leaving it unknown.
22. *"be careful not to get too mcp focused maybe an mcp specifics resource md file shoudl be
    separate"*
    - Split `mcp-specifics.md`; B8 restated generically, surfacing the HTTP prior art.
23. *"you need to make this applicable to c and rust and python and mcp and jsonrps etc etc so
    be careful withthe phrasing and framing and assumptions at the core"*
    - B2 renamed to "Declaration versus implementation drift"; A9 degeneralized from schemas.
24. *"remember how we discussed ai looks at examples like that's the whole universe rather than
    the general principle, words must be explicitly used to help pure examples are not enough"*
    - The generalizing rule now governs writing, not only reading: state the principle in words
      *and* give the example.
25. *"the languages i use the most are typescript javascript rust python c/c++ also be careful
    you are not adding fluff or unwarranted length"*
    - Added `indexOf` returning `-1`; cut `strcmp` and two framing sentences. Net shorter.

## Lessons from building this skill

Process findings, distinct from the guidance itself. Each cost real rework.

1. **The passes kept finding defects in the document that defines them.** The checker shipped
   B5 (reported "no findings" after parsing zero parameters) and B1 (silently skipped every
   nested parameter under a type union). SKILL.md and this file each carried A9 unstated
   assumptions. A9 was written, then immediately caught three instances in its own section.
   If a rule is real, it applies to its own statement — run each new pass on the skill first.
2. **Grep yields candidates; only reading yields findings.** Three separate false results:
   A1 flagged 11 cross-references of which 10 were English words; an ableism scan matched
   "B**lame**d" via `lame`; a self-check reported "Group A/B not self-defined" because a line
   wrap split the phrase. Every one looked authoritative.
3. **Renumbering breaks cross-references silently.** Inserting pass A2 shifted A4→A5 through
   A7→A8, breaking the checklist's mapping column *and* the script's printed codes, with
   nothing failing. The fix generalizes: the script now reports by check *name*, because names
   are stable and numbers are positional.
4. **Consolidate on pointer count, not file count.** Three files that constantly referenced
   each other were one file; the proof was a `§10` pointer that outlived the section it named.
5. **Reformatting is not reducing.** Converting the References prose to a table saved exactly
   zero words — markdown pipe syntax cost back everything the consolidation gained.
6. **Compression and fact-deletion look identical in a diff.** Cutting words that restate an
   adjacent word's work is compression; cutting the checker's 11 check names is deletion, and
   a reader cannot recover those from context. The usable test is *will the reader have the
   other copy in front of them?*, which is why pass A8 states it.
7. **Verify before asserting, including about specifications.** Three corrections, all caught
   by someone asking rather than by a check: `destructiveHint` defaults to **true**, inverting
   the claim that missing annotations leave the effect class unknown; Jaro-Winkler was called
   "actively wrong" when it ranks correctly and merely compresses the gap; a seven-way `mode`
   enum divergence was a false positive because the shared value `full` meant the same thing
   in all five tools using it.
8. **Deriving a checker from the codebases that inspired it hides its blind spots.** Running
   it against an unrelated third-party server confirmed the checks and, as importantly, showed
   it staying silent where they did not apply.
9. **Good content is not a usable skill.** The guidance was sound long before the skill was
   dependable. A blind triggering test — an agent given only the description and sixteen
   requests — found four overtriggering phrases, one self-contradiction, and five uncovered
   cases that no amount of content review would have surfaced.
10. **Section order is a correctness property, not styling.** B8 sat under the "Group A"
    heading, and "Generalizing Beyond the Examples" opened with "every check names an instance"
    while positioned above any check.
11. **A bare example is read as the rule's full scope, by humans and models alike.** This
    shaped how the passes are worded: each states its principle in words *and* names an
    instance, and cited cases are labelled as cases (*an instance*, *seen in the wild*). Left
    to an example alone, "reject negative `limit`" gets applied to `limit` and nothing else.
    The reader-facing half of this — state the accepted range rather than a sample value —
    belongs in SKILL.md and is there; this entry is the authoring rule.
12. **Scripted edits fail silently; the Edit tool fails loudly.** `if old in t` skips a
    non-matching replacement without complaint, which produced vanished edits, orphan line
    fragments, and a word hyphenated across a line break. This is the skill's own silent-ignore
    defect committed by the tool chosen to fix it.

## Version history

**3.0.0** — Renamed from `parameter-naming-and-guidance`; the old name undersold the scope,
since 5 of 17 passes operate on the callable and SKILL.md never said "function" or "tool name".
Added pass B8 (protocol contract completeness: `additionalProperties`, `outputSchema`,
`annotations`, error channel) and pass A9 (unstated assumptions). Broadened B5 from zero-result
honesty to result-set honesty, closing a hole where Part 1 required ordering disclosure and no
pass verified it. Moved B8 out from under the Group A heading. Added `tests/`. Description
rewritten after a blind triggering test found four overtriggering phrases, one
self-contradiction, and five uncovered use cases. SKILL.md 2,134 → ~1,850 words with all facts
retained; sections reordered so nothing is invoked before it is introduced.

**2.x** — Split Group A (is the text right) from Group B (does the behavior match) and put B
first. Consolidated six files to four: `mistakes-and-fixes.md` folded into the passes it
justified, and three workflow files merged into one document with three Parts.

**1.0.0** — Initial: the Four Facts, naming rules, and the first review passes, drawn from
defects found preparing ai-session-search 1.0.0-rc.1.

This skill is grounded in two evidence classes: **specifications** (external, citable) and
**direct observation** (defects found in real codebases this week). Observation entries name
the artifact so a maintainer can re-verify or discard them as the code changes.

---

## Primary — specifications, verified this session

- [JSON-RPC 2.0 Specification](https://www.jsonrpc.org/specification)
  — confirmed `-32602 "Invalid params"` is the reserved code for invalid method parameters,
  distinct from `-32601 "Method not found"`. Basis for pass B7 treating unknown *tool* and
  unknown *parameter* as separate dispatch paths that must both be checked.
- [Python 3 — Common Sequence Operations](https://docs.python.org/3/library/stdtypes.html)
  — confirmed `s[-5]` returns exactly one item (`len(s) + i` substitution) and raises
  `IndexError` on short input, while `s[-5:]` returns *up to* five items and clamps silently.
  Basis for the Part 1 §1 claim that "negative counts from the end" is insufficient without
  stating arity.

- **pydantic 2.12.3** — `BaseModel(a=2, unknown_key="x")` accepted and silently dropped the
  unknown key. Confirms the Step Zero claim that `extra="ignore"` is the default.
- **Levenshtein vs Jaro-Winkler on prefix-clustered names** — for the typo `search_message`:
  Levenshtein 1 (`search_messages`) vs 5 (`search_sessions`); Jaro-Winkler 0.987 vs 0.856.
  `difflib.get_close_matches` returns both. Corrects an earlier overstatement here:
  Jaro-Winkler ranks correctly but compresses the gap, so a typo-tolerant threshold admits the
  wrong candidate. It does not mis-rank.
- **`difflib.get_close_matches`** — present in the Python 3 standard library, as claimed in the
  library table.
- [MCP `schema.ts`, 2025-06-18](https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-06-18/schema.ts)
  — `ToolAnnotations` declares `title`, `readOnlyHint` (default `false`), `destructiveHint`
  (default **`true`**), `idempotentHint` (default `false`), `openWorldHint` (default `true`),
  and `annotations` is optional on `Tool`. Corrects an earlier claim in pass B8 that missing
  annotations leave a caller unable to tell whether a tool mutates: the spec's defaults mean an
  absent block *asserts* the tool is destructive, non-idempotent, and open-world. The cost is
  therefore borne by read-only tools, which a conforming client must treat as destructive.
  The schema also states `destructiveHint` and `idempotentHint` are meaningful only when
  `readOnlyHint == false`, and that display precedence is `title`, `annotations.title`, `name`.
  Note that `Annotations` (audience, priority, lastModified) is a *content* type and unrelated.
- **GitKraken MCP, 31 tools / 126 parameters**, audited 2026-07-20 — third-party commercial
  server, used to test the checker outside the two codebases it was derived from. Confirmed by
  hand: `git_branch.action` and `git_worktree.action` declare enums (`create`/`list`,
  `list`/`add`) that their descriptions never name; `app_tool_box.directory` has no description.
  Annotations 31/31, outputSchema 0/31, additionalProperties 0/31.

## Primary — specifications, cited from model knowledge, NOT fetched this session

Flagged explicitly because this skill's own rules forbid presenting unverified claims as
verified. Remaining rows in the Step Zero and library tables were not executed either — verify
before relying on any of them for a consequential decision.

- ISO 80000-2 (Quantities and units — Mathematics) defines ℕ as including `0`, while much
  mathematical literature excludes it. Basis for naming rule 6 ("never use math jargon for a
  bound"). The claim needed for the rule is only that the convention is *disputed*, which is
  uncontroversial; the specific standard number is the unverified part. Standard is paywalled.
- [JSON Schema Validation 2020-12](https://json-schema.org/draft/2020-12/json-schema-validation)
  — `minimum` as the keyword whose presence signals whether negatives are accepted. Used as
  background, not as a load-bearing claim.

## Primary — direct observation (the tool's own behavior is ground truth)

- **ai-session-search**, branch `feat/ai-session-search-rust-migration`, commits `1581150`,
  `728852e`, `87b00fe`, `7722279` — source of the unmarked "Seen in the wild" entries in
  Part 2 of the reference, each naming the wrong text and the committed correction.
- **ai-session-search MCP response shape**, observed live 2026-07-20 — an empty
  `search_messages` result echoes `match_mode`, `limit`, and `offset`, but omits `query`,
  `session_id`, `path_prefix`, `provider`, and the seq bounds. Concrete instance of the
  zero-result ambiguity in pass B5. Open at time of writing.

## Secondary — cross-project corroboration

Entries marked `[independent]` in Part 2 of the reference come from a Codex session on an
unrelated codebase during the same week. Recurrence across unrelated projects is the evidence
that these failure modes are structural. Retrieved via `ai-session-search`.

- Session `codex:019f5f0a-8bad-7e41-935a-c446d448ecc4` (repo `codebase-memory-mcp`):
  - seq 33692 — closing the input policy exposed four implemented-but-undeclared parameters
    (`verbose`, `project_name`, `repo_path`, `auto_dep_limit`). Basis for pass B2.
  - seq 33896 — `search_code` + `search_in:"graph"` silently performed a source search.
    Basis for pass B1.
  - seq 34319 — valid-but-wrong project yields an empty result indistinguishable from a
    genuine miss; fixed by echoing the resolved project. Basis for pass B5.
  - seq 24733, 25388, 25417 — advertised `WITH` + `OPTIONAL MATCH` failing to compose; a
    docstring example (`ORDER BY CASE … LIKE`) the grammar cannot execute; an unprojected
    `ORDER BY` key silently ignored. Basis for passes B1, B3, and B4.
  - seq 22947 — `detect_changes.depth` clamped, echoed, and never used to traverse. Basis
    for pass B1.
  - seq 24168, 22197 — property discovery capped at 50 keys with no truncation reported while
    documented as "full"; project premortem naming silent clipping as a cause of false
    "not found" conclusions. Basis for pass B5.
  - seq 33559, 33791 — argument validation is `Ω(k)` in supplied keys; a full typo-rejection
    round trip measured 49.8–61.7 µs median, 70.2–82.2 µs p95 over 900 calls, so a shared
    cache was rejected. Basis for the "measure before optimizing validation" rule.
  - seq 22548, 35478 — a user rejected "reasonable computational cost" as vague (basis for
    pass A6) and questioned `mcp_schema_project_snapshot` as implying database duplication
    (basis for naming rule 7).

## Method

- [Anthropic: The Complete Guide to Building Skills for Claude](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)
  (January 2026), via the local `claude-skill-builder` skill — progressive disclosure levels,
  trigger-phrase description format, required `references/sources.md` with per-source
  "what it confirmed" notes.

## Discrepancies

`claude-skill-builder/scripts/audit-skill.sh` produced three warnings on this skill. All three
were traced to the checker rather than the skill, and the script was **fixed in place** —
path handling repaired, and each warning amended to state its intent and permit self-credit
when a differently-shaped skill meets it.

- **Relative paths failed.** `audit-skill.sh .` reported 1 FAIL and 79%; the absolute path
  reported 0 FAIL and 91% on identical content. Cause: `basename "."` returns `.`, failing the
  kebab-case name check. Fixed by resolving with `cd … && pwd -P` before `basename`. Verified
  across `.`, `name`, `name/`, and `./name`.
- **`## How It Works` was grepped literally,** so the check could not see a descriptive
  heading such as `## The Process` — penalizing the naming discipline it should reward.
  Warning rejected for this skill; string amended to name the intent.
- **"No examples found"** looks only in `## Examples` or `references/examples/`. This skill's
  worked examples live inside Part 2 of the reference, beside the passes they illustrate.
  Consolidation was chosen over satisfying the grep; splitting them back out would recreate
  the duplication that merging removed. Warning amended to accept inline examples.
- **The same script warns when SKILL.md lacks "quantitative outcomes"**, grepping for
  `faster|reduction|improvement|save.*time|NN%` (line 314). This contradicts
  `claude-skill-builder/references/best-practices.md:62-91`, which states outcome-focused
  language belongs in a GitHub README and is *wrong* for SKILL.md. Warning rejected as
  internally inconsistent; no invented metrics were added. The real measurements this skill
  does carry (`Ω(k)`, 49.8–61.7 µs, median 148-character descriptions) are facts, not
  positioning, and simply do not match the grep.
- **`best-practices.md:406` recommends a 15–30 word description**, while
  `best-practices.md:48` and the Anthropic guide set a 1024-character hard limit and the
  guide's own Format B example exceeds 30 words. Followed the character limit; this skill's
  description is 790 characters, since trigger-phrase coverage matters more than brevity
  for activation.

## Naming decisions for this skill's own files

The rules apply to filenames and headings too:

| Candidate | Verdict |
|---|---|
| `parameter-naming-and-guidance` | **Renamed away** at v3.0.0. Undersold the scope: 5 of 17 passes (A2, B2, B3, B7, B8) operate on the callable, and SKILL.md mentioned "function" and "tool name" zero times. |
| `designing-and-reviewing-signatures` | Rejected. A signature is name, parameters, and return type — it excludes descriptions and error messages, over half the content. |
| `hard-to-misuse-apis` | Rejected. Names the outcome and covers all five areas, but "APIs" is a stretch for config keys and env vars. |
| `function-signature-and-parameter-guidance` | **Chosen.** `function-signature` rather than bare `signature`, which a cold reader parses as crypto or email (pass A9). `guidance` carries the descriptions and error messages a signature omits. |
| `param-surface-audit` | Rejected. "Surface" is invented jargon; `param` is an abbreviation nobody searches for. |
| `parameter-review` | Rejected. "Review" is a vague verb — it says neither what gets reviewed nor what is produced. |
| `references/gotchas.md` | Rejected. Names a feeling, not contents. |
| `references/designing-and-reviewing-signatures-and-parameters.md` | **Chosen.** Names the two activities it serves. Long, and worth it — a shorter `handbook.md` or `guide.md` would say nothing. |

Two consolidation rounds shaped that last file, both naming problems pointing at design
problems: `mistakes-and-fixes.md` held 21 defects that each restated the pass they justified,
so each moved under its pass as evidence; and three files covering three phases of one
workflow cross-referenced each other constantly, until a `§10` pointer outlived the section it
named. **A file that needs constant pointers into another file is usually one file.**

Section headings follow the same rule: "The Four Facts" and "What to Avoid" say what follows;
"Best Practices" and "Considerations" would not.
