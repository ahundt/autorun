# Orchestration roles and shared state

Read this before a Standard or Full route, or when acting as orchestrator, trackkeeper, synthesis preparer, or release gate owner. It defines authority, persistent state, and route scale; it does not replace a node's delegation packet.

## Logical roles and authority

The orchestrator may perform any workflow pass, including investigation, auditing, criticism, planning, implementation, verification, recovery, and adjudication. Role ownership identifies accountability or supplies an independent check; it does not prohibit the orchestrator from doing the same work. One agent may hold several roles on a small task. Separate agents are useful only when independence, specialization, or parallel breadth improves the result.

| Role | Owns | Cannot do silently |
| --- | --- | --- |
| orchestrator | contract, routing, task state, decisions, final response | widen scope, grant permission, accept unsupported worker claims |
| trackkeeper | independent audit of orchestrator and worker behavior; evidence-backed control findings and clearance recommendations | edit artifacts, command workers, cancel work, change state, record dispositions or recheck results, or treat its finding as a verdict |
| evidence scout | bounded sourced claims and limitations | choose the mechanism or declare completion |
| synthesis preparer | claim normalization, provenance, conflicts, branch completeness | accept the final synthesis, invent missing evidence, or replace user intent |
| planner/packet owner | dependency order, critical path, proposed node packets, joins | command workers, parallelize coupled writes, or omit required branches |
| worker | assigned artifact or evidence node and its check | exceed write/permission scope or overwrite peer work |
| critic/cold reader | reproducible findings against the public contract | repair its own finding or act as final authority |
| recovery owner | last trusted state, quarantine, resume/restart evidence | expand permissions or treat quarantined output as trusted |
| release gate owner | readiness evidence and verification of release authorization | confuse a checkpoint or `READY` result with `RELEASE` |

The orchestrator remains accountable when roles are delegated and is itself inside the trackkeeper's audit scope. Trackkeeper advice supplements rather than replaces or limits the orchestrator's own checks and decisions. On a Full route, use a dedicated read-only trackkeeper agent when the host supports delegation; its bounded assignment is to run the exception checklist at the defined boundaries, inspect direct evidence rather than only orchestrator summaries, and return structured findings and clearance recommendations. It never issues worker corrections or receives implementation work. If the host cannot supply that agent, the orchestrator runs the same checklist as a separate ledger pass and records the weaker independence. The trackkeeper is a logical lane, not a claim that a host can run a permanent background process.

Only the orchestrator dispatches or corrects workers. A runtime or harness may enforce a narrow affected-action gate on unresolved high or critical findings; that gate does not grant the trackkeeper dispatch authority. A one-shot independent adjudicator may resolve a disputed finding from evidence, but it owns no implementation, task state, synthesis, or user-facing decision.

## Shared state

Keep the ledger in the host's plan/task system or a note in an already permitted location when persistence matters. Otherwise keep it in-session and disclose that it will not survive compaction.

| Ledger area | Minimum fields |
| --- | --- |
| contract | `contract_revision`, `outcome`, `motivation`, `in_scope`, `out_of_scope`, `constraints`, `permissions`, `success_evidence` |
| requirements | `requirement_id`, `exact_wording`, `normalized_meaning`, `acceptance_criteria`, `source`, `state`, `dependency_requirement_ids` |
| evidence | `claim`, `evidence_type`, `evidence_coordinates`, `observed_at_rfc3339`, `qualifiers`, `limitations`, `correlation_group` |
| decisions | `candidate_mechanisms`, `selected_mechanism`, `selection_reason`, `invalidating_conditions` |
| nodes | `node_id`, `owner`, `read_scope`, `write_scope`, `input_revision`, `dependency_node_ids`, `output_artifact`, `acceptance_check`, `accepted_revisions`, `acceptance_evidence`, `join_requirement`, `execution_state`, `result_state`, `failure_reason`, `omitted_scope`, `recovery_action` |
| findings | `finding_id`, `actor_under_review`, `observed_contract_revision`, `affected_requirement_ids`, `affected_node_ids`, `artifact_location`, `quoted_problem`, `violated_criterion`, `severity` (`critical`, `high`, `medium`, `low`), `evidence_coordinates`, `affected_side_effects`, `recommended_route`, `clearance_check`, `gate_scope`, `disposition`, `disposition_evidence`, `recheck_result`, `repair_node_id` |
| gates | `criterion`, `required_evidence`, `result` (`pass`, `fail`, `blocked`, `deferred`), `requirement_ids` |
| events | monotonic `event_sequence`, `event_class`, `contract_quote`, `affected_requirement_ids`, `route`, `stale_node_ids` |
| recovery | `checkpoint_id`, `last_trusted_artifact`, `quarantined_paths`, `recovery_decision` (`repair`, `restart`, `blocked`) |

Execution states are `pending`, `active`, `paused`, `done`, `stale`, `blocked`, and `superseded`. A correction changes execution state before it changes prose.

Result states are `complete`, `partial`, `blocked`, `failed`, and `recovery_required`. Execution state answers whether a node ran; result state answers what downstream work may trust. Never infer completeness from `done`. Only the orchestrator accepts a result against its assigned acceptance criteria, recording contract, input, and output revisions in `accepted_revisions` and proof in `acceptance_evidence`. These fields remain empty until acceptance; invalidate them and recheck when those requirements or artifacts change.

Reuse an existing PRD and `REQ-*`/`BUG-*` identifiers when present; do not create a parallel source of truth or add a tracker to the fast route.

## Scale router

| Signal | Fast | Standard | Full |
| --- | --- | --- | --- |
| ownership | one | one causal owner; optional scout/critic | orchestrator plus only earned logical roles |
| dependency shape | one change/check | explicit ordered list | directed work cycle with acyclic per-epoch prerequisites and required joins |
| evidence | current artifact | current artifact plus one uncertainty source | bounded source branches and claim synthesis |
| persistence | none unless risky | compact checkpoint | persistent ledger and recovery receipt |
| review | focused self-check | targeted critic if costly to misunderstand | plan critic, routed cold read, adjudication |

Move up when ambiguity, coupling, duration, reversibility, breadth, or a reproduced failure justifies the cost. Move down when a branch cannot change the decision or a check duplicates the same oracle.
