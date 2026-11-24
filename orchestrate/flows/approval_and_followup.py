# ADK version: ibm-watsonx-orchestrate-adk 1.x
# Written against the flow_builder API as of November 2026.
# If the API has changed, compare against:
#   from ibm_watsonx_orchestrate.flow_builder import flow, Step, HumanApproval
# and the official ADK changelog.
#
# Import with:
#   orchestrate tools import -k flow -f orchestrate/flows/approval_and_followup.py
"""ClaimBack approval-and-follow-up Orchestrate flow (SPEC §5.3, §8).

Implements the human-in-the-loop gate that prevents any appeal from being
marked READY_TO_SEND without explicit user approval.

Flow steps
----------
1. ``present_draft`` — Tool call: fetch the current appeal draft for the case
   via ``case_get_draft``.
2. ``await_approval`` — Human approval step: present the draft to the user
   and wait for an explicit Approve or Reject action.
3a. On approval → ``approve_case``: call ``case_approve`` to mark the case
    READY_TO_SEND, then ``schedule_reminder``: call ``case_schedule_followup``
    to create the internal-appeal deadline reminder.
3b. On rejection → ``return_for_edit``: return a message telling the user to
    edit the draft and re-submit for review.

Usage
-----
The supervisor triggers this flow when the user clicks "Approve" in the chat
or when the UI posts to /api/cases/{id}/approve.
"""
from __future__ import annotations

# VERIFY: import path may differ between ADK versions —
#   check `python -c "import ibm_watsonx_orchestrate; print(ibm_watsonx_orchestrate.__version__)"`
#   and compare against the ADK changelog.
from ibm_watsonx_orchestrate.flow_builder import (  # type: ignore[import]
    HumanApprovalStep,
    ToolStep,
    flow,
)


@flow(
    name="approval_and_followup",
    description=(
        "Human-in-the-loop gate for appeal approval. "
        "Presents the draft, waits for explicit approval, then marks the case "
        "READY_TO_SEND and schedules the internal-appeal deadline reminder."
    ),
)
def approval_and_followup(
    case_id: str,
    approved_by: str,
    followup_due_at: str,
    followup_kind: str = "internal_appeal",
) -> None:
    """Approval and follow-up flow.

    Parameters
    ----------
    case_id:
        UUID of the case to approve.
    approved_by:
        Identifier of the approver (passed through to ``case_approve``).
    followup_due_at:
        ISO-8601 datetime for the follow-up reminder (e.g. the internal
        appeal deadline calculated by Agent 8).
    followup_kind:
        Reminder type — default ``"internal_appeal"``.

    Notes
    -----
    The ``@flow`` decorator registers this function as an Orchestrate flow.
    Each ``ToolStep`` / ``HumanApprovalStep`` maps to a named Orchestrate
    action; the ADK serialises the graph at import time.

    VERIFY: ``ToolStep`` constructor signature — some ADK versions use
    ``ToolStep(tool_name, inputs={...})``; others use
    ``ToolStep.from_operation_id("case_get_draft", {...})``.
    """
    # ── Step 1: Fetch the draft ───────────────────────────────────────────────
    # VERIFY: ToolStep constructor signature
    present_draft = ToolStep(
        name="present_draft",
        tool="case_get_draft",          # operationId from claimback_openapi.json
        inputs={"case_id": case_id},
        description="Retrieve the current appeal draft for the case.",
    )

    # ── Step 2: Human approval gate ───────────────────────────────────────────
    # VERIFY: HumanApprovalStep constructor — field names may differ.
    await_approval = HumanApprovalStep(
        name="await_approval",
        prompt=(
            "Please review the appeal draft above.\n"
            "• Click **Approve** to mark it ready to send and schedule your deadline reminder.\n"
            "• Click **Reject** to return the draft for editing.\n\n"
            "⚠️ This is synthetic demo data. Not legal or medical advice."
        ),
        approve_label="Approve",
        reject_label="Reject — send back for editing",
    )

    # ── Step 3a: Approved path ────────────────────────────────────────────────
    # VERIFY: branching syntax — some ADK versions use `on_approve` / `on_reject`
    #         kwargs; others use `when(await_approval.approved)`.
    approve_case = ToolStep(
        name="approve_case",
        tool="case_approve",            # operationId
        inputs={
            "case_id": case_id,
            "approved_by": approved_by,
        },
        description="Mark the case READY_TO_SEND.",
        # VERIFY: condition field name
        condition=await_approval.approved,  # type: ignore[attr-defined]
    )

    schedule_reminder = ToolStep(
        name="schedule_reminder",
        tool="case_schedule_followup",  # operationId
        inputs={
            "case_id": case_id,
            "due_at": followup_due_at,
            "kind": followup_kind,
        },
        description="Create the internal-appeal deadline reminder.",
        # VERIFY: condition field name
        condition=await_approval.approved,  # type: ignore[attr-defined]
    )

    # ── Step 3b: Rejected path ────────────────────────────────────────────────
    return_for_edit = ToolStep(
        name="return_for_edit",
        tool=None,                      # no tool call — just a message step
        message=(
            "The draft has been sent back for editing. "
            "Make your changes and submit for approval again when ready."
        ),
        # VERIFY: condition field name
        condition=await_approval.rejected,  # type: ignore[attr-defined]
    )

    # ── Wire the flow graph ───────────────────────────────────────────────────
    # VERIFY: graph wiring API — may be positional args, .then() chaining,
    #         or a `steps=[...]` list passed to @flow.
    present_draft >> await_approval  # type: ignore[operator]
    await_approval >> approve_case >> schedule_reminder  # type: ignore[operator]
    await_approval >> return_for_edit  # type: ignore[operator]
