"""Every agent must deliver approved decisions to the prompt. No network, no API key."""
import os, sys, base64
sys.path.insert(0, os.getcwd()); sys.dont_write_bytecode = True
from backend.context.builder import ContextBuilder, RenderedPreviewPayload, ReferenceImagePayload
from backend.schemas.enums import AgentRole, WorkflowStage
from backend.schemas.state import (ProjectState, DesignSpec, Plan, ApprovedDecision,
                                   NormalizedRequirements)
from backend.agents.requirement_analyzer import RequirementAnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.design_director import DesignDirectorAgent
from backend.agents.reference_analyzer import ReferenceAnalyzerAgent
from backend.agents.frontend_architect import FrontendArchitectAgent
from backend.agents.visual_ux_critic import VisualUxCriticAgent
from backend.agents.anti_slop_reviewer import AntiSlopReviewerAgent

ok = lambda b: "PASS" if b else "**FAIL**"
MARK = "MARKER-APPROVED-DECISION"
SHOT = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"p" * 200).decode()

cb = ContextBuilder()
s = ProjectState(user_request="build a coffee shop site")
s.design_spec = DesignSpec(font_families=["Inter"])
s.plan = Plan(objective="sell coffee", acceptance_criteria=["nav works"])
s.normalized_requirements = NormalizedRequirements(goal="g", input_density="sparse")
s.approved_decisions.append(
    ApprovedDecision(decision_id="d1", stage=WorkflowStage.DESIGN, statement=MARK)
)
prev = RenderedPreviewPayload(url="u", screenshot_base64=SHOT, screenshot_media_type="image/png")
img = ReferenceImagePayload(image_id="i", content_type="image/png", data_base64=SHOT)

cases = [
    ("requirement_analyzer", RequirementAnalyzerAgent, AgentRole.REQUIREMENT_ANALYZER, {}),
    ("planner", PlannerAgent, AgentRole.PLANNER, {}),
    ("design_director", DesignDirectorAgent, AgentRole.DESIGN_DIRECTOR, {}),
    ("reference_analyzer", ReferenceAnalyzerAgent, AgentRole.REFERENCE_ANALYZER,
     dict(reference_image=img)),
    ("frontend_architect", FrontendArchitectAgent, AgentRole.FRONTEND_ARCHITECT,
     dict(component_inventory=["ui/button"])),
    ("visual_ux_critic", VisualUxCriticAgent, AgentRole.VISUAL_UX_CRITIC,
     dict(rendered_preview=prev)),
    ("anti_slop_reviewer", AntiSlopReviewerAgent, AgentRole.ANTI_SLOP_REVIEWER,
     dict(rendered_preview=prev, findings=[])),
]

print("  %-22s %-9s %-10s %s" % ("AGENT", "GRANTED", "IN PROMPT", "VERDICT"))
bad = 0
for name, cls, role, kw in cases:
    ctx = cb.build(s, role, task="t", **kw)
    agent = cls(None)
    body = agent.prompt_template(ctx).format_messages(**agent.prompt_variables(ctx))[1].content
    text = (
        " ".join(b.get("text", "") for b in body if isinstance(b, dict) and b.get("type") == "text")
        if isinstance(body, list) else body
    )
    granted = len(ctx.constraints) > 0
    present = MARK in text
    good = present == granted
    bad += (not good)
    print("  %-22s %-9s %-10s %s" % (name, granted, present, ok(good)))

print()
print("  agents dropping granted constraints:", bad, ok(bad == 0))
