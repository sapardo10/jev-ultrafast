"""Local TYPE_TEXT helper: a small instruct model writes field values, no API key."""

import os
import time

from .local_llm import DEFAULT_MODEL, chat, extract_json

EXAMPLES = (
    ("Book a flight from Paris to Rome on March 3.", "Where to?", '{"text": "Rome"}'),
    ("Book a flight from Paris to Rome on March 3.", "Where from?", '{"text": "Paris"}'),
    ("Book a flight from Paris to Rome on March 3.", "Departure", '{"text": "March 3"}'),
    ("Reserve a table for four people in Lisbon.", "Guests", '{"text": "4"}'),
    ("Book a flight from Paris to Rome on March 3.", "Passport number", '{"text": null}'),
)


def question(goal, label, details=""):
    lines = ["Goal: %s" % goal]
    if details:
        lines.append(details)
    lines.append('Which value belongs in the "%s" field? JSON only.' % label)
    return "\n".join(lines)


def render_context(context):
    field = context.get("field") or {}
    page = context.get("page") or {}
    lines = []
    if field.get("value"):
        lines.append("The field already says: %s" % field["value"])
    if page.get("text"):
        lines.append("Page text: %s" % " ".join(page["text"].split())[:1200])
    actions = [str(a.get("action") or a.get("text")) for a in context.get("recent_actions") or []]
    if actions:
        lines.append("Recent actions: " + "; ".join(actions))
    return "\n".join(lines)


def messages(context):
    goal = context.get("goal", "")
    label = (context.get("field") or {}).get("label", "")
    prompt = []
    for example_goal, example_label, example_text in EXAMPLES:
        prompt.append({"role": "user", "content": question(example_goal, example_label)})
        prompt.append({"role": "assistant", "content": example_text})
    prompt.append({"role": "user", "content": question(goal, label, render_context(context))})
    return prompt


def parse(content):
    output = extract_json(content)
    value = output["text"]
    if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError()
    return value


ATTEMPTS = 2  # generation is read-only (nothing typed yet), so a malformed answer may be asked again


def generate(context, writer=None):
    started = time.perf_counter()
    for attempt in range(ATTEMPTS):
        content = (writer or chat)(messages(context))
        try:
            value = parse(content)
            break
        except (ValueError, KeyError, TypeError):
            if attempt == ATTEMPTS - 1:
                raise ValueError(
                    f"Text helper returned no valid field value; nothing typed. Last answer: {content[:120]!r}"
                ) from None
    return value, {
        "model": os.environ.get("LOCAL_LLM_MODEL") or os.environ.get("LOCAL_TEXT_MODEL") or DEFAULT_MODEL,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": {},
    }
