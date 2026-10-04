"""Local instruct-LLM backend: browser decisions and text values without API keys.

Runs the model in process with transformers, or against any OpenAI-compatible
chat endpoint when LOCAL_LLM_BASE_URL is set (for example `mlx_lm.server`).
"""

import json
import os
import re
import time

from .model import action_space

DEFAULT_MODEL = "Qwen/Qwen3-1.7B"
_RUNTIME = {}

DECISION_SYSTEM = """You control a browser to complete one goal. Choose the single next action.
Reply with JSON only: {"operation": "<operation>", "target": "<offered target or null>"}.
Use only operations and targets visible in the request; never invent an index.
Never TYPE_TEXT a field that already contains the goal's value; move to the next unmet
requirement. Set requested filters (SELECT, checkbox CLICK) before submitting. Do not repeat an
action whose effect is already visible; an action that changed nothing must not be tried again.
Prefer a useful visible operation over WAIT. DONE means the page already satisfies every
requirement; a matching result alone does not prove a requested filter was set. BLOCKED means no
listed operation can make progress. WAIT is only for loading or an absent control, and takes no target."""


def model_name():
    return os.environ.get("LOCAL_LLM_MODEL") or os.environ.get("LOCAL_TEXT_MODEL") or DEFAULT_MODEL


def load_runtime():
    if "model" not in _RUNTIME:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        name = model_name()
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        tokenizer = AutoTokenizer.from_pretrained(name)
        model = AutoModelForCausalLM.from_pretrained(
            name, dtype=torch.float16 if device == "mps" else torch.float32
        )
        _RUNTIME.update(name=name, device=device, tokenizer=tokenizer, model=model.to(device).eval())
    return _RUNTIME


def local_chat(messages, max_new_tokens):
    import torch

    runtime = load_runtime()
    tokenizer = runtime["tokenizer"]
    try:
        text = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
    except Exception:
        text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt").to(runtime["device"])
    with torch.no_grad():
        output = runtime["model"].generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    return tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True)


def remote_chat(messages, max_new_tokens):
    from .model import post_json

    key = os.environ.get("LOCAL_LLM_API_KEY") or "local"
    body = {
        "model": model_name(),
        "messages": messages,
        "max_tokens": max_new_tokens,
        "temperature": 0,
    }
    if os.environ.get("LOCAL_LLM_REASONING"):
        body["reasoning_effort"] = os.environ["LOCAL_LLM_REASONING"]
    if os.environ.get("LOCAL_LLM_JSON") == "1":
        body["response_format"] = {"type": "json_object"}
    result = post_json(os.environ["LOCAL_LLM_BASE_URL"].rstrip("/") + "/chat/completions", key, body)
    return result["choices"][0]["message"]["content"]


def chat(messages, max_new_tokens=160):
    if os.environ.get("LOCAL_LLM_BASE_URL"):
        return remote_chat(messages, max_new_tokens)
    return local_chat(messages, max_new_tokens)


def extract_json(content):
    match = re.search(r"\{.*\}", content, re.DOTALL)
    return json.loads(match.group(0)) if match else None


DECISION_EXAMPLES = (
    (
        """Goal: Book a table for four people in Porto.
Elements:
[1] searchbox City value=""
[2] button Search
Available operations and targets:
CLICK: 2
TYPE_TEXT: 1
No target: BLOCKED, DONE, WAIT
Recent actions: (none)
Choose the next action.""",
        '{"operation": "TYPE_TEXT", "target": "1"}',
    ),
    (
        """Goal: Book a table for four people in Porto.
Elements:
[1] searchbox City value="Porto"
[2] button Search
Available operations and targets:
CLICK: 2
No target: BLOCKED, DONE, WAIT
Recent actions: City "Porto" (page changed)
Choose the next action.""",
        '{"operation": "CLICK", "target": "2"}',
    ),
    (
        """Goal: Book a table for four people in Porto and open Trindade.
Elements:
[1] link Trindade
[2] text Trindade — 4 guests — 7:30 PM
Available operations and targets:
CLICK: 1
No target: BLOCKED, DONE, WAIT
Recent actions: City "Porto" (page changed); Search (page changed); Trindade (page changed)
Choose the next action.""",
        '{"operation": "DONE", "target": null}',
    ),
)


def decision_messages(prompt):
    messages = [{"role": "system", "content": DECISION_SYSTEM}]
    for example, reply in DECISION_EXAMPLES:
        messages.append({"role": "user", "content": example})
        messages.append({"role": "assistant", "content": reply})
    messages.append({"role": "user", "content": prompt})
    return messages


def option_text(option):
    return option["label"].split(" → ")[-1][:40]


def element_line(element):
    line = "[%s] %s %s" % (element["index"], element.get("role", ""), element.get("label", ""))
    if element.get("value") and "checked" not in element:
        line += ' value="%s"' % str(element["value"])[:40]
    for flag in ("checked", "selected", "expanded"):
        if flag in element:
            line += " %s=%s" % (flag, element[flag])
    if element.get("options"):
        options = ["%s %s" % (o["index"], option_text(o)) for o in element["options"][:12]]
        line += " options=[%s%s]" % ("; ".join(options), " ..." if len(element["options"]) > 12 else "")
    return line


def operations_block(elements, controls):
    by_operation = {}
    for element in elements:
        for operation in element.get("operations", []):
            by_operation.setdefault(operation, []).append(element["index"])
    lines = []
    for operation in sorted(by_operation):
        if operation == "SELECT":
            options = [
                "%s %s" % (o["index"], option_text(o)) for element in elements for o in element.get("options", [])
            ]
            lines.append("SELECT: " + "; ".join(options))
        else:
            lines.append("%s: %s" % (operation, ", ".join(by_operation[operation])))
    lines.append("No target: " + ", ".join(sorted(set(controls) | {"DONE", "BLOCKED"})))
    return "Available operations and targets:\n" + "\n".join(lines)


def decision_prompt(state, goal, history, elements, controls):
    def summary(h):
        change = h.get("page_changed")
        return "%s%s%s" % (
            h.get("action", ""),
            ' "%s"' % h["text"] if h.get("text") else "",
            "" if change is None else (" (page changed)" if change else " (no page change)"),
        )

    actions = [summary(h) for h in history[-8:]]
    lines = [
        "Goal: %s" % goal,
        "Page: %s" % state["title"],
        "Elements:",
        *[element_line(element) for element in elements],
        operations_block(elements, controls),
        "Page text: %s" % " ".join(state["text"].split())[:500],
        "Recent actions: %s" % ("; ".join(actions) if actions else "(none)"),
        "Choose the next action.",
    ]
    return "\n".join(lines)


def match_target(value, offered):
    if value in offered:
        return value
    token = str(value).strip().split(" ", 1)[0].rstrip(",")
    return token if token in offered else None


def parse_decision(content, operations, targets):
    output = extract_json(content)
    if not isinstance(output, dict) or "operation" not in output or "target" not in output:
        raise ValueError("expected an operation and a target key")
    operation = output["operation"]
    target = output["target"]
    if operation not in operations:
        raise ValueError("%r is not an offered operation" % (operation,))
    if operation in targets:
        matched = match_target(target, targets[operation])
        if matched is None:
            raise ValueError("%r is not an offered target for %s" % (target, operation))
        target = matched
    elif target not in (None, "null", ""):
        raise ValueError("%s does not take a target" % (operation,))
    return operation, target if operation in targets else None


ANALYSIS_SYSTEM = """You audit a browser page against a goal. In at most three short lines, state which
requirements of the goal the page already satisfies and which are still missing, judged only from
the page state. End with "NEXT: <the one requirement to satisfy next>"."""


def analyze(chat_fn, prompt):
    return chat_fn([{"role": "system", "content": ANALYSIS_SYSTEM}, {"role": "user", "content": prompt}], 120)


def choose(state, goal, history, chat_fn=None):
    elements, targets, controls = action_space(state["actions"])
    operations = set(targets) | set(controls) | {"DONE", "BLOCKED"}
    prompt = decision_prompt(state, goal, history, elements, controls)
    chat_fn = chat_fn or chat
    started = time.perf_counter()
    audit = analyze(chat_fn, prompt)
    messages = decision_messages(prompt + "\nProgress audit:\n" + audit)
    error = None
    for _attempt in range(2):
        content = chat_fn(list(messages), 60)
        try:
            operation, target = parse_decision(content, operations, targets)
            break
        except (ValueError, TypeError) as problem:
            error = problem
            messages.extend(
                [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "Invalid choice (%s). Reply with JSON only, for example "
                            '{"operation": "CLICK", "target": "3"} or {"operation": "WAIT", "target": null}.'
                            % problem
                        ),
                    },
                ]
            )
    else:
        raise ValueError("Local model returned no valid choice; no action executed.") from error
    if operation in targets:
        choice = targets[operation][target]["id"]
        probabilities = {targets[operation][key]["id"]: float(key == target) for key in targets[operation]}
        target_probabilities = {key: float(key == target) for key in targets[operation]}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities = {choice: 1.0}
        target_probabilities = {}
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": 1.0,
        "probabilities": probabilities,
        "operation_probabilities": {name: float(name == operation) for name in sorted(operations)},
        "target_probabilities": target_probabilities,
        "target_confidence": 1.0 if target is not None else None,
        "raw_answers": {},
        "model": model_name(),
        "usage": {},
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {"model": model_name(), "messages": messages},
    }
