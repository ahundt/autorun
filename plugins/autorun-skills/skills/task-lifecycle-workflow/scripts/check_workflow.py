"""Development-only static checks for the containing task-lifecycle-workflow package.

Run explicitly when developing or validating this skill, not when using its workflow.
Requires Python 3 and its standard library; no arguments or repository root needed.
Reads package files without modifying them. Prints PASS/FAIL and exits nonzero on
failed checks or unreadable inputs. Does not simulate model behavior or host hooks.
"""

from pathlib import Path
from collections import deque
import re
import sys

p = Path(__file__).resolve().parents[1]
graphs = {
    f.stem: f.read_text(encoding="utf-8") for f in (p / "references").glob("*.mmd")
}


def parse(s):
    return re.findall(r"^\s+(\w+) -->\|([^\n]*?)\| (\w+)\s*$", s, re.M)


edges = {k: parse(v) for k, v in graphs.items()}
failures = []


def check(label, ok):
    print(("PASS" if ok else "FAIL") + ": " + label)
    if not ok:
        failures.append(label)


def edge(g, a, b, words=""):
    return any(x == a and y == b and words in label for x, label, y in edges[g])


def reachable(es, start, end, without=()):
    seen = set(without)
    todo = deque([start])
    while todo:
        n = todo.popleft()
        if n in seen:
            continue
        if n == end:
            return True
        seen.add(n)
        todo.extend(b for a, _, b in es if a == n)
    return False


outer = edges["outer-lifecycle"]
recovery = edges["off-rails-recovery"]
work = edges["work-cycle"]
coding = edges["qualitative-coding-pipeline"]
check(
    "Both recovery outcomes re-enter the router, not a direct wait",
    sum(a == "recover_trusted_state" for a, _, b in outer) == 2
    and all(b == "event_received" for a, _, b in outer if a == "recover_trusted_state")
    and edge(
        "outer-lifecycle",
        "recover_trusted_state",
        "event_received",
        "recovery_blocked_return",
    ),
)
check(
    "Recovery cannot bypass event routing to reach a wait",
    not reachable(
        outer, "recover_trusted_state", "blocked_wait_state", {"event_received"}
    ),
)
check(
    "Unavailable restoration has a checkpoint return to its caller",
    edge(
        "off-rails-recovery",
        "recovery_blocked",
        "recovery_blocked_return",
        "checkpoint",
    ),
)
check(
    "Recovery labels and prose designate outer-owned checkpoint resumption",
    edge(
        "off-rails-recovery",
        "recovery_blocked",
        "recovery_resume_event",
        "outer router supplies",
    )
    and "Resuming blocked recovery enters `recovery_resume_event`"
    in (p / "SKILL.md").read_text(encoding="utf-8"),
)
check(
    "Resumed recovery cannot integrate without verifying restoration",
    reachable(recovery, "recovery_resume_event", "verified_integration")
    and not reachable(
        recovery, "recovery_resume_event", "verified_integration", {"restoration_check"}
    ),
)
check(
    "Retry cannot reach either repair owner without verifying restoration",
    all(
        reachable(recovery, "repair_retry_bound_available", target)
        and not reachable(
            recovery, "repair_retry_bound_available", target, {"restoration_check"}
        )
        for target in ["bounded_repair", "fresh_worker_restart"]
    ),
)
check(
    "Repair and post-mortem retry exhaustion retain failure returns",
    all(
        edge("off-rails-recovery", n, "recovery_blocked_return", "no: exhausted bound")
        for n in ["repair_retry_bound_available", "postmortem_repair_bound_available"]
    ),
)
check(
    "Missing correction acknowledgement enters an action gate, not recheck clearance",
    edge(
        "outer-lifecycle",
        "correction_acknowledgement_check",
        "affected_action_gate",
        "fail: block until worker acknowledges or is replaced",
    )
    and not edge(
        "outer-lifecycle",
        "correction_acknowledgement_check",
        "trackkeeper_recheck",
        "fail:",
    ),
)
check(
    "Acknowledgement gate names side effects and forbids premature clearance",
    "affected_action_gate[gate: block affected side effects,"
    in graphs["outer-lifecycle"]
    and "Autonomous resolution cannot clear this gate for an unacknowledged worker."
    in (p / "references/continuity-recovery-and-gates.md").read_text(encoding="utf-8"),
)
check(
    "Worker completion requires acceptance check evidence, not self-acceptance",
    "passing acceptance check results, and evidence, pending orchestrator acceptance"
    in (p / "SKILL.md").read_text(encoding="utf-8")
    and "Only the orchestrator accepts a result against its assigned acceptance criteria"
    in (p / "references/orchestration-and-state.md").read_text(encoding="utf-8")
    and "invalidate them and recheck when those requirements or artifacts change."
    in (p / "references/orchestration-and-state.md").read_text(encoding="utf-8"),
)
check(
    "Release prose requires permission for action/artifact/target and permits matching prior scope",
    "Every `RELEASE` action requires explicit user permission covering the action, artifact, and target; reuse prior permission while its scope covers the current release."
    in (p / "SKILL.md").read_text(encoding="utf-8"),
)
check(
    "Self-check findings use the same pass/rework adjudication as independent review",
    edge(
        "work-cycle",
        "plan_review_routing",
        "review_adjudication",
        "no: orchestrator self-checks plan and reports findings",
    )
    and edge("work-cycle", "review_adjudication", "targeted_rework_routing", "rework:"),
)
check(
    "Scoped stops/prohibitions replan while preserving independent work",
    edge(
        "outer-lifecycle",
        "choose_restart_stage",
        "plan_dependencies",
        "scoped stop or prohibition; preserve independent work",
    ),
)
check(
    "Incomplete pagination branch explicitly excludes unverified evidence",
    edge(
        "qualitative-coding-pipeline",
        "pagination_complete",
        "retain_private_evidence",
        "new candidates unverified, prior verified evidence retained",
    )
    and "Unverified candidates are not evidence"
    in (p / "references/qualitative-coding.md").read_text(encoding="utf-8"),
)
check(
    "Incomplete pagination return cannot promote a control",
    not reachable(coding, "retain_private_evidence", "admitted_workflow_control"),
)
check(
    "Graph labels and prose require corrective work before DONE",
    "required corrective work remains" in graphs["outer-lifecycle"]
    and "required work complete; other actions" in graphs["outer-lifecycle"]
    and "reaches `DONE` only after required corrective work is complete"
    in (p / "references/continuity-recovery-and-gates.md").read_text(encoding="utf-8"),
)
check(
    "Release reuse branch names both artifact and target",
    edge(
        "outer-lifecycle",
        "readiness_gate",
        "outcome_postmortem",
        "already verified for this artifact and target",
    ),
)
check(
    "Readiness distinguishes authorized and unauthorized pending release",
    edge(
        "outer-lifecycle",
        "readiness_gate",
        "release_gate",
        "release pending and authorized",
    )
    and edge(
        "outer-lifecycle",
        "readiness_gate",
        "await_release_authorization",
        "release pending; permission missing",
    ),
)
check(
    "Both escalation routes name missing authority/information and choices without safe defaults",
    "user-only authority or information missing, or does a consequential choice lack a safe default"
    in graphs["outer-lifecycle"]
    and "user-only authority or information missing after authorization checks, or consequential choice lacks safe default"
    in graphs["work-cycle"],
)
check(
    "Escalation label separates missing authority from choices without safe defaults",
    edge(
        "outer-lifecycle",
        "user_input_requirement",
        "blocked_wait_state",
        "yes: user-only authority or information missing after authorization checks, or consequential choice lacks safe default",
    ),
)
check(
    "Core retains completion, observation, delegation, and audit terminology",
    all(
        phrase in (p / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "strong definition of done",
            "read-only observation and transfers no command or control",
            "Copy the canonical `result_contract` unchanged",
            "trackkeeping supplies independence, not an exclusive capability",
            "the trackkeeper returns recheck evidence and a clearance recommendation",
            "revisioned correction packet",
            "Keep exact user wording in private evidence notes",
        )
    ),
)
check(
    "Resolved acknowledgement failure returns to recheck without restarting event classification",
    edge(
        "outer-lifecycle",
        "orchestrator_autonomous_resolution",
        "trackkeeper_recheck",
        "correction acknowledgement or worker replacement",
    )
    and reachable(
        outer, "affected_action_gate", "trackkeeper_recheck", {"event_received"}
    ),
)
check(
    "Whole-workflow stop or no independent work has a wait route with explicit resume",
    edge(
        "outer-lifecycle",
        "choose_restart_stage",
        "blocked_wait_state",
        "whole-workflow user stop, or loud blocker with no eligible independent work",
    )
    and edge(
        "outer-lifecycle",
        "blocked_wait_state",
        "event_received",
        "explicit user resume",
    )
    and "Keep those nodes ineligible until an explicit matching user resume or permission"
    in (p / "references/continuity-recovery-and-gates.md").read_text(encoding="utf-8"),
)
check(
    "Retry exhaustion changes mechanism before exhausting method-change bounds",
    edge(
        "work-cycle",
        "targeted_rework_routing",
        "mechanism_selection",
        "retry bound exhausted with method-change budget remaining",
    )
    and edge(
        "work-cycle",
        "targeted_rework_routing",
        "blocked_workflow_state",
        "retry and method-change bounds exhausted",
    ),
)
check(
    "Acceptance ledger has criteria, accepted revisions, and evidence",
    all(
        field
        in (p / "references/orchestration-and-state.md").read_text(encoding="utf-8")
        for field in [
            "`acceptance_criteria`",
            "`accepted_revisions`",
            "`acceptance_evidence`",
        ]
    ),
)
check(
    "Closeout corrections stay in scope and carry retry budgets across epochs",
    "Required corrective work satisfies an in-scope requirement or repairs a defect in in-scope agent-owned output"
    in (p / "SKILL.md").read_text(encoding="utf-8")
    and "Carry retry and method-change bounds across epochs, including post-mortem corrections"
    in (p / "SKILL.md").read_text(encoding="utf-8"),
)
check(
    "Assignment and correction packets carry acceptance criteria",
    "Applicable requirement IDs, their acceptance criteria, and exact contract-changing wording."
    in (p / "references/role-packets-and-messages.md").read_text(encoding="utf-8")
    and "Requirement IDs, changed acceptance criteria, and exact changed wording."
    in (p / "references/role-packets-and-messages.md").read_text(encoding="utf-8")
    and "requirements this node must satisfy or inform, with their acceptance criteria"
    in (p / "references/planning-delegation-and-joins.md").read_text(encoding="utf-8"),
)
check(
    "Outstanding acknowledgement recheck has a gated severity route, never an advisory one",
    edge(
        "outer-lifecycle",
        "orchestrator_records_recheck",
        "finding_severity_route",
        "unresolved finding",
    )
    and edge(
        "outer-lifecycle",
        "finding_severity_route",
        "affected_action_gate",
        "acknowledgement/replacement missing",
    )
    and all(
        "acknowledgement/replacement is not missing" in label
        for a, label, b in outer
        if a == "finding_severity_route" and b == "classify_event"
    ),
)
check(
    "Budget renewal requires materially changed conditions in core and recovery prose",
    all(
        "Renew bounds only when new user input, authority, or an authorized contract change materially changes the failed attempt's conditions; record why."
        in (p / doc).read_text(encoding="utf-8")
        for doc in ["SKILL.md", "references/continuity-recovery-and-gates.md"]
    ),
)
check(
    "Implementation rework edge requires remaining retry bound",
    edge(
        "work-cycle",
        "targeted_rework_routing",
        "eligible_node_dispatch",
        "within retry bound",
    ),
)
check(
    "DONE is reachable but cannot bypass outcome post-mortem",
    reachable(outer, "event_received", "done_terminal")
    and not reachable(outer, "event_received", "done_terminal", {"outcome_postmortem"}),
)
release_entries = [(a, label, b) for a, label, b in outer if b == "release_gate"]
without_release_authorization = [e for e in outer if e not in release_entries]
check(
    "Release gate has only the authorized readiness entry and no bypass",
    release_entries
    == [("readiness_gate", "pass: release pending and authorized", "release_gate")]
    and reachable(outer, "event_received", "release_gate")
    and not reachable(without_release_authorization, "event_received", "release_gate"),
)
check(
    "Work-cycle return kinds remain separate",
    all(
        edge("work-cycle", a, b)
        for a, b in [
            ("epoch_ready_state", "outer_readiness_return"),
            ("trusted_state_recovery_event", "outer_recovery_return"),
            ("blocked_workflow_state", "outer_blocked_return"),
        ]
    ),
)
for name, s in graphs.items():
    check(
        name + ": all solid edges parsed",
        len(edges[name]) == sum("-->" in line for line in s.splitlines()),
    )
    nodes = set(re.findall(r"^\s+(\w+)[\[{]", s, re.M))
    check(
        name + ": all edge endpoints exist",
        all(a in nodes and b in nodes for a, _, b in edges[name]),
    )
expected = {
    "references/continuity-recovery-and-gates.md": [
        "outer-lifecycle",
        "off-rails-recovery",
    ],
    "references/planning-delegation-and-joins.md": ["work-cycle"],
    "references/qualitative-coding.md": ["qualitative-coding-pipeline"],
}
for doc, names in expected.items():
    check(
        doc + ": exact inline/source parity",
        re.findall(
            r"```mermaid\n(.*?)\n```", (p / doc).read_text(encoding="utf-8"), re.S
        )
        == [graphs[n].rstrip() for n in names],
    )
# Negative controls: ensure the reachability checks detect the reviewed unsafe paths.
unsafe_recovery = [
    (
        a,
        label,
        "repair_scope_assessment"
        if a == "repair_retry_bound_available" and label.startswith("yes:")
        else b,
    )
    for a, label, b in recovery
]
check(
    "Negative control: old retry target is detected as a restoration bypass",
    reachable(
        unsafe_recovery,
        "repair_retry_bound_available",
        "fresh_worker_restart",
        {"restoration_check"},
    ),
)
unsafe_outer = [
    (
        a,
        label,
        "blocked_wait_state"
        if a == "recover_trusted_state" and label.startswith("recovery_blocked_return:")
        else b,
    )
    for a, label, b in outer
]
check(
    "Negative control: old recovery return is detected as a router bypass",
    reachable(
        unsafe_outer, "recover_trusted_state", "blocked_wait_state", {"event_received"}
    ),
)
unreturned_resolution = [
    e
    for e in outer
    if not (
        e[0] == "orchestrator_autonomous_resolution" and e[2] == "trackkeeper_recheck"
    )
]
check(
    "Negative control: removing resolved-finding return loses recheck before event restart",
    not reachable(
        unreturned_resolution,
        "affected_action_gate",
        "trackkeeper_recheck",
        {"event_received"},
    ),
)
print(f"{len(failures)} failed checks")
sys.exit(bool(failures))
