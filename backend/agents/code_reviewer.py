"""The Code Reviewer: reads the code a step actually changed.

WHY THIS FILE EXISTS
    The other reviewers judge the rendered result. This one judges the thing that
    produced it. A page can look right and still be held together by a dead import, a
    component nobody renders, or a key pasted into a source file — none of which a
    screenshot will ever show.

    It is also the narrowest role in the system. Its entire granted context is the
    changed files: no plan, no design spec, no approved decisions, no project state
    at all. That is deliberate. A reviewer that can see the plan starts reviewing
    whether the code matches the plan, which is someone else's judgement, and the
    code questions get less attention for it.

LAYER
    Agents (``backend/agents/``). It supplies :meth:`evidence_blocks` rather than
    rebuilding the prompt, so the task and the untrusted-input guard are contributed
    by ``base.py`` and cannot be dropped here.

THE LIMIT THAT SHAPES EVERY FINDING
    It sees the changed files and nothing else — not the modules they import, not the
    callers they affect, not the rest of the tree. So an import it cannot resolve is
    a *question*, not a defect. Saying "this import is broken" about a file you were
    not shown wastes the reader's time and teaches them to distrust the review.

    The prompt is built around that distinction, because it is the single easiest way
    for this role to be confidently wrong.

CODE IS UNTRUSTED INPUT
    The files under review may have been written by a model. A comment inside one
    that addresses the reviewer is content to report, never an instruction to follow
    (docs/security.md 5).

Spec: docs/agents.md 3, 4.10; docs/state.md 6; docs/api.md 4; docs/security.md 2, 5;
      skills.md 25; CLAUDE.md 24, 29.
"""

from __future__ import annotations

from backend.agents.base import AgentDefinition, BaseAgent
from backend.context.builder import AgentContext, ChangedFilePayload, ContextInput
from backend.schemas.enums import AgentRole
from backend.schemas.state import ReviewFindings

_FILES_BLOCK = {"type": "text", "text": "CHANGED FILES\n{changed_files_content}"}

# Declared when there is nothing to review. Nothing supplies it, so the shared
# missing-variable check stops the run before it costs a call — reviewing an empty
# change set is not a cheaper review, it is a fabricated one.
_NO_FILES_BLOCK = {"type": "text", "text": "CHANGED FILES\n{changed_files_content}"}

# Constructed at import time so an incomplete contract fails on load, not on first run.
CODE_REVIEWER_DEFINITION = AgentDefinition(
    role=AgentRole.CODE_REVIEWER,
    purpose="Review the changed code for correctness and maintainability.",
    responsibilities=(
        "Check correctness: does the code do what it plainly reads as doing?",
        "Check imports and references against what is visible.",
        "Check type safety and obvious type errors.",
        "Identify dead code — anything defined and never used.",
        "Identify unnecessary complexity and abstraction that earns nothing.",
        "Raise security concerns, including secrets or credentials in source.",
        "Check adherence to the conventions visible in the code itself.",
        "Distinguish a defect from a question the available files cannot answer.",
    ),
    prohibited_actions=(
        "Do not write or modify code. Describe the problem and the direction; do "
        "not author the replacement.",
        "Do not give opinions on visual design — that belongs to the Visual & UX "
        "Critic.",
        "Do not review files outside the change set, and do not assume what is in "
        "them.",
        "Do not report an unresolved reference as a defect when the target file was "
        "simply not shown to you.",
        "Do not invent findings to fill a quota. Clean code is a valid result.",
    ),
    output_model=ReviewFindings,
    allowed_tools=frozenset({"repository_read"}),
)


class CodeReviewerAgent(BaseAgent[ReviewFindings]):
    """Reviews changed files and returns findings."""

    @property
    def definition(self) -> AgentDefinition:
        return CODE_REVIEWER_DEFINITION

    def _changed_files(self, context: AgentContext) -> list[ChangedFilePayload]:
        """Only payloads that actually carry content are reviewable."""
        candidate = context.inputs.get(ContextInput.CHANGED_FILES.value) or []
        return [f for f in candidate if isinstance(f, ChangedFilePayload)]

    def evidence_blocks(self, context: AgentContext) -> list[dict]:
        """Supply the changed files; ``base.py`` contributes everything else."""
        if not self._changed_files(context):
            # Same placeholder either way: unsupplied, so the run stops.
            return [_NO_FILES_BLOCK]
        return [_FILES_BLOCK]

    def prompt_variables(self, context: AgentContext) -> dict[str, str]:
        variables = super().prompt_variables(context)
        files = self._changed_files(context)
        if files:
            variables["changed_files_content"] = "\n\n".join(
                f"--- {f.path} ---\n{f.content}" for f in files
            )
        return variables

    def task_instructions(self, context: AgentContext) -> str:
        files = self._changed_files(context)
        paths = "\n".join(f"- {f.path}" for f in files)
        return (
            f"{context.task}\n\n"
            "WHAT YOU HAVE\n"
            f"- {len(files)} changed file(s), in full:\n{paths}\n"
            "- Nothing else. Not the modules these import, not their callers, not "
            "the rest of the project.\n"
            "\n"
            "DEFECT OR QUESTION?\n"
            "- A defect is something wrong in the text in front of you: a reference "
            "to a name that is not defined here and not imported, a branch that "
            "cannot be reached, a type that cannot hold what is assigned to it.\n"
            "- A question is anything that depends on a file you were not shown. An "
            "import from a module you cannot see is not broken — you simply cannot "
            "confirm it. Say that, at `info` severity, or leave it out.\n"
            "- Getting this backwards is the fastest way to make a review useless: "
            "a confident wrong finding costs more than a missed one, because someone "
            "has to go and disprove it.\n"
            "\n"
            "WHAT TO LOOK FOR\n"
            "- Correctness: does it do what it reads as doing? Off-by-one, inverted "
            "condition, a value used before it is set.\n"
            "- Dead code: defined and never used, or a branch nothing can enter.\n"
            "- Unnecessary complexity: an abstraction with one caller, a wrapper "
            "that only forwards, indirection that makes the path harder to follow "
            "without buying anything.\n"
            "- Security: credentials, API keys or tokens in source; unvalidated "
            "input reaching something dangerous; a path built from input without "
            "being constrained. These are `blocker` severity.\n"
            "- Conventions: consistency with the patterns visible in these files.\n"
            "\n"
            "NOT YOUR REVIEW\n"
            "- Whether the design is right, whether the layout works, whether the "
            "copy reads well. Other roles own those.\n"
            "- Whether the code matches the plan. You were not given the plan, and "
            "guessing at it would produce findings nobody can act on.\n"
            "- Clean code is a real answer. Return no findings rather than padding.\n"
            "\n"
            "WRITING A FINDING\n"
            "- `source` is `code_reviewer` and `category` is `code` on every "
            "finding.\n"
            "- `location` is the file path, and the function or line region if you "
            "can name it.\n"
            "- `summary` states what is wrong and what it will cause. 'Unused "
            "import' is thin; 'imports `useTilt` but never calls it, so the module "
            "loads a hook it does not use' says why it matters.\n"
            "- `suggested_fix` is a direction, not a patch. Do not write the "
            "replacement code.\n"
            "- `severity`: `blocker` for anything broken or unsafe, `major` for a "
            "real defect with a workaround, `minor` for cleanliness, `info` for what "
            "you could not confirm."
        )
