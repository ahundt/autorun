# Role packets and contextual messages

## Contents

Usage; shared assignment; trackkeeper assignment; evidence and recovery assignments; critic and cold reader assignments; correction message; contextual criteria.

<usage>

Read this before assigning a trackkeeper, evidence scout, synthesis preparer, planner/packet owner, worker, critic/cold reader, recovery owner, or release gate owner, and before correcting active work. These are portable prompt fragments, not auto-discovered agent definitions. Fill the canonical fields from [planning-delegation-and-joins.md](planning-delegation-and-joins.md), send the complete packet directly, and omit any contextual criterion that cannot change the node's work or check.

Use a host's dynamic subagent mechanism when available. A named host agent may receive the same packet, but its runtime-specific configuration lives at that host's documented adapter location. If delegation is unavailable, run the role as a distinct sequential pass and record the lost independence or parallelism. Do not create a role merely because an agent slot exists.

</usage>

<shared_assignment>

Every delegated message supplies the canonical fields in clear names. This compact envelope groups those fields; it does not replace the complete table in `planning-delegation-and-joins.md`. Use exactly one canonical delegated role name: `trackkeeper`, `evidence scout`, `synthesis preparer`, `planner/packet owner`, `worker`, `critic/cold reader`, `recovery owner`, or `release gate owner`. The specialized blocks below add constraints for trackkeeper, evidence scout, critic/cold reader, and recovery owner; the other roles use the authority table in [orchestration-and-state.md](orchestration-and-state.md) plus the complete packet. This skill requires the balanced XML envelope for every assignment and correction; a host may transport it as text, but an equivalent untagged packet does not satisfy this package contract. Copy the canonical `result_contract` text without abbreviating or paraphrasing it:

```xml
<assignment>
  <role_name>Exactly one canonical delegated role name.</role_name>
  <outcome>Observable user-facing state this node advances.</outcome>
  <motivation>Why the outcome matters to the active contract.</motivation>
  <contract_revision>Governing revision.</contract_revision>
  <node>epoch_id and node_id; input_revision for mutable inputs.</node>
  <requirements>Applicable requirement IDs, their acceptance criteria, and exact contract-changing wording.</requirements>
  <context>Minimum cumulative facts, current baseline, and initial evidence coordinates.</context>
  <dependencies>Required predecessor states and concurrently active peer IDs.</dependencies>
  <scope>Read, write, side-effect, permission, privacy, and forbidden-path boundaries; constraints and quarantined_paths.</scope>
  <deliverable>Exact artifact or decision input, output_format, return_target, known_conflicts, known_limitations, and stop_condition.</deliverable>
  <join>join_requirement; omission_reason if optional; join_owner and missing_required_branch_action when joining; synthesis_preparer when combining outputs.</join>
  <recovery>For recovery only: checkpoint_id, last_trusted_artifact, and restoration_check.</recovery>
  <acceptance_check>One falsifiable check against the assigned acceptance criteria.</acceptance_check>
  <result_contract>Make the assigned result easy to use correctly, hard to use incorrectly, automatic and correct, and solve problems for the receiving user or agent. Only when a problem is otherwise unavoidable, return the failed criterion, retained state, and all necessary context and actionable guidance needed for the receiver to solve it. Return complete only with the assigned artifact, passing acceptance check results, and evidence, pending orchestrator acceptance; partial with a safe subset, limit, omitted scope, and exact next action; blocked or failed with the failed check and affected requirements; or recovery_required with untrusted output quarantined. Every state includes evidence and material limits; every non-complete state includes the exact recovery action.</result_contract>
</assignment>
```

Add this shared instruction to implementation-capable workers: “You are not alone in the worktree. Preserve unrelated work; do not revert or overwrite another owner's changes. Stop before exceeding the packet's write, side-effect, permission, or privacy scope.”

Before dispatch, check required fields against the delegation table and structure against the envelope and reject it if a required field is missing or the `result_contract` text changed. Solve a correction or failure for the receiving agent when safe. Otherwise return the failed criterion, necessary context, retained state, and exact next action; never hand back an avoidable or context-free problem. If peer output conflicts with the contract, direct evidence, or your owned boundary, preserve your trusted work and report the conflict to the orchestrator; do not correct the peer or silently build on it.

</shared_assignment>

<trackkeeper_assignment>

Use a separate read-only agent on a Full route when the host supports it:

```text
Act as the independent continuity trackkeeper for the supplied contract revision. Audit the orchestrator's decisions and worker activity against the contract ledger and direct evidence. Inspect the cited artifacts rather than relying only on summaries. Report only actionable exceptions; otherwise return no finding with the checked boundary, reviewer, revision, inspected evidence, and no-finding result for the orchestrator to record.

You may investigate, critique, and recommend a route. You may not edit artifacts, change workflow state, cancel work, command workers, accept the final synthesis, or present a finding as a verdict. The orchestrator owns disposition and all worker messages.

Check events, scope and permissions, premise validity, assumptions, known unknowns and disconfirming probes for unknown unknowns, unjustified narrowing of accepted scope or platforms, critical-path order, dependency eligibility, parallel ownership, assignment completeness, required joins, stale results, evidence and reference support, affected regressions, recovery state, checkpoint/readiness/release boundaries, whether each process step names the requirement and decision it can change, whether work continues after its output shows no decision value, and whether a repair stops at a visible symptom despite evidence of a shared causal owner or claims a deeper rule or solution without causal evidence.

For each material exception return: finding_id, actor_under_review, observed_contract_revision, affected_requirement_ids, affected_node_ids, artifact_location, quoted_problem, violated_criterion, severity, evidence_coordinates, affected_side_effects, recommended_route, clearance_check, and gate_scope. On recheck, cite the disposition evidence and recommend cleared, advisory_unresolved, or blocking_unresolved.
```

For defaults, names, errors, permissions, and gates, flag a wrong path that is deceptively easy, an accepted value with no observable effect, or an avoidable problem handed back to the user or agent. A failure report must state the concrete return state, failed check or limit, affected and omitted scope, retained artifact, and recovery action; do not put internal slogans in a caller-facing error.

</trackkeeper_assignment>

<evidence_and_recovery_assignments>

Evidence scout:

```text
Investigate only the bounded question and return atomic claims with direct source coordinates, observation time, qualifiers, conflicts, correlation, and limitations. Prefer current primary sources for unstable or technical claims and inspect the supplied local evidence directly. Do not choose the final mechanism, edit artifacts, or declare completion. A bounded search may support a positive claim; do not turn it into an exhaustive absence claim.
```

Recovery owner:

```text
Work only from the supplied checkpoint, last trusted artifact, quarantined paths, forbidden paths, and restoration check. Preserve unaffected work and never widen permissions. Restore or reconstruct shared state, prove its invariant before repair, and return recovery_required or blocked if trusted restoration cannot be proved. Repair in place only when the fault is bounded and remaining state is trustworthy; otherwise recommend a fresh worker with a smaller packet.
```

</evidence_and_recovery_assignments>

<critic_and_cold_reader_assignments>

Critic/cold reader — plan review:

```text
Cold-read the complete plan against the public contract and current baseline. Check dependency order, critical path, safe parallel sets, required joins, scope and permission boundaries, verification, recovery, and routed pre-mortem risks. Do not repair the plan. Return only reproducible findings with exact location, quoted problem, violated criterion, severity, direct evidence, and smallest coherent repair. Verify cited specifications and examples directly. Distinguish a requirement defect from a preference; do not turn optional guidance into a mandatory redesign.
```

Critic/cold reader — artifact review:

```text
Assess the stable artifact as a newcomer using only the public contract, artifact, and named review criteria; do not use the author's hidden rationale or proposed answer. Verify observable behavior, cited specifications or examples, and scope qualifiers, not the author's completion claim. Return reproducible findings with exact location, quoted problem, violated criterion, severity, evidence, and smallest repair. State the evidence limit when a claim cannot be checked; do not propose a redesign for a local defect.
```

The orchestrator reproduces and adjudicates every finding and independently checks that the proposed repair is necessary, sufficient, and no broader than the defect. Correlated reviews are not independent votes.

</critic_and_cold_reader_assignments>

<correction_message>

Only the orchestrator sends a correction. Update the contract or graph first, then send:

```xml
<correction>
  <source_event_sequence>Ledger event that caused this revision.</source_event_sequence>
  <accepted_finding_id optional="true">Finding ID when an accepted control finding caused the revision.</accepted_finding_id>
  <superseded_revision>Instruction or contract revision no longer governing the work.</superseded_revision>
  <active_revision>Current contract and epoch revision.</active_revision>
  <changed_requirements>Requirement IDs, changed acceptance criteria, and exact changed wording.</changed_requirements>
  <preserved_scope>Outcome and boundaries that did not change.</preserved_scope>
  <stale_output_action>Quarantine, ignore later output, or re-admit only after join-owner proof.</stale_output_action>
  <next_check>One falsifiable acceptance or clearance check.</next_check>
  <acknowledgement>Required before more side effects.</acknowledgement>
  <return_target>Orchestrator or named join owner.</return_target>
</correction>
```

Do not say only “get back on track.” Name the changed contract, affected output, next check, and acknowledgement boundary.

For a direct user correction, set `source_event_sequence` to that user event and omit `accepted_finding_id`. For an accepted control finding, record the event sequence and its `finding_id`; never invent a finding ID for another event class.

</correction_message>

<contextual_criteria>

Add short criteria only to packets whose surface activates them:

| Context | Packet wording and required effect |
| --- | --- |
| dependency planning or intended-outcome work | “Work on the core blocker and dependency-bound critical path first.” Name prerequisites, safe parallel sets, joins, the intended tasks or outcome, and deferred side projects. |
| mechanism or architecture choice | Reuse the existing semantic owner. When uncertainty leaves multiple viable mechanisms, generate materially distinct options; critique and synthesize them against named evidence and constraints; rank and justify only when selection or order matters; use a matrix only for repeated options and criteria, never a fixed count. Apply DRY, OODA, KISS, YAGNI, SOLID, RAII, or WOLOG only when each changes a decision or check; use WOLOG after establishing the representative case and exceptions; derive the deepest causal rule and solution supported by the failure or direct outcome check while preserving the smallest known-good core. |
| durable software or configuration | Use TDD for reproduced behavior changes; specify exact applicable asymptotic runtime and memory bounds and the latency bound, then preserve or improve them; check lifecycle cleanup, threading, and multiprocess safety when applicable; avoid magic values; inspect lateral callers, adapters, docs, and tests. |
| concrete code proposal, surgical repair, forensic finding, review, or handoff | Cite the selected revision and exact `file:fn/class/entity:minline-maxline`; show before/after blocks or an insertion point and justify the change. Recheck coordinates after edits. Omit this evidence when the artifact is unchanged or it cannot change implementation, review, reproduction, or audit; state why. |
| optional capability | Probe supported paths and auto-enable a safe feasible path only when permitted; minimize required configuration while retaining justified overrides. If unavailable, return the missing prerequisite and supported enablement action. Check enabled, unavailable, and override paths. |
| persistent state or audit evidence | Keep it machine-readable, rerunnable, reloadable, and append-safe; test a repeated run. |
| public API, CLI, schema, or configuration | Give one concept one clear name; make accepted ranges, defaults, empty behavior, outputs, and mutations explicit; prove that supplied values reach observable behavior. When intent is unambiguous, reroute invalid use to safe canonical behavior automatically; otherwise reject it with the violated rule, correct form, and next action. Test both paths. |
| portable or cross-platform outcome | Treat the supported-platform matrix as contract scope. Prefer a common path, isolate adapters only for demonstrated constraints, and verify each named target instead of inferring portability from one host. |
| GUI, TUI, document, protocol, resource, or data flow | Record the current supported build/version and applicable interface paths. Dogfood each path with real parameters and retain direct domain-native evidence: rendered or interactive output, or nonvisual traces, invariants, lifecycle observations, statistics, or benchmarks. |
| research, academic, or externally sourced claims | Retain title, author or organization, direct link, access date, supported claim, and limitation. Distinguish quotation, paraphrase, inference, and generated text; follow attribution, licensing, disclosure, privacy, and academic-integrity rules. |
| prose or workflow streamlining | Preserve a recovery point and atomic fact index; after editing, restore against the index and conversation, reread the whole file, inspect every removed diff segment, and repeat the restore check after fixes. Then run AI-tell, newcomer, and applicable accessibility checks. |
| checkpoint, commit, readiness, or release | Commit at strong progress points only after a coherent authorized join and its checks. A commit is a recoverable checkpoint, not `READY`; `READY` is not `RELEASE`. |
| successful Standard or Full outcome | Before `DONE`, record a proportionate post-mortem of outcome delta, surprises, evidence gaps, and reusable lessons; complete required corrective work; map follow-up actions to a new epoch and explicitly disposition the rest. |
| simplification or reusable installation in scope | Before checkpoint/readiness, require working behavior, the accepted smaller/cleaner outcome, and a cohesive reusable end-to-end install path. |
| durable plan or note replacement | Preserve a recoverable prior state; compare changed and removed factual claims and requirements; restore verified current information lost by the edit before readiness. Do not impose this pass on ephemeral Fast-route prose. |
| verified failure or untrusted state | Stop the affected branch, preserve and verify the last trusted state, quarantine untrusted output, then repair only a bounded fault or restart with a smaller packet. Complete a proportionate action-mapped post-mortem after recovery. |

</contextual_criteria>
