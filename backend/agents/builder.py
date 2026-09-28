"""The Builder: the only role that changes the project rather than describing it.

WHY THIS FILE EXISTS
    Every other agent produces a document. This one produces files, which makes it
    the only place where a model's output has consequences that survive the call. It
    reads the approved plan, design system and component tree, and writes the code
    those describe.

WHY IT OVERRIDES ``run``
    ``base.run`` is one shot: prompt in, validated object out. That fits a role whose
    answer *is* the object. It does not fit a role whose work is a sequence of writes
    — the Builder has to write a file, see whether it was accepted, and continue.

    So this role runs a bounded tool-calling loop instead. It is the deliberate
    exception, and the reason is worth stating: ``docs/agents.md`` 4.6 grants it
    "workspace file read/write" as tools and defines its output in the past tense
    (``files_written``), which only makes sense if the writing happened first.

    Everything else is unchanged — the contract, the scoped context, the prompt
    assembly and the failure taxonomy all come from ``base.py``.

THE REPORT IS OBSERVED, NOT CLAIMED
    ``BuildResult`` is assembled from the writes the workspace tool actually
    performed, not from the model's account of what it did. If a write is refused —
    a path outside ``src/``, a builder-owned config file — that refusal is what gets
    recorded, and the model cannot report success for it.

    This is the same principle already applied to the Impeccable gate: a fact about
    what happened belongs to the layer that did it.

CONTAINMENT IS NOT THIS FILE'S JOB
    Which paths are writable is decided and enforced in ``backend/tools/workspace.py``.
    This agent holds a handle to one workspace and can express nothing outside it.
    The prompt states the boundary so the model does not waste turns discovering it,
    but the prompt is not what keeps it.

Spec: docs/agents.md 3, 4.6; docs/architecture.md 5.1-5.2; docs/security.md 3.1-3.4;
      docs/state.md 6; skills.md 4, 19; CLAUDE.md 17.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from backend.agents.base import (
    AgentDefinition,
    AgentFailure,
    AgentFailureKind,
    AgentResult,
    BaseAgent,
)
from backend.context.builder import AgentContext, ChangedFilePayload, ContextInput
from backend.schemas.enums import AgentRole
from backend.schemas.state import BuildResult
from backend.tools.workspace import (
    WorkspaceAccessError,
    WorkspaceFiles,
    build_workspace_tools,
)

logger = logging.getLogger(__name__)

# A build step is a handful of files. The cap is not a budget so much as a stop: a
# model looping on a refused path would otherwise keep asking (CLAUDE.md 26).
DEFAULT_MAX_TOOL_ITERATIONS = 12

_FILES_BLOCK = {
    "type": "text",
    "text": "FILES ALREADY IN THIS CHANGE SET\n{changed_files_content}",
}

# Constructed at import time so an incomplete contract fails on load, not on first run.
BUILDER_DEFINITION = AgentDefinition(
    role=AgentRole.BUILDER,
    purpose="Implement the approved architecture and design.",
    responsibilities=(
        "Create or modify only the files the approved plan calls for.",
        "Follow the approved design tokens and component rules exactly.",
        "Keep every change minimal and targeted.",
        "Give each new file a short top comment saying why it exists.",
        "Report anything built differently from the approved architecture.",
    ),
    prohibited_actions=(
        "Do not touch files outside the plan.",
        "Do not rewrite unrelated code, and do not reformat what you did not change.",
        "Do not regenerate the whole project for a local change.",
        "Do not add, change or pin a dependency.",
        "Do not write outside src/, and do not modify builder-owned template files "
        "such as package.json, vite.config.ts, index.html or tsconfig files.",
        "Do not generate a backend for the site you are building — it is "
        "frontend-only.",
        "Do not re-decide the plan or the design system; implement them as given.",
    ),
    output_model=BuildResult,
    allowed_tools=frozenset({"write_workspace_file", "read_workspace_file"}),
)


class BuilderAgent(BaseAgent[BuildResult]):
    """Writes the approved architecture into a project workspace."""

    def __init__(
        self,
        model,
        workspace: WorkspaceFiles,
        *,
        max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
    ) -> None:
        """
        Args:
            model: a LangChain chat model configured for this role's policy.
            workspace: the one project workspace this run may touch. Supplied by the
                caller, never derived from anything the model produced.
            max_tool_iterations: how many tool rounds before the run is stopped.
        """
        super().__init__(model)
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be at least 1")
        self._workspace = workspace
        self._max_tool_iterations = max_tool_iterations

    @property
    def definition(self) -> AgentDefinition:
        return BUILDER_DEFINITION

    def _changed_files(self, context: AgentContext) -> list[ChangedFilePayload]:
        candidate = context.inputs.get(ContextInput.CHANGED_FILES.value) or []
        return [f for f in candidate if isinstance(f, ChangedFilePayload)]

    def evidence_blocks(self, context: AgentContext) -> list[dict]:
        """Show files already touched in this change set, when there are any.

        Unlike the reviewers, an empty set is normal here — the first build step
        starts from nothing — so this block is simply omitted rather than becoming a
        missing variable that stops the run.
        """
        return [_FILES_BLOCK] if self._changed_files(context) else []

    def prompt_variables(self, context: AgentContext) -> dict[str, str]:
        variables = super().prompt_variables(context)
        files = self._changed_files(context)
        if files:
            variables["changed_files_content"] = "\n\n".join(
                f"--- {f.path} ---\n{f.content}" for f in files
            )
        return variables

    async def run(self, context: AgentContext) -> AgentResult[BuildResult]:
        """Write the approved work, then report what was actually written."""
        if context.role is not self.role:
            return AgentResult(
                role=self.role,
                failure=AgentFailure(
                    AgentFailureKind.CONTRACT_VIOLATION,
                    f"Context was built for '{context.role.value}', "
                    f"not '{self.role.value}'",
                ),
            )

        template = self.prompt_template(context)
        variables = self.prompt_variables(context)
        missing = sorted(set(template.input_variables) - set(variables))
        if missing:
            return AgentResult(
                role=self.role,
                failure=AgentFailure(
                    AgentFailureKind.CONTRACT_VIOLATION,
                    f"Prompt template is missing variables: {', '.join(missing)}",
                ),
            )

        tools = build_workspace_tools(self._workspace)
        by_name = {t.name: t for t in tools}
        model = self._model.bind_tools(tools)
        messages: list[Any] = list(template.format_messages(**variables))

        written: list[str] = []
        modified: list[str] = []
        refused: list[str] = []
        notes: list[str] = []

        for iteration in range(1, self._max_tool_iterations + 1):
            try:
                reply = await model.ainvoke(messages)
            except TimeoutError:
                return self._stop(
                    AgentFailureKind.TIMEOUT, "Model call timed out", iteration
                )
            except Exception as exc:
                logger.exception("builder provider error")
                return self._stop(
                    AgentFailureKind.PROVIDER_ERROR,
                    f"Model call failed ({type(exc).__name__})",
                    iteration,
                )

            calls = getattr(reply, "tool_calls", None) or []
            if not calls:
                # No more work requested: the model has finished.
                if isinstance(reply, AIMessage) and isinstance(reply.content, str):
                    summary = reply.content.strip()
                    if summary:
                        notes.append(summary)
                break

            messages.append(reply)
            for call in calls:
                name = call.get("name", "")
                args = call.get("args", {}) or {}
                tool = by_name.get(name)
                if tool is None:
                    # The model asked for something it was never given.
                    outcome = f"No such tool: {name}"
                    refused.append(outcome)
                else:
                    existed = (
                        name == "write_workspace_file"
                        and self._workspace.exists(str(args.get("path", "")))
                    )
                    try:
                        outcome = str(tool.invoke(args))
                        if name == "write_workspace_file":
                            path = str(args.get("path", ""))
                            (modified if existed else written).append(path)
                    except WorkspaceAccessError as exc:
                        # The boundary held. Tell the model why, so it can correct,
                        # and record the attempt — it is a deviation, not a silent no.
                        outcome = f"Refused: {exc}"
                        refused.append(f"{args.get('path', '?')}: {exc}")
                        logger.warning("builder write refused: %s", exc)
                messages.append(
                    ToolMessage(content=outcome, tool_call_id=call.get("id", ""))
                )
        else:
            notes.append(
                f"Stopped after {self._max_tool_iterations} tool rounds; the step may "
                "be incomplete."
            )

        result = BuildResult(
            files_written=written,
            files_modified=modified,
            notes=notes,
            # A refused write is a departure from what was attempted, and the reader
            # needs to know it happened rather than infer it from an absence.
            deviations_from_plan=[f"Write refused — {r}" for r in refused],
        )
        logger.info(
            "builder finished written=%d modified=%d refused=%d",
            len(written),
            len(modified),
            len(refused),
        )
        return AgentResult(role=self.role, output=result)

    def _stop(
        self, kind: AgentFailureKind, message: str, attempts: int
    ) -> AgentResult[BuildResult]:
        return AgentResult(
            role=self.role, failure=AgentFailure(kind, message, attempts)
        )

    def task_instructions(self, context: AgentContext) -> str:
        return (
            f"{context.task}\n\n"
            "HOW TO WORK\n"
            "- Use `write_workspace_file` to create or replace a file, and "
            "`read_workspace_file` when you need to see something first.\n"
            "- Write whole files. A write replaces the file's contents.\n"
            "- When the step is done, stop calling tools and say in one line what "
            "you did.\n"
            "\n"
            "WHAT YOU MAY WRITE\n"
            "- Paths under `src/` only. Everything else is refused, including "
            "`package.json`, `vite.config.ts`, `index.html` and the tsconfig files. "
            "Those are owned by the template because the build executes them.\n"
            "- If a write is refused you will be told why. Do not retry the same "
            "path; adjust or leave it and report it.\n"
            "- You cannot add a dependency. If the work seems to need one, stop and "
            "say so — it is the user's decision, not yours.\n"
            "\n"
            "SCOPE\n"
            "- Build what the approved plan and component tree describe, and "
            "nothing else. An extra component is code someone has to maintain.\n"
            "- Use the approved design system as given: its tokens, its spacing, its "
            "component language. Do not substitute your own values.\n"
            "- Touch only what this step requires. Do not reformat or tidy code you "
            "were not asked to change — it buries the real diff.\n"
            "- The site is frontend-only. Do not generate a server, a database "
            "layer or an API for it.\n"
            "\n"
            "EVERY NEW FILE\n"
            "- Starts with a short comment saying what it is responsible for and why "
            "it exists — architectural intent, not a restatement of the code.\n"
            "\n"
            "IF YOU DEPART FROM THE PLAN\n"
            "- Say so plainly in your closing line. A departure nobody is told about "
            "is indistinguishable from a mistake, and the next stage will review the "
            "result against the plan you were given."
        )
