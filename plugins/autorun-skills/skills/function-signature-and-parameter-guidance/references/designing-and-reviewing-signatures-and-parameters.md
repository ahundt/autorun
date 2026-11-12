# Designing and Reviewing Signatures and Parameters

One document, three phases. Read the phase you are in.

| Phase | Part | Use when |
|---|---|---|
| Design | [Part 1 — Ambiguous Shapes](#part-1--ambiguous-shapes) | Naming a tool or parameter, or settling a value space, before it exists |
| Audit | [Part 2 — Review Passes](#part-2--review-passes) | Checking tools and parameters that already exist, before a release |
| Fix | [Part 3 — Solving It For The Caller](#part-3--solving-it-for-the-caller) | Deciding what a rejection should say and do |

Part 1 asks whether the value space is well defined. Part 2 asks whether the implementation
honors it. Part 3 is what to do about anything either turns up.

---

# Part 1 — Ambiguous Shapes

Eight shapes that are ambiguous *by construction*. No amount of careful wording fixes them;
each has to be disambiguated explicitly, because a competent reader can derive two different
correct-looking meanings from the same signature.

For each: the collision, the readings it produces, and the facts the description must state.

---

## 1. The signed integer — count, index, and sentinel collide

**The principle: any integer whose sign or zero carries a second meaning is two parameters
wearing one type.** The instances below span languages and surfaces because the collision is in
the type, not the surface.

JavaScript's `indexOf` returns `-1` for not-found, so `if (s.indexOf(x))` is wrong for a match
at position 0 — the most-hit instance of this defect anywhere. C's `read()` returns a byte
count *or* `-1`, and `ssize_t` exists only to make room for that one sentinel; `snprintf`
returns what it *would* have written, not what it did.

**Where the language offers a second channel, use it.** Rust's `Option<usize>` and
`Result<usize, E>`, TypeScript's `number | null`, Python raising rather than returning `-1`,
Go's `(n, err)`. A documented sentinel is a worse fix than a type that cannot carry one.

A single `int` routinely carries three unrelated meanings at once.

| Value | Count reading | Index reading | Sentinel reading |
|---|---|---|---|
| `5` | the first 5 items | the item at position 5 | — |
| `-5` | the last 5 items | the item 5 from the end | — |
| `0` | zero items | the first item (0-based) | **all / unlimited** |
| absent | the default | the default | the default |

Three traps:

**`0` usually reverses its natural reading.** In count-space `0` means "none". APIs
overwhelmingly overload it to "unlimited". A caller reasoning from first principles gets the
*opposite* of the truth, so `0` can never be left to inference — even when it seems obvious.

**"Negative means from the end" is not enough.** It does not say how many items come back.
Python makes the distinction visible: `a[-5:]` returns up to five items — fewer if the sequence
is shorter — while `a[-5]` returns exactly one and raises `IndexError` on short input. Both are
"from the end", yet they differ in arity *and* in short-input behavior:

```
positive keeps its first N lines, negative keeps its last N lines, 0 keeps complete content
```

not

```
negative counts from the end
```

**Count and index cannot share a parameter.** If `5` might mean "five items" or "the item at
index 5", no wording rescues it — rename, or split into two parameters.

**Required facts:** what positive selects, what negative selects, what `0` selects, what
omission selects. Four facts, every signed parameter, every time.

## 2. Bound inclusivity — the invisible off-by-one

`until`, `end`, `max`, `to`, `before`, `seq_to`, `depth`. Nothing in any of those names says
whether the endpoint is included.

Precedent is genuinely split: `range(0, 5)` excludes `5`; SQL `BETWEEN` includes it; slices
exclude; a message-index `seq_to` usually includes. The reader has no basis to guess.

**Required fact:** the word "inclusive" or "exclusive". One word.

Same for `depth`: does `depth=1` mean the node itself, or the node plus one hop?

## 3. Empty, absent, and null are three states, not two

`query=""`, `query` omitted, and `query=null` are distinct inputs that routinely behave
differently. The storage layer bites too: one project found optional columns normalized `NULL`
to `""` on read, so the historical null could no longer be distinguished from empty without a
migration.

Guard the reverse case: does an empty filter match *everything* or *nothing*? Both are
defensible; only one is implemented.

**Required facts:** what omission does, and what an explicitly empty value does when it differs
from omission. If they are identical, say so — the reader cannot derive that either.

## 4. Filter versus presentation — does this change *which*, or only *how much*?

The most consequential ambiguity for AI callers. Given `lines_per_message`, a caller cannot tell
whether trimming displayed lines also drops results, changes ranking, or alters pagination.

Guess "it filters" and they avoid it, blowing their context budget. Guess "cosmetic" when it
actually filters and they conclude "no matches" from a truncated view.

**Required facts:** state the negative space explicitly. A presentation parameter must say what
it does *not* affect:

```
This presentation window does not change matches, ranking, result count, pagination,
context membership, or reference extraction.
```

Long, and it earns the length. This is the one place where enumerating what a parameter does
*not* touch beats a shorter positive statement, because the reader's default assumption is
wrong and expensive.

## 5. Pagination — before or after what, and in what order?

`limit` and `offset` interact with filtering, deduplication, ranking, and context expansion, and
the order is invisible from the signature.

- Does `limit` cap rows scanned, rows matched, or rows returned after dedup?
- Does `offset` skip filtered or unfiltered rows?
- Do context rows count against `limit`?
- Is ordering stable enough that paging is coherent at all?
- **Is the ordering by relevance, or by something arbitrary?**

Unstable ordering makes `offset` silently lossy: page 2 can omit rows that moved to page 1.

The last question corrupts every capped result when unanswered. Observed: one tool documented
"ranked by relevance" while its sibling ordered by `session_id, seq`, disclosing that only in a
response field. A 30-hit sample returned unrelated sessions and missed known-relevant ones —
they sorted later, not lower. An agent reading that concludes the corpus is irrelevant.

**Required facts:** what the count counts, whether ordering is deterministic, and **what the
ordering is** — stated where the caller reads `limit`. A fact disclosed after the call cannot
inform the call.

## 6. Units — the name usually omits them

`size`, `max`, `width`, `timeout`, `context`. Bytes or characters? Characters or graphemes?
Lines or messages? Milliseconds or seconds?

**Fix at the name, not the description:** `preview_chars`, `timeout_ms`, `lines_per_message`.

`context` is worth calling out: in a search API it usually means *neighboring turns*, but a
reader may reasonably take it as characters of surrounding text.

## 7. A filter that can silently become a no-op

When one filter matches against several fields, some inputs select *everything*. The caller gets
a full result set that looks filtered, and nothing in the response says otherwise.

Observed: `path_prefix` matched "working directory, git repo, **or transcript path**". Every
transcript for one agent lived under `~/.claude/`, so `path_prefix="~/.claude"` — the most
natural value to type when scoping to that agent's work — matched every session ever recorded,
including repositories in unrelated trees. Documented behavior; undocumented *consequence*; and
invisible, because plausible results come back.

This is the zero-result defect (pass B5) inverted. That one is an empty answer that looks like a
miss; this is a complete answer that looks narrowed. Both come from the response not saying what
it actually filtered on.

**Required facts:** name each field the filter tests, and state which inputs match broadly. The
sturdier fix is structural — split the filter so the common intent is the default and the broad
match is opt-in, or echo which field matched on each hit.

**Generalizes to:** any "search everywhere" default, case-insensitive matching that collapses
distinct values, short prefixes that match all, and tag filters that OR when the caller expects
AND.

## 8. Composite parameters are namespaces, not parameters

An options object, config block, dict, or nested struct is not one parameter. Every shape above
applies to each member *and* to the container:

- What does an **empty** container mean — no filters, or filter-everything-out?
- What does an **absent** container mean, and does it differ from empty?
- What happens to an **unknown key** inside it — rejected, ignored, or passed through?
- Do members **interact**, and what happens when conflicting ones are both set?
- Does a member **override** or **merge** with a value set elsewhere (config file, env var, a
  sibling flag)?

This is where "it is a high-level object, so the checks do not apply" does the most damage. A
composite has a larger value space than a scalar, so it carries more ambiguity, not less.

---

## Disambiguation Checklist

The checklist lives in `SKILL.md`, so it is loaded whenever the skill triggers rather than
only when this reference is opened. The shapes above are the reasoning behind its questions;
the right-hand column there names the pass in Part 2 that verifies each answer.

---

# Part 2 — Review Passes

## Group B — Does the behavior match the text?

## B1 — Silent ignore

Trace every parameter from parse to use. Three failure modes, escalating:

1. **Clamped** without saying so — you pass `depth=50`, get `depth=3`, and are not told.
2. **Echoed but unused** — validated, clamped, returned in the response, never reaching the
   behavior. Looks honored at every observable layer.
3. **Ignored with a substitute behavior** — an enum value valid on a sibling tool causes a
   different operation instead of an error.

```bash
# Does the name appear anywhere past the parse/validate layer?
rg -n 'depth' src/ | rg -v 'parse|validate|schema|clamp'
```

An empty result from that second command is the finding.

**Seen in the wild [independent]:** `detect_changes.depth` was clamped, echoed, and never
used to traverse, so a bounded impact analysis returned a flat report. An unprojected
`ORDER BY` key was accepted and ignored, giving "apparently successful but incorrectly ordered
results". `search_code` accepted `search_in:"graph"` and ran a *source* search — legitimate on
a sibling tool, so it validated cleanly.

**Fixes:** implement it or delete it; reject rather than clamp, or report the effective value
and say it was clamped; reject invalid *combinations* of individually valid values and name
the tool that does support them.

**Test shape:** assert the *effect*, not acceptance. A test asserting "no validation error"
passes on all three failure modes.

## B2 — Declaration versus implementation drift

Compare what is *declared* against what the code accepts. The declaration is wherever a caller
looks first — a JSON Schema, an OpenAPI document, a C header, a `.pyi` stub, a docstring, a
`--help` string, a trait or interface definition. Both directions fail:

- **Implemented, undeclared** — works, but undiscoverable; closing the input policy breaks it.
- **Declared, unimplemented** — advertised and silently dropped.

This pass costs nothing where the declaration *is* the definition — a Rust or TypeScript
signature sits on its own body. It applies wherever they are separate and something other than
the compiler must keep them aligned: a C or C++ header, a `.d.ts` or `.pyi` stub, hand-written
docs, or any schema not generated from the code it describes. A C prototype absolutely can
drift from its definition across translation units, which is why `-Wmissing-prototypes` exists.

**Seen in the wild [independent]:** closing the input policy produced seven test failures, and
every one was pre-existing drift rather than damage from the change — `verbose`, the
`project_name` compatibility alias, `repo_path`, and `auto_dep_limit` were all accepted by the
code and absent from their canonical schemas.

**Fix:** make the schemas describe the already-supported behavior, then add drift tests so
discovery, dispatch, and CLI cannot diverge again. Weakening validation or deleting working
compatibility is the wrong direction.

**Lesson:** failures that appear when you *tighten* validation usually expose old drift. Read
them before rolling back.

## B3 — Composition

Feature lists imply composition. For every pair of advertised features, confirm the pair works,
and document every restriction — a caller cannot infer it, and cannot tell an unsupported
combination from a bug.

**Seen in the wild [independent]:** a description advertised both `WITH` and `OPTIONAL MATCH`;
`WITH … OPTIONAL MATCH` was rejected as an unexpected trailing clause. Multiple `ORDER BY` keys
were rejected too, and that restriction was documented nowhere.

## B4 — Examples execute

Every example in a description, help text, or docstring is a test. Extract and run them in CI.

**Seen in the wild [independent]:** a docstring recommended `ORDER BY CASE … LIKE`; the engine's
grammar accepts neither `CASE` nor `LIKE` in that position. Confident, prominent, never run.

**Lesson:** examples are the highest-trust element in documentation, so a broken one costs more
than a missing one.

## B5 — Result-set honesty

Does the response tell the caller what it actually did?

- Does an empty result echo the resolved inputs it filtered on?
- Does every cap emit `truncated` / `has_more` and a continuation cursor?
- Is anything advertised as "complete" or "full" that is actually capped?
- **Is the ordering stated where the caller reads `limit`, not only in the response?** Part 1
  §5 sets the requirement; this is where it is checked. A capped result under undisclosed
  ordering is an arbitrary sample presented as the top matches — and a full result set from a
  filter that silently matched everything (Part 1 §7) is the same defect inverted.

**Seen in the wild [independent]:** after strict validation closed the typo path, a
*valid-yet-wrong* project name still returned empty — and the response omitted the resolved
project entirely, so "searched the wrong project" and "searched the right one, found nothing"
were indistinguishable. Fixed by echoing the resolved project; no wording change substitutes.

Separately, property discovery was capped at 50 keys per label with no truncation reported while
documented as "full" discovery. That project's own premortem named the consequence: response
caps silently clip the relevant result and agents draw false "not found" conclusions.

**Lesson:** validation removes typos, not wrong-but-plausible values. A bounded result described
as complete is a false claim, not a rounding detail.

## B6 — Cross-surface parity

Trigger the same mistake through the CLI, the schema, and the library binding. They need not be
byte-identical, but must name the same parameter, the same bound, and the same corrective action.
Divergence teaches callers the surfaces behave differently.

## B7 — Unknown-name handling

Check **every** dispatch path — a boundary guard and a fallback arm are two messages that drift
apart. JSON-RPC separates `-32601 "Method not found"` from `-32602 "Invalid params"`; the two
paths are distinct by specification, so check both.

- **Unknown tool/command:** list served names, derived from the registry the dispatcher reads.
- **Unknown parameter:** offer the nearest accepted name by edit distance when one is close
  (threshold `len/3`, clamped to 1..3), else list accepted names sorted.
- **Out-of-range value:** append the parameter's own description so the caller learns what the
  accepted values select.
- **Aliases:** every accepted alias appears in the schema, or it does not exist.

**Seen in the wild:** a fix landed at the request-boundary guard only; the dispatcher's fallback
arm produced a second, untouched message — the one most callers actually hit. An existing test
caught it. Derive the shared text from one function so the two cannot drift.

**Before optimizing this,** see Part 3 — the cost is linear in supplied keys and was measured
well below the complexity of a cache.

## B8 — Protocol contract completeness

Everything above asks whether the *inputs* are well described. A caller also has to predict
what comes back and whether calling is safe. Four declarations answer that, on any surface:

| Declaration | What its absence costs | Where it lives |
|---|---|---|
| **Strict input** — unknown fields rejected | a misspelled argument succeeds silently | `additionalProperties: false`; `deny_unknown_fields`; `extra="forbid"` |
| **Output shape** — the response contract | callers parse defensively or guess | `outputSchema`; OpenAPI `responses`; a return type |
| **Effect class** — read-only, destructive, idempotent | every call is treated as dangerous | MCP `annotations`; HTTP method semantics |
| **Display name** distinct from the identifier | cosmetic only | `title` |

**None of this is new, and the prior art is the argument.** HTTP settled the same two problems
decades earlier: `GET` is *safe*, `PUT` and `DELETE` are *idempotent*, `POST` is neither — the
effect class is carried by the method itself. And `4xx` versus `5xx` is exactly the split below.
A surface that omits these is not choosing simplicity; it is discarding a distinction its
protocol already offers.

**Effect class is the safety-relevant one, and defaults decide who pays.** Where a protocol
supplies defaults, they usually assume the dangerous case — MCP defaults `destructiveHint` to
**true**, so omitting the block *asserts* the tool is destructive rather than leaving it
unknown. The cost then lands on the read-only majority, which a conforming client must treat
like a delete. Check the defaults for your surface before assuming absence means "unspecified".

**Use the right error channel.** Every protocol separates *you called it wrong* from *the call
was valid and failed* — JSON-RPC `-32602` versus a result flagged as an error; HTTP `4xx`
versus `5xx`; an exception type versus a returned error value. Conflating them denies the
caller the one fact that decides whether retrying can help.

Trigger four cases and check which channel each takes: unknown tool or method, unknown
parameter, out-of-range value, and a valid call whose operation fails.

For MCP's exact spellings, hint defaults, and a live-server audit recipe:
`references/mcp-specifics.md`.

---

## Group A — Is the text right?

## A1 — Availability

Does every parameter named inside a description or error exist on *that* entry point, settable
by *that* caller? Highest-yield text pass: helpful cross-references get written from memory of
the whole API, but each entry point exposes a subset.

```bash
rg -o '`[a-z_]{3,}`' path/to/schema.rs
```

For each hit, find the enclosing tool, list its real parameters, confirm membership. A shared
validation helper may only name parameters common to every caller it serves.

**Seen in the wild:** a shared paging validator named three parameters that exist on none of the
four query types it served — they belonged to *methods* and to a different tool.

```
- limit must be 0 or greater, got -5; pass a positive count, 0 for every match,
  or use lines_per_message, transcript_lines, or summary_items, which take negatives
+ limit must be 0 or greater, got -5; pass a positive count, or 0 for every match
```

Shorter *and* correct. The redirect was kept only in the one schema where both parameters
genuinely coexist.

**Beware false positives.** In one audit this pass flagged 11 candidates and 10 were English
words colliding with parameter names: "explicit **offset**" (a timezone), "**limit** each
returned message" (verb), "with **context**" (noun), "auto-generated **summary** messages"
(adjective). Verify each; never bulk-edit grep output.

**Do not invert that into skipping verification.** A 10-out-of-11 false-positive rate is a
fact about short parameter names being common English words, not a licence to dismiss the
batch — the single real finding in that audit was the most consequential defect in the
release. Cheap to check, expensive to miss: check all of them.

## A2 — Semantic duplication

Do two parameters mean the same thing under different names, or does one name mean different
things on different tools?

List every parameter across every tool and group by meaning rather than by spelling. Duplicates
hide behind synonyms: `limit`/`max_results`/`count`, `path`/`directory`/`root`,
`verbose`/`debug`, `filter`/`query`/`match`. Each pair forces callers to learn which is which,
and the two drift as one gains features the other lacks.

```bash
# Group declared parameters by name to find one concept spelled several ways,
# and one spelling used for several concepts.
rg -o '"[a-z_]{3,}"\s*:\s*\{' schema.json | sort | uniq -c | sort -rn
```

**Fixes, in order of preference:** extend the existing parameter; alias the new name to it and
declare the alias; or, if both must exist, state in each description how it differs from its
near-twin. Shipping two undifferentiated names is the option to avoid.

The mirror defect is one name meaning different things on different tools — worse, because a
caller who learned it once is now confidently wrong. A shared name with a different enum per
tool is the mechanically detectable form. **It is also a candidate, not a finding**, and the
worked example below is why.

Measured on one server, `mode` carried seven distinct value spaces:

| Tool | `mode` accepts | What `mode` selects |
|---|---|---|
| `search_graph` | `full`, `summary` | detail level |
| `search_code` | `compact`, `full`, `files` | detail level |
| `get_code` | `full`, `signature`, `head_tail` | detail level |
| `get_code_snippet` | `full`, `signature`, `head_tail` | detail level |
| `index_repository` | `full`, `moderate`, `fast`, `cross-repo-intelligence` | detail level |
| `trace_path` | `calls`, `data_flow`, `cross_service` | **which edges to follow** |
| `manage_adr` | `get`, `update`, `sections` | **which action to perform** |

**The first reading was wrong.** Reading the descriptions shows `full` means *the maximal,
least-reduced variant* in all five: individual not aggregated, snippets included not
deduplicated, whole source not signature, all files not filtered. "full = give me everything"
transfers correctly every time. Flagging it would have been a false positive.

**The real defect is a level up:** `mode` selects three *kinds* of thing — detail level (five
tools), edge selection, action verb. Only the last two break the convention, and the action
verb is the hazard: `manage_adr` with `mode="update"` **writes**, so a mutation hides behind a
name promising a view setting, on a tool with no `destructiveHint` (B8).

**The test to apply, before calling any shared name overloaded:** do the shared *values* carry
a consistent meaning? If yes, the convention holds and the differing enums are fine. If no,
rename the outliers — `traversal_edges`, `action` — and leave the convention alone.

The same applies to descriptions: one name described differently across tools is a candidate.
Sometimes it is legitimate per-tool tailoring, sometimes two concepts wearing one name. Read
them before deciding.

### Check the whole function too, not only its parameters

Two tools that do the same job are the same defect one level up, and more expensive: callers
must choose before they can call, and the two diverge in capability over time. Ask what task
each tool completes, in one sentence, and look for sentences that match.

**The tell is a parameter whose value selects behavior another tool already provides** — the
seam where two overlapping tools were welded together. Observed: `search_code` accepted
`search_in: "graph"`, the job `search_graph` exists to do, and resolved the overlap by silently
running a source search. The duplication and the silent substitution (B1) were one defect.

Signals worth a closer look, each of which may be legitimate — decide, then record why:

- A tool that is another tool plus a mode flag.
- Several tools enumerating the same collection through different filters.
- A `type`, `mode`, `kind`, or `search_in` parameter whose branches have little code in common.
- Two tools whose descriptions differ only in adjectives.

**Fixes:** merge and keep the mode parameter; or keep both and have each description name the
other and say when to prefer it. What must not survive is an overlap resolved by silently
picking one, and the choice going unstated where the caller reads.

## A3 — Grammatical attachment

In "a, b, and c, which take negatives" — does the clause attach to `c` or to all three? Any list
followed by a relative clause, participle, or trailing qualifier is a candidate. Rewrite or
split. English will not disambiguate this and readers split roughly evenly.

```bash
rg ', which |, that |, taking |, accepting ' path/to/
```

## A4 — Negation

Ban `no negative`, `not negative`, `non-negative`, `never`, `cannot be`, `must not`,
`don't pass`. Each states what is forbidden and leaves the accepted set implicit; double
negatives invert under paraphrase.

```bash
rg -i "no negative|not negative|non-negative|never |cannot |must not|don't pass" path/to/
```

Restate as accepted values: `"0 or greater"`, `"one of: user, assistant, system"`. A genuine
mutual exclusion may say so, but must still name the way forward — `"--seq selects one message
by sequence, so it takes only --context; drop --role and --limit, or omit --seq to filter a
range"`.

## A5 — Value-space completeness

Run the Disambiguation Checklist in `SKILL.md` against every parameter; Part 1 holds the
reasoning behind its questions. The minimum for a signed integer is four facts: positive,
negative, zero, omitted. Report as a fraction.

**Seen in the wild:** guidance claimed `-5` "probably meant lots of results". Three sibling
parameters already established positive = first N, negative = last N, `0` = all — so `-5` means
the *last five* and `0` means "lots". Check siblings before inventing semantics.

## A6 — Default drift

Does every declared default appear in the human-readable text? A default living only in a schema
`default` key or a function signature is invisible to a caller reading prose.

```bash
rg '"default"|= None|unwrap_or' path/to/
```

State it in words: "defaults to none", "defaults to false".

## A7 — Vague qualitative words

```bash
rg -i "reasonable|appropriate|large|fast|efficient|as needed|properly"
```

Each is guidance shaped like a fact. **Seen in the wild [independent]:** a user rejected
"reasonable computational cost" in a schema and required "effective and computationally
efficient". Replace with a threshold, a measurement, or delete.

## A8 — Brevity, measured

Compute median and maximum description length; justify each outlier against the required facts.
A median near 150 characters is a healthy shape.

**The median is a diagnostic, not a target** — it says "look at the outliers", not "make
everything 150 characters". Padding a short description that already carries its facts, or
trimming a long one that needs them, turns a measurement into damage. The standard is
two-sided: every fact present is required, and every required fact is present.

Cut: restatements of the type, filler, repetition of the parameter's own name. Never cut: an
accepted value, a special-value meaning, a default, a unit, or an interaction.

**Order matters more than length.** Readers and context-compactors truncate tails, so the
distinguishing fact goes first.

**Restatement is not automatically duplication.** It earns its place whenever the reader is in
a different context and cannot see the original:

- An **error message** restates the description's facts, because the caller is not reading the
  schema at the moment it fires.
- An **always-loaded checklist** restates the reference, because the reference may never be
  opened.
- A **presentation parameter** restates what it does *not* affect (Part 1 §4), because the
  reader's default assumption is wrong.

The test is not "do these words appear twice?" but "will the reader have the other copy in
front of them?" Cut only when the answer is yes — deleting a restatement because it exists
elsewhere is how an always-loaded document loses facts a reader needed. Made and reverted here.

**Seen in the wild:** a proposal to shorten several bounds to "natural numbers" was rejected —
whether `0 ∈ ℕ` is disputed, and `0` was load-bearing in every affected parameter. Brevity that
trades away precision on the special case is a regression.

An outlier legitimately survives when it carries irreducible facts: one 504-character
description (3.4× the median) stated three sign cases, what the parameter does *not* affect,
its ordering relative to another parameter, and a contrast against a sibling tool.

## A9 — Unstated assumptions

Does the text presuppose anything the reader does not have *at the point they read it*?

Read each description as a newcomer who has seen nothing else — no other parameter, no source,
no conversation, no prior call. Everything the text leans on must either be present or be
reachable from what it names.

Five forms, in rough order of frequency:

| Form | Example | Why it breaks |
|---|---|---|
| **Forward reference** | "duplicates at both levels" before the levels are named | The reader has no model to attach it to |
| **Undefined term** | "the catalogue", "the seam", "streamlined mode" | Project vocabulary that reads as ordinary English |
| **Dangling deixis** | "this", "the other one", "as above", "the same way" | No antecedent survives out of context |
| **Assumed reading order** | "unlike the previous parameter", "see the `query` description" | Declarations reach the caller individually, in no fixed order |
| **Assumed access** | "check the dashboard", "see the config" | The reader may hold neither |

**Assumed reading order is broken by construction wherever declarations are surfaced one at a
time** — a tool description in a schema, a hover tooltip over one parameter, a `--help` entry, a
man-page flag, a docstring rendered for a single function. There is no "previous" parameter and
no "above". Any comparison must name its target in full — `get_session transcript_lines`, not
"the windowing parameter mentioned earlier".

```bash
# Deixis and ordering assumptions inside description strings.
rg -o '"description"[^"]*"[^"]*\b(as above|as described|see below|the previous|the other|likewise|similarly|this one)\b' schema.json
```

Grep finds the ordering forms. Forward references and undefined terms need a cold reader,
because they look fine to anyone who already knows the answer: **you cannot detect an unstated
assumption using the knowledge that makes it invisible.** Hand the text to someone without
project context and ask what they cannot answer.

Found while writing this document, by running this pass on it: "duplicates at both levels"
(levels never named), "Group B outranks Group A" in an introduction that never says what the
groups are, and a bare "see A8" pointing into a file the reader had not been told to open.

---

## Process Failures to Expect

Not about the parameters — about reviewing them. All observed.

| Failure | Correction |
|---|---|
| **Test hit a different guard** than intended, passing while proving nothing. **[independent]** — hit twice in one week on unrelated codebases. | Confirm the red test fails *for the expected reason*. |
| **Assumed red meant the code was wrong.** A boundary test failed because the assertion miscounted, not because the implementation misbehaved. "Fixing" the code would have widened a threshold that was already correct. | On red, decide *which* is wrong before changing either. Recompute the expected value by hand. |
| **Suppressed stderr, then read the empty output as a result.** `cmd 2>/dev/null` turned `error: unexpected argument` into zero rows, and the corpus was briefly declared empty. Nearly filed as a product bug. | Never discard stderr while investigating. An empty result and a swallowed error are indistinguishable — this document's own pass B5 applied to your shell. |
| **Reviewed a stale artifact.** The installed binary predated the branch by four days, so findings described a schema already fixed. **[independent]** — the same week, another agent noted "the optimized executable predates the current validation change". | Check the artifact's build date against your last commit before acting on any finding. |
| **Tested at a layer that skips validation** — direct dispatch bypasses the schema entirely. | Test at the layer that actually validates. |
| **Fixed one of two code paths.** | Grep every site producing that error class; derive shared text from one function. |
| **Hardcoded a list that must stay in sync** — "unknown tool" listed served tools as a literal. | Build from the registry the dispatcher reads. |
| **Claimed two files diverged without diffing** — the entire delta was an uncommitted local edit. | Diff against committed state before reporting drift. |
| **Blamed the tool before re-reading the invocation** — a flag placed after `--` instead of before it. | Reproduce against the documented form first. |
| **Deferred a fix as "disproportionate at release time"** with nothing yet published. | Before first publish there is no compatibility surface to protect. |
| **Treated grep hits as findings.** | Verify each candidate individually. |
| **Promoted a mechanical candidate without testing its premise.** Seven differing `mode` enums were called a defect; reading the descriptions showed the shared value `full` meant the same thing in all five tools that used it. The real defect was one level up and much narrower. | A structural signal answers "are these different?" — not "does the difference harm the caller?" Answer the second before reporting. |

---

## Reporting

Report open findings rather than dropping them. A pass with an unresolved finding is a result;
silently omitting it is not.

**A filled template is not evidence that the passes ran.** Every number must come from a
command you executed or a file you read. An unmeasured count is a fabrication, and worse than
no report because it ends the review. `not run` is a legitimate entry, and the only honest one
for a skipped pass.

**The grep commands are starting points, not the check.** Each assumes a convention: backticked
parameters, descriptions in one file, a matching language. Zero hits from a command written for
another codebase means the command did not fit — adapt the pattern and re-run.

```
B1 Silent ignore         62 params traced, 62 reach behavior
B2 Schema drift           0 implemented-undeclared, 0 declared-unimplemented
B3 Composition            3 pairs checked, 3 restrictions documented
B4 Examples               9 extracted, 9 executed in CI
B5 Result-set honesty     echo: NO (open)   truncation: yes   ordering stated: NO (open)
B6 Cross-surface          parity confirmed via CLI, MCP schema, Python binding
B7 Unknown names          2 dispatch paths checked
B8 Protocol contract      additionalProperties 7/7, outputSchema 4/7, annotations 0/7

A1 Availability          11 candidates → 1 real
A2 Semantic duplication   1 concept spelled 5 ways, 1 name reused for 3 concepts
A3 Attachment             1 candidate  → 1 real
A4 Negation               3 → all fixed
A5 Value-space            4/4 signed params complete
A6 Default drift          2 → both fixed
A7 Vague words            0
A8 Brevity                median 148 chars, max 504 (lines_per_message), justified
A9 Unstated assumptions   3 forward refs, 0 undefined terms, 0 ordering assumptions
```

## Regression-lock shape

Assert the *absence* of banned phrasing, not only the presence of the good phrasing.

```python
@pytest.mark.parametrize("query_type", [SessionQuery, MessageQuery, AnalysisQuery, FileQuery])
def test_negative_limit_names_what_to_pass_instead(query_type):
    with pytest.raises(ValueError) as excinfo:
        query_type(limit=-5)
    message = str(excinfo.value)

    assert "limit" in message                    # names the parameter
    assert "0 or greater" in message             # states the bound as accepted values
    assert "-5" in message                       # quotes the offending value
    assert "0 for every match" in message        # says what 0 selects

    assert "no negative" not in message          # regression lock: double negative
    assert "not negative" not in message
    assert "lines_per_message" not in message    # regression lock: unavailable parameter
    assert "transcript_lines" not in message
```

The four `assert ... not in` lines are the ones that keep the fix fixed.

---

# Part 3 — Solving It For The Caller

## The Remediation Ladder

Ordered from most to least helpful. Climb as high as is *safe*, not as high as is possible.

| Rung | Response | When it is right |
|---|---|---|
| 1 | **Accept and proceed silently** — treat the input as the intended one | Only when the mapping is unambiguous, documented, and harmless if wrong. A declared compatibility alias qualifies. A guess never does. |
| 2 | **Accept, and say what was assumed** | Same as rung 1 but the mapping is newly introduced, deprecated, or lossy. Echo the effective value. |
| 3 | **Reject, naming the single likeliest fix** | The default. One candidate is clearly closest. |
| 4 | **Reject, listing accepted values** | Nothing is close enough to single out, or several tie. |
| 5 | **Reject bare** | Never. |

**Rung 1 is the trap.** Silently correcting an input the caller did not write is pass B1's
silent-substitution defect wearing a friendly face. The test: could the caller reasonably have
meant something else? If yes, drop to rung 3.

**Rungs 3 and 4 are one message, not two.** Lead with the suggestion, then still list the
catalogue — a caller whose guess was wrong recovers from the same response instead of making a
second discovery call.

```
unknown tool: search_message — did you mean "search_messages"? this server
provides "search_sessions", "get_session", "list_sessions", …
```

**Never invent a suggestion to fill the slot.** A confidently wrong pointer is worse than none:
it converts one retry into a detour. Test the far-miss case explicitly, asserting no suggestion
appears.

---

## Choosing an Algorithm

| Algorithm | Good for | Watch out |
|---|---|---|
| **Levenshtein** (insert/delete/substitute) | The safe default for identifiers | Counts a transposition as 2 edits |
| **Damerau-Levenshtein** (adds transposition) | Typos specifically — `limti`→`limit` costs 1, not 2 | Slightly more code; worth it for hand-typed input |
| **Jaro-Winkler** (weights a common prefix) | Human names, free-text fields | Ranks correctly but compresses the gap on namespaced APIs — see below |
| **Trigram / n-gram** (`pg_trgm`, `difflib` ratio) | Longer strings, fuzzy search over descriptions | Weak on short identifiers |
| **Soundex / Metaphone** (phonetic) | Spoken input, name lookup | Meaningless for identifiers — avoid |

**The Jaro-Winkler trap.** It boosts scores for shared prefixes — exactly what a namespaced API
has everywhere. It still *ranks* correctly; the problem is that it compresses the gap, so any
threshold loose enough to catch typos admits the wrong candidate too. Measured for the typo
`search_message`:

| Candidate | Levenshtein | Jaro-Winkler |
|---|---|---|
| `search_messages` (correct) | **1** | 0.987 |
| `search_sessions` (wrong) | **5** | 0.856 |

Levenshtein separates 1 from 5, so a threshold of 3 admits one and rejects the other. Both
Jaro-Winkler scores sit above any usual cutoff. Python's `difflib.get_close_matches`, which is
ratio-based, returns *both* for this input. For prefix-clustered names — `get_*`, `list_*`,
`search_*` — prefer plain Levenshtein precisely because it does not reward the shared part.

### Threshold, tie-breaks, and cost

- **Scale the threshold with length, then clamp.** `len/3` clamped to `1..3` works well: short
  names do not collide with unrelated short names, long names tolerate the extra slip a long
  word invites, and the clamp stops a 30-character name from matching almost anything.
- **Break ties deterministically** — by distance, then shortest candidate, then lexically. One
  typo must never produce different suggestions across runs.
- **Do not optimize before measuring.** Validation is `Ω(k)` in supplied keys and cannot be
  sublinear. A full typo-rejection round trip — JSON parse, schema parse, validation, error
  serialization, pipe I/O — measured 49.8–61.7 µs median over 900 calls. A linear scan over a
  few hundred candidates is not the bottleneck. Reach for a **BK-tree** only with a large
  dictionary (thousands of terms) where you have measured the scan mattering.

---

## Libraries by Language

Verify the exact API against current docs before use — versions and function names move.

| Language | Distance library | Framework support |
|---|---|---|
| **Rust** | `strsim` (Levenshtein, Damerau, Jaro-Winkler, normalized variants) | `clap` suggests near-miss flags automatically |
| **Python** | `difflib.get_close_matches` (stdlib, ratio-based); `rapidfuzz` (fast, C++ backed) | CPython itself suggests for `NameError`/`AttributeError`; `click` via `click-didyoumean` |
| **Go** | `agext/levenshtein`; `lithammer/fuzzysearch` | `cobra` has built-in command suggestions with a configurable minimum distance |
| **JS/TS** | `fastest-levenshtein`, `leven`, `didyoumean2` | `commander` exposes suggestion-after-error |
| **Ruby** | `did_you_mean` — in the standard library | Wired into `NoMethodError`/`NameError` by default |
| **Java** | Apache Commons Text `LevenshteinDistance` | `picocli` suggests near-miss options |
| **C / C++** | Hand-rolled is ~25 lines; no dependency needed | — |
| **SQL** | Postgres `pg_trgm` (`similarity`, `%` operator), `fuzzystrmatch` | — |

**Prefer the framework's built-in.** If the CLI parser already suggests, its behavior is tested,
localized, and consistent with the ecosystem. Add a hand-rolled pass only for the surfaces the
framework does not cover — typically MCP tool names, JSON keys, and config file keys.

**Cover every surface once you have the helper.** Extract one `nearest_name(name, candidates)`
and call it from each surface. Two copies of a threshold drift; one cannot.

---

## Step Zero: You Cannot Suggest What You Never See

Before any of the above can run, the system has to *notice* the unknown input. Most
serialization defaults do not — they discard it silently, so the suggestion code is never
reached and the caller gets a successful-looking call that ignored their argument.

| Layer | Default for an unknown key | Make it reject |
|---|---|---|
| JSON Schema / MCP | **Allows** (`additionalProperties` defaults to true) | `"additionalProperties": false` |
| Rust `serde` | **Ignores** | `#[serde(deny_unknown_fields)]` |
| Python `pydantic` v2 | **Ignores** (`extra="ignore"`) | `extra="forbid"` |
| Go `encoding/json` | **Ignores** | `Decoder.DisallowUnknownFields()` |
| Java `Jackson` | Rejects | already strict — leave it |
| CLI parsers (`clap`, `argparse`, `commander`) | Rejects | already strict |
| Env vars | **Ignores**, universally | nothing built in — see below |
| YAML/TOML config | **Ignores** in most loaders | loader-specific strict mode |

The pattern: **argument parsers are strict, serializers are permissive.** So a CLI catches a
typo while the same product's JSON, config, and env surfaces silently drop it — one concept,
two behaviors, four surfaces, one codebase.

Closing this is prerequisite work, and it pays a second dividend: tightening the policy
surfaces every parameter that was implemented but undeclared. Expect failures, and read them
before rolling back — one project's strictness change produced seven, all pre-existing drift.

## What The Language Lets You Do At All

Where the mistake is caught determines whether you can help, and the type system matters less
than the boundary.

| Where the name is bound | Who reports the mistake | Your leverage |
|---|---|---|
| Compile time (Rust struct field, Go field, typed kwargs) | The compiler or IDE | **None at runtime** — the name itself is your entire guidance |
| Runtime, in-process (Python `**kwargs`, JS options object, Ruby hash) | You, if you check | Full ladder available |
| Across a boundary (CLI, JSON, MCP, HTTP, config, env) | You, always | Full ladder — **and mandatory** |

The boundary row surprises people: **a statically typed program is dynamically typed at its
edges.** A Rust MCP server has compile-checked structs internally and untyped JSON arriving from
the client, so the type system helps with neither detection nor message — which is why such a
server still needs hand-rolled suggestions.

### Where suggestions are structurally impossible

- **Positional parameters** — there is no name to misspell, so nothing to suggest. A caller who
  swaps two same-typed arguments gets silence from every layer. The only defenses are keeping
  arity low, ordering by likely-to-differ types, and naming the *function* so the order is
  implied. Applies to C, Go, and anything called positionally.
- **Languages without keyword arguments** (C, Go, Java, most JS call sites) — options arrive as
  a struct or object. Static languages catch a misspelled field at compile time, so no runtime
  message is possible or needed; JS silently yields `undefined`, so it needs a runtime check.
- **Environment variables** — no ecosystem validates these by default. `MYAPP_TIMEOOUT` is
  ignored everywhere, always. If you read env, enumerate the recognized names at startup, warn
  on unrecognized ones sharing your prefix, and offer a `--print-config` that shows every
  effective value with its source. That is the only recovery path this surface has.
- **Shell and template expansion** — no introspection is available. Validate after expansion.

When suggestion is impossible, the effort moves entirely to naming, arity, and startup
validation. Those are the rungs you have left.

## Where Suggestions Are Not The Answer

Edit distance bridges typos, not vocabulary. It will never map a CLI's `--regex` to an MCP
`match_mode` — different words for one concept, at a distance no threshold should accept.

When two surfaces deliberately spell a concept differently, the fix is a small explicit alias
table consulted *before* the distance fallback. Build it only from observed misuse — a
speculative alias table is a second vocabulary to maintain.

The same limit applies to structural mistakes: a caller who nests a key one level too deep, or
passes an array where an object belongs, needs a type-shaped message (`expected an object with
keys a, b; got an array`), not a nearest-name hint.
