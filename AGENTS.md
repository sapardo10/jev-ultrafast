# Jev Ultrafast

Read README.md before editing. Keep the loop small: page -> indexed elements -> operation + target -> execution.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values.
- `mcp_server.py` exposes `browser_task` to other agents: it starts its own Chrome (CDP 9333) and harness daemon (`BU_NAME=jev-mcp`), and falls back to the in-process model when the configured endpoint is down. One browser session per call; the tool must keep returning the same JSON trace shape.
- The default decision backend is a local instruct LLM (`local_llm.py`); `laya_model.py` answers the same questions with Laya, and `model.choose_typesafe` handles the remote TypeSafe path. Consume only the selected operation's target. Large Laya target sets split into grouped heads.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- TYPE_TEXT invokes the text helper: the OpenAI-compatible model when TEXT_MODEL_API_KEY is set, the local instruct model otherwise (same runtime as the default decision backend). Cache a stale retry's value only while its entire helper input is identical.
- Never retry a browser mutation. Log execution before observing its result.
- Screenshots are optional; the model does not consume them. Keep demonstration footage at its original speed.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs or download models.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep examples, README claims, raw evidence, and model-call counts consistent.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, node --check jev_ultrafast/static/app.js, uv build.
