"""Local Laya decision engine: the same operation/target choice contract, no API key."""

import os
import time

from .model import action_space, validate_choice
from .questions import TARGET, next_action

OPERATIONS = {
    "CLICK": "Click a visible element, button, link, menu option, autocomplete suggestion, or calendar day.",
    "TYPE_TEXT": "Type or replace text in a visible editable field. A helper writes the value from the goal.",
    "SELECT": "Choose an observed dropdown value.",
}
DONE = "Every requirement is visibly satisfied."
BLOCKED = "No supported operation can progress."

_ENGINE = None


def load_engine():
    global _ENGINE
    if _ENGINE is None:
        import laya

        subfolder = os.environ.get("LAYA_SUBFOLDER") or "typed-decisions"
        _ENGINE = laya.load(
            os.environ.get("LAYA_MODEL") or "convaiinnovations/laya",
            device=os.environ.get("LAYA_DEVICE") or None,
            subfolder=None if subfolder == "root" else subfolder,
        )
        _ENGINE.cfg["max_len"] = int(os.environ.get("LAYA_MAX_LEN") or "2048")
        _ENGINE.cfg["head_max_len"] = int(os.environ.get("LAYA_HEAD_MAX_LEN") or "512")
    return _ENGINE


def unload_engine():
    global _ENGINE
    _ENGINE = None


def chunk_size():
    return max(2, int(os.environ.get("LAYA_CHUNK_SIZE") or "24"))


def compact_element(element):
    item = {
        key: element[key]
        for key in ("index", "label", "role", "value", "checked", "selected", "expanded", "operations")
        if key in element
    }
    if "options" in element:
        item["option_count"] = len(element["options"])
        item["options"] = ["%s %s" % (o["index"], o["label"][:40]) for o in element["options"][:20]]
    return item


def native_history(history):
    """Screens opened and values typed, as plain facts. A log of "action, page_changed" makes nimble read the
    opened screen as finished work and scroll or back out (CLICK 0.30 vs 0.95 on Settings > Display)."""
    opened = [
        h["action"].split("\n")[0].split(" — ")[0].strip()[:40]
        for h in history[-8:]
        if h.get("kind") == "click" and h.get("page_changed")
    ]
    typed = [{"field": h["action"].split(" — ")[0][:40], "text": h["text"]} for h in history[-8:] if h.get("text")]
    return {"opened_screens": opened, **({"typed": typed} if typed else {})}


def page_state(state, goal, history, elements):
    where = (
        {"app": state["package"], "screen": state["url"].split("/", 1)[-1]}
        if state.get("native")
        else {"url": state["url"], "title": state["title"]}
    )
    recent = (
        native_history(history)
        if state.get("native")
        else {
            "recent_actions": [
                {key: h.get(key) for key in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ]
        }
    )
    return {
        "goal": goal,
        "screen" if state.get("native") else "page": where,
        "elements": [compact_element(element) for element in elements],
        "page_text": state["text"][:4000],
        **recent,  # last: nimble weighs a history key placed before the page text over the controls themselves
    }


def option_label(key, action):
    label = action["label"].replace(" → ", ": ")[:80]
    if action["kind"] == "select":
        return label
    value = action.get("current_value", action.get("value", ""))
    if value and str(value) not in label:
        label += " = " + str(value)[:40]
    flags = " ".join("%s=%s" % (key, action[key]) for key in ("checked", "selected", "expanded") if key in action)
    return label + ((" " + flags) if flags else "")


def target_instructions(goal, operation):
    return "Goal: %s\nOperation: %s\n%s" % (goal, operation, TARGET)


def split_target(operation, candidates, goal, chunk):
    qid = operation.lower() + "_target"
    keys = list(candidates)
    groups = [keys[i : i + chunk] for i in range(0, len(keys), chunk)]
    questions = {}
    if len(groups) == 1:
        questions[qid] = {
            "type": "choice",
            "criteria": {key: option_label(key, candidates[key]) for key in keys},
            "instructions": target_instructions(goal, operation),
        }
        return questions, None
    questions[qid + "_group"] = {
        "type": "choice",
        "criteria": {
            "%d" % (i + 1): "candidates %s through %s" % (group[0], group[-1]) for i, group in enumerate(groups)
        },
        "instructions": (
            "Goal: %s\nOperation: %s\nChoose which group of candidate elements contains the best target. "
            "Every candidate is listed in the state." % (goal, operation)
        ),
    }
    for i, group in enumerate(groups):
        questions["%s_%d" % (qid, i + 1)] = {
            "type": "choice",
            "criteria": {key: option_label(key, candidates[key]) for key in group},
            "instructions": target_instructions(goal, operation),
        }
    return questions, groups


def choose_target(answers, operation, candidates, groups):
    qid = operation.lower() + "_target"
    if groups is None:
        answer = validate_choice(answers.get(qid, {}), candidates)
        return answer["choice"], answer["probabilities"], answer["confidence"], answer
    group_answer = validate_choice(answers.get(qid + "_group", {}), ["%d" % (i + 1) for i in range(len(groups))])
    combined = {key: 0.0 for key in candidates}
    for i, group in enumerate(groups):
        answer = validate_choice(answers.get("%s_%d" % (qid, i + 1), {}), group)
        weight = group_answer["probabilities"]["%d" % (i + 1)]
        for key in group:
            combined[key] = weight * answer["probabilities"][key]
    total = sum(combined.values()) or 1.0
    combined = {key: value / total for key, value in combined.items()}
    target = max(combined, key=combined.get)
    return target, combined, round(combined[target], 4), group_answer


def choose(state, goal, history, engine=None):
    elements, targets, controls = action_space(state["actions"])
    labels = {key: OPERATIONS[key] for key in targets}
    labels.update({key: value["label"] for key, value in controls.items()})
    labels.update(DONE=DONE, BLOCKED=BLOCKED)
    questions = {
        "operation": {
            "type": "choice",
            "criteria": labels,
            "instructions": "Goal: %s\n%s" % (goal, next_action(state)),
        }
    }
    layouts = {}
    for operation, candidates in targets.items():
        part, groups = split_target(operation, candidates, goal, chunk_size())
        questions.update(part)
        layouts[operation] = groups
    body = page_state(state, goal, history, elements)
    started = time.perf_counter()
    result = (engine or load_engine()).predict(body, questions)
    answers = result.get("answers", {})
    operation_answer = validate_choice(answers.get("operation", {}), labels)
    operation = operation_answer["choice"]
    target = None
    target_probs = {}
    target_confidence = None
    if operation in targets:
        candidates = targets[operation]
        target, target_probs, target_confidence, _ = choose_target(answers, operation, candidates, layouts[operation])
        choice = candidates[target]["id"]
        probabilities = {candidates[key]["id"]: value for key, value in target_probs.items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities = {choice: operation_answer["probabilities"][operation]}
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_probs,
        "target_confidence": target_confidence,
        "raw_answers": answers,
        "model": result.get("model", "laya"),
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {
            "model": os.environ.get("LAYA_MODEL") or "convaiinnovations/laya",
            "state": body,
            "questions": questions,
        },
    }
