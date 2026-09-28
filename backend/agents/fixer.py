"""The Fixer: applies the corrections the reviewers asked for, and nothing else.

WHY THIS FILE EXISTS
    Findings are worthless until something acts on them, and acting on them is the
    riskiest moment in the loop: the page already works, and every edit can make it
    worse. So this role is defined more by what it will not touch than by what it
    changes.

    Two boundaries do the work:

      - it may write only the files it was given, not merely anywhere under ``src/``
      - it must not act on a finding that conflicts with an approved decision

    The second is the one that matters. A reviewer can flag something the user chose
    on purpose — a gradient that was a decision, a font that was specified. Fixing
    that would undo the user's own choice on the strength of a detector rule, which
    is exactly the failure the whole approval mechanism exists to prevent
    (docs/design.md 5.5, docs/agents.md 4.11).

WHY IT OVERRIDES ``run``
    Same reason as the Builder: its work is a sequence of writes, not a single
    answer. It runs a bounded tool loop, then asks once for a structured report.
    Contract, scoped context, prompt assembly and failure taxonomy still come from
    ``base.py``.

WHAT THE REPORT IS ALLOWED TO CLAIM
    ``files_modified`` comes from the writes that actually happened, and
    ``escalated_to_user`` is computed from the findings that were marked as
    conflicting — not from the model's account of either. A conflicted finding is
    removed from whatever else the model put it in, so it cannot be reported as
    fixed. The model's judgement is used only where judgement is the answer: which
    findings it believes it resolved, and why.

Spec: docs/agents.md 3, 4.11; docs/design.md 5.5; docs/state.md 6, 7;
      docs/architecture.md 5.2; docs/workflow.md 4; skills.md 19.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.agents.base import (
    AgentDefinition,
    AgentFailure,
    AgentFailureKind,
    AgentResult,
    BaseAgent,
)
from backend.context.builder import AgentContext, ChangedFilePayload, ContextInput
from backend.schemas.enums import AgentRole
from backend.schemas.state import EvaluationFinding, FixResult
from backend.tools.workspace import (
    WorkspaceAccessError,
    WorkspaceFiles,
    build_workspace_tools,
)

logger = logging.getLogger(__name__)

# A targeted fix touches few files. The cap stops a model looping on a refusal
# rather than budgeting its work (CLAUDE.md 26).
DEFAULT_MAX_TOOL_ITERATIONS = 10

_FINDINGS_BLOCK = {"type": "text", "text": "FINDINGS TO FIX\n{findings_to_fix}"}
_FILES_BLOCK = {"type": "text", "text": "AFFECTED FILES\n{affected_files}"}

_REPORT_REQUEST = (
    "Now report what you did, as structured output. List the finding ids you "
    "actually resolved, the ones you could not, and a short rationale. Do not list "
    "a finding as resolved unless the change you made addresses it."
)

# Constructed at import time so an incomplete contract fails on load, not on first run.
FIXER_DEFINITION = AgentDefinition(
    role=AgentRole.FIXER,
    purpose="Apply targeted corrections for specific findings.",
    responsibilities=(
        "Fix exactly the findings you were given.",
        "Use the smallest change that resolves each one.",
        "Preserve every unrelated approved decision.",
        "Report anything you could not fix, and why.",
    ),
    prohibited_actions=(
        "Do not make unrelated modifications, and do not reformat code you were not "
        "asked to change.",
        "Do not redesign. A finding asks for a correction, not a new approach.",
        "Do not regenerate the whole project for a local change.",
        "Do not act on a finding that conflicts with an approved decision — "
        "escalate it to the user instead.",
        "Do not write files you were not given. Your scope is the affected files, "
        "not the whole workspace.",
    ),
    output_model=FixResult,
    allowed_tools=frozenset({"write_workspace_file", "read_workspace_file"}),
)


class FixerAgent(BaseAgent[FixResult]):
    """Applies targeted corrections within a scoped set of files."""

    def __init__(
        self,
        model,
        workspace: WorkspaceFiles,
        *,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        super().__init__(model)
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be at least 1")
        self._workspace = workspace
        self._max_tool_iterations = max_tool_iterations

    @property
    def definition(self) -> AgentDefinition:
        return FIXER_DEFINITION

    # --- Context ---------------------------------------------------------

    def _findings(self, context: AgentContext) -> list[EvaluationFinding]:
        candidate = context.inputs.get(ContextInput.FINDINGS.value) or []
        return [f for f in candidate if isinstance(f, EvaluationFinding)]

    def _affected(self, context: AgentContext) -> list[ChangedFilePayload]:
        candidate = context.inputs.get(ContextInput.CHANGED_FILES.value) or []
        return [f for f in candidate if isinstance(f, ChangedFilePayload)]

    def _actionable(self, context: AgentContext) -> list[EvaluationFinding]:
        """Findings this role is permitted to act on."""
        return [f for f in self._findings(context) if not f.conflicts_with_approved_decision]

    def _conflicted(self, context: AgentContext) -> list[EvaluationFinding]:
        """Findings that must go to the user rather than be fixed."""
        return [f for f in self._findings(context) if f.conflicts_with_approved_decision]

    def evidence_blocks(self, context: AgentContext) -> list[dict]:
        """Findings and the files in scope. Both are always declared: without either
        there is nothing to fix, and the shared missing-variable check stops the run
        before it costs a call."""
        return [_FINDINGS_BLOCK, _FILES_BLOCK]

    def prompt_variables(self, context: AgentContext) -> dict[str, str]:
        variables = super().prompt_variables(context)

        actionable = self._actionable(context)
        if actionable:
            variables["findings_to_fix"] = "\n\n".join(
                f"[{f.finding_id}] severity={f.severity.value} "
                f"location={f.location or 'unspecified'}\n{f.summary}"
                + (f"\nSuggested: {f.suggested_fix}" if f.suggested_fix else "")
                for f in actionable
            )

        affected = self._affected(context)
        if affected:
            variables["affected_files"] = "\n\n".join(
                f"--- {f.path} ---\n{f.content}" for f in affected
            )
        return variables

    # --- Execution -------------------------------------------------------

    async def run(self, context: AgentContext) -> AgentResult[FixResult]:
        """Apply the fixes, then report what actually changed."""
        if context.role is not self.role:
            return self._fail(
                AgentFailureKind.CONTRACT_VIOLATION,
                f"Context was built for '{context.role.value}', not '{self.role.value}'",
            )

        conflicted = [f.finding_id for f in self._conflicted(context)]

        # Everything conflicts: there is nothing this role may touch, and the answer
        # is known without asking a model.
        if self._findings(context) and not self._actionable(context):
            logger.info("fixer: all %d finding(s) conflict; escalating", len(conflicted))
            return AgentResult(
                role=self.role,
                output=FixResult(
                    escalated_to_user=conflicted,
                    rationale=(
                        "Every finding conflicts with an approved decision, so none "
                        "was actioned."
                    ),
                ),
            )

        template = self.prompt_template(context)
        variables = self.prompt_variables(context)
        missing = sorted(set(template.input_variables) - set(variables))
        if missing:
            return self._fail(
                AgentFailureKind.CONTRACT_VIOLATION,
                f"Prompt template is missing variables: {', '.join(missing)}",
            )

        # Scope: only the files this run was handed. Narrower than the workspace
        # tool's own src/ rule, and enforced here because "the affected files" is a
        # per-run set the tool cannot know (docs/agents.md 4.11).
        in_scope = {f.path for f in self._affected(context)}

        tools = build_workspace_tools(self._workspace)
        by_name = {t.name: t for t in tools}
        model = self._model.bind_tools(tools)
        messages: list[Any] = list(template.format_messages(**variables))

        modified: list[str] = []
        refused: list[str] = []

        for iteration in range(1, self._max_tool_iterations + 1):
            try:
                reply = await model.ainvoke(messages)
            except TimeoutError:
                return self._fail(AgentFailureKind.TIMEOUT, "Model call timed out", iteration)
            except Exception as exc:
                logger.exception("fixer provider error")
                return self._fail(
                    AgentFailureKind.PROVIDER_ERROR,
                    f"Model call failed ({type(exc).__name__})",
                    iteration,
                )

            calls = getattr(reply, "tool_calls", None) or []
            if not calls:
                break

            messages.append(reply)
            for call in calls:
                name = call.get("name", "")
                args = call.get("args", {}) or {}
                path = str(args.get("path", ""))
                tool = by_name.get(name)

                if tool is None:
                    outcome = f"No such tool: {name}"
                    refused.append(outcome)
                elif name == "write_workspace_file" and path not in in_scope:
                    # Out of scope even though the workspace would have allowed it.
                    outcome = (
                        f"Refused: {path} is not one of the affected files for this "
                        "fix. Scope is limited to the files you were given."
                    )
                    refused.append(f"{path}: out of scope for this fix")
                    logger.warning("fixer write refused: out of scope")
                else:
                    try:
                        outcome = str(tool.invoke(args))
                        if name == "write_workspace_file":
                            modified.append(path)
                    except WorkspaceAccessError as exc:
                        outcome = f"Refused: {exc}"
                        refused.append(f"{path}: {exc}")
                        logger.warning("fixer write refused: %s", exc)

                messages.append(ToolMessage(content=outcome, tool_call_id=call.get("id", "")))

        # Judgement — which findings it believes it resolved — is the one thing only
        # the model can answer, so it is asked for once, as structured output.
        messages.append(HumanMessage(content=_REPORT_REQUEST))
        try:
            reported = await self._model.with_structured_output(FixResult).ainvoke(messages)
        except Exception as exc:
            logger.exception("fixer report failed")
            return self._fail(
                AgentFailureKind.SCHEMA_INVALID,
                f"Could not produce a fix report ({type(exc).__name__})",
            )

        return AgentResult(
            role=self.role, output=self._reconcile(reported, modified, refused, conflicted)
        )

    def _reconcile(
        self,
        reported: FixResult,
        modified: list[str],
        refused: list[str],
        conflicted: list[str],
    ) -> FixResult:
        """Replace what can be observed; keep only what must be judged.

        A conflicted finding is stripped from every other list first, so a model
        cannot report as fixed something it was forbidden to touch.
        """
        conflict_set = set(conflicted)
        addressed = [f for f in reported.findings_addressed if f not in conflict_set]
        unresolved = [f for f in reported.unresolved if f not in conflict_set]
        unresolved += [f"{r}" for r in refused if r not in unresolved]

        rationale = reported.rationale
        if conflicted:
            note = (
                f"{len(conflicted)} finding(s) conflict with an approved decision and "
                "were escalated rather than changed."
            )
            rationale = f"{rationale} {note}".strip() if rationale else note

        return FixResult(
            findings_addressed=addressed,
            # Observed, not claimed: these are the writes that actually happened.
            files_modified=sorted(set(modified)),
            unresolved=unresolved,
            escalated_to_user=conflicted,
            rationale=rationale,
        )

    def _fail(
        self, kind: AgentFailureKind, message: str, attempts: int = 1
    ) -> AgentResult[FixResult]:
        return AgentResult(role=self.role, failure=AgentFailure(kind, message, attempts))

    # --- Prompt ----------------------------------------------------------

    def task_instructions(self, context: AgentContext) -> str:
        conflicted = self._conflicted(context)
        scope = sorted({f.path for f in self._affected(context)})
        conflict_note = (
            f"\n- {len(conflicted)} further finding(s) were withheld because they "
            "conflict with an approved decision. They are not yours to act on and "
            "are being raised with the user."
            if conflicted
            else ""
        )

        return (
            f"{context.task}\n\n"
            "WHAT TO DO\n"
            "- Fix the findings listed above, one at a time, using "
            "`write_workspace_file`. Read a file first if you need to.\n"
            "- A write replaces the whole file, so include the full contents.\n"
            "- When you are done, stop calling tools.\n"
            f"{conflict_note}\n"
            "\n"
            "SCOPE\n"
            f"- You may write only these files:\n"
            + ("\n".join(f"  - {p}" for p in scope) or "  (none)")
            + "\n"
            "- Any other path is refused, even inside src/. If a fix genuinely needs "
            "a file you were not given, leave the finding unresolved and say so.\n"
            "\n"
            "THE SMALLEST CHANGE THAT WORKS\n"
            "- Change what the finding is about and nothing else. Do not tidy, "
            "rename, reorder or reformat in passing — it hides the real fix and "
            "gives the next reviewer more to read.\n"
            "- Do not redesign. If a finding seems to call for a different approach "
            "rather than a correction, that is a judgement for the user: leave it "
            "unresolved and explain.\n"
            "\n"
            "PRESERVE WHAT WAS APPROVED\n"
            "- The constraints above are settled decisions. A fix that quietly "
            "changes one is worse than the finding it was trying to resolve.\n"
            "\n"
            "REPORTING\n"
            "- Only list a finding as resolved if the edit you made actually "
            "addresses it. An optimistic report costs the next iteration real work, "
            "because the re-evaluation will find it again and nobody will know why.\n"
            "- Say plainly what you could not fix and why."
        )
