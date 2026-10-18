# DETROIT

**An agentic AI website builder that turns natural-language ideas into structured plans and web projects.**

DETROIT combines AI-driven planning, human-in-the-loop approvals, graph-based orchestration, and quality checks in one workflow. It is built to make website generation more transparent, controllable, and user-friendly.

## Key Features

- **Agentic workflow:** Coordinates planning and build stages with LangGraph.
- **Human-in-the-loop (HITL):** Lets users review plans and provide feedback before the workflow continues.
- **Live activity updates:** Streams graph-stage events through an AG-UI-compatible event stream and displays them in the chat workspace.
- **Quality gate:** Keeps Impeccable evaluation mandatory; a failed or unavailable gate must not be treated as a successful build.
- **Project persistence:** Stores project state and provides authenticated, user-scoped project access.
- **User dashboard:** Shows persisted project counts, statuses, activity timestamps, and iteration counts instead of fabricated metrics.
- **GitHub workflow:** Supports a review-and-approval flow for pushing generated changes to an existing repository.

## Tech Stack

- **Frontend:** React, TypeScript, Tailwind CSS
- **Backend:** Python, FastAPI, Pydantic
- **AI orchestration:** LangChain, LangGraph
- **Agent UI events:** AG-UI protocol over Server-Sent Events (SSE)
- **Auth and persistence:** Supabase, PostgreSQL
- **Repository integration:** GitHub API
- **UI quality checks:** Impeccable

## Workflow

1. Submit a website idea.
2. Review the generated plan and respond to any HITL questions.
3. Follow build-stage activity in the chat workspace.
4. Run the mandatory quality evaluation and any required fixes.
5. Review the project and, when configured, approve a push to an existing GitHub repository.

## Getting Started

### Prerequisites

- Python and Node.js/npm
- A configured Supabase project
- A valid Anthropic API key for live AI generation
- A GitHub token with appropriately scoped repository permissions for GitHub push functionality

### Setup

1. Clone the repository and enter its directory.
2. Create a Python virtual environment and install the backend dependencies:

   ```bash
   python -m venv .venv
   # Windows PowerShell
   .venv\Scripts\Activate.ps1
   # macOS/Linux: source .venv/bin/activate
   python -m pip install -r backend/requirements.txt
   ```

3. Configure the backend environment using `.env.example` and the required settings. Configure the frontend environment using the project's frontend configuration. Keep all secrets in local environment files; never commit them.
4. Start the backend from the repository root:

   ```bash
   python -m backend.run
   ```

5. In a second terminal, start the frontend:

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

Use the local URLs printed by the development servers. Refer to the project configuration and documentation for required environment variable names and service settings.

## Development Status

DETROIT is under active development. The AG-UI event stream and dashboard changes have passed the automated tests reported during implementation, but a live AI build and signed-in browser-level verification are still pending. The backend's real tool-call start/result events also require further integration, so the current activity timeline should not be assumed to show every internal tool execution.

## Security Notes

- Never commit API keys, access tokens, or local `.env` files.
- Project access must be enforced by the backend and database policies, not frontend filtering alone.
- Use a GitHub token limited to the target repository and grant only the permissions required for the operation.

---

**Status:** Active development
