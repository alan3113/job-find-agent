from __future__ import annotations

import uvicorn


def main() -> None:
    # Localhost binding prevents remote access to review and approval actions.
    uvicorn.run("job_agent.dashboard.app:app", host="127.0.0.1", port=8000)

