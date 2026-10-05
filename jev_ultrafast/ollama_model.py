"""Ollama System One decision engine (Ollama 0.35+, e.g. `ollama pull nimble`): local, no API key.

Reuses the Laya state and option labels but asks in stages. Nimble lets sibling questions in one
request sway each other (target heads pulled the operation to SCROLL_DOWN on a filled search box),
and it cannot map an opaque "candidates 25 through 48" group label back to elements. So the
operation is asked alone, then the chosen operation's targets in chunks, then one final round
among each chunk's leaders. Ollama only accepts string descriptions and 2–26 candidates.
"""

import os
import time

from . import laya_model
from .model import action_space, post_json, validate_choice
from .questions import next_action

MAX_CANDIDATES = 26
FINALISTS_PER_CHUNK = 2


class OllamaEngine:
    def __init__(self, base_url=None, model=None):
        base = base_url or os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434"
        self.url = base.rstrip("/") + "/v1/systemone"
        self.model = model or os.environ.get("OLLAMA_MODEL") or "nimble"

    def predict(self, state, questions):
        # A question with one candidate has a certain answer; Ollama rejects it, so answer it here.
        certain = {qid: next(iter(q["criteria"])) for qid, q in questions.items() if len(q["criteria"]) == 1}
        for qid, question in questions.items():
            if len(question["criteria"]) > MAX_CANDIDATES:
                raise RuntimeError(f"{qid} has {len(question['criteria'])} options; no action executed.")
        asked = {qid: q for qid, q in questions.items() if qid not in certain}
        result = {"model": self.model, "answers": {}, "usage": {}}
        if asked:
            result = post_json(self.url, None, {"model": self.model, "state": state, "questions": asked})
        for qid, key in certain.items():
            result["answers"][qid] = {"type": "choice", "choice": key, "probabilities": {key: 1.0}, "confidence": 1.0}
        return result


def add_usage(total, usage):
    for key, value in (usage or {}).items():
        if type(value) in (int, float):
            total[key] = total.get(key, 0) + value


def choose_target(engine, body, goal, operation, candidates, usage):
    """Each chunk's leaders advance until one question holds every remaining candidate."""
    keys, rounds = list(candidates), 0
    while True:
        chunks = [keys[i : i + MAX_CANDIDATES] for i in range(0, len(keys), MAX_CANDIDATES)]
        questions = {
            "%s_target_%d" % (operation.lower(), i + 1): {
                "type": "choice",
                "criteria": {key: laya_model.option_label(key, candidates[key]) for key in chunk},
                "instructions": laya_model.target_instructions(goal, operation),
            }
            for i, chunk in enumerate(chunks)
        }
        result = engine.predict(body, questions)
        rounds += 1
        add_usage(usage, result.get("usage"))
        answers = [validate_choice(result["answers"].get(qid, {}), chunk) for qid, chunk in zip(questions, chunks)]
        if len(chunks) == 1:
            return answers[0], rounds
        keys = [
            key
            for answer in answers
            for key in sorted(answer["probabilities"], key=answer["probabilities"].get, reverse=True)[
                :FINALISTS_PER_CHUNK
            ]
        ]


def shares_word(label, goal):
    words = {w for w in "".join(c if c.isalnum() else " " for c in label.lower()).split() if len(w) >= 4}
    return any(w in goal.lower() for w in words)


PENDING = {"done", "apply", "confirm", "ok"}  # picker closers; Search/Next exist on ordinary pages too


def pending_confirm(state):
    """A visible Done/Apply/Search control means a picker or form the last click opened is not finished."""
    def first_word(label):
        words = "".join(c if c.isalnum() else " " for c in label.lower()).split()
        return words[0] if words else ""

    return any(a["kind"] == "click" and first_word(a["label"]) in PENDING for a in state.get("actions", []))


UNDO = {"back", "volver", "atras", "atrás", "close", "cerrar", "cancel", "cancelar"}


def goal_just_reached(history, goal):
    """The last click was on exactly what the goal names (its label is most of the goal). A late-animating panel can
    still read as unchanged, so the page-changed flag is not required: undoing that click is never the next step."""
    last = history[-1] if history else None
    if not last or last.get("kind") != "click":
        return False
    clicked = last["action"].split("\n")[0].strip()[:60]
    return bool(clicked) and clicked.lower() in goal.lower() and len(clicked) * 2 >= len(goal.strip())


def drop_undo_targets(targets, history, goal):
    """Do not offer Back/Close right after the goal's own control was opened: undoing it is never the next step."""
    if not goal_just_reached(history, goal) or "CLICK" not in targets:
        return targets
    kept = {
        key: a
        for key, a in targets["CLICK"].items()
        if ("".join(c if c.isalnum() else " " for c in a["label"].lower()).split() or [""])[0] not in UNDO
    }
    if not kept:
        return targets
    return {**targets, "CLICK": kept}


def done_label(history, state, goal=""):
    """Nimble weighs option text over instructions: cite the evidence that the last click took effect."""
    last = history[-1] if history else None
    if not last or last.get("kind") != "click" or not last.get("page_changed"):
        return laya_model.DONE
    clicked = last["action"].split("\n")[0].strip()[:60]
    if pending_confirm(state):
        return laya_model.DONE
    if state.get("native") and not shares_word(clicked, goal):
        # A native "Allow"/"Next" tap unrelated to the goal is no evidence of completion; do not nudge to DONE.
        return laya_model.DONE
    label = "%s Last action clicked '%s' and the page changed; if that completes the goal, choose DONE." % (
        laya_model.DONE,
        clicked,
    )
    if clicked and clicked.lower() in goal.lower() and len(clicked) * 2 >= len(goal.strip()):
        label += " The goal is just to open '%s', and it is now open: choose DONE." % clicked
    return label


def choose(state, goal, history, engine=None):
    engine = engine or OllamaEngine()
    elements, targets, controls = action_space(state["actions"])
    targets = drop_undo_targets(targets, history, goal)
    labels = {key: laya_model.OPERATIONS[key] for key in targets}
    if "TYPE_TEXT" in labels:
        # Nimble weighs option text over instructions, so say which fields still need typing.
        empty = [a["label"].rstrip(": ")[:30] for a in targets["TYPE_TEXT"].values() if not str(a.get("value") or "")]
        labels["TYPE_TEXT"] += " Empty fields: %s." % ", ".join(empty[:6]) if empty else " Every field has a value."
    labels.update({key: value["label"] for key, value in controls.items()})
    labels.update(DONE=done_label(history, state, goal), BLOCKED=laya_model.BLOCKED)
    last = history[-1] if history else {}
    if targets and last.get("kind") == "click" and last.get("page_changed") and pending_confirm(state):
        # The click just opened/changed a picker that still shows Done/Apply: finishing it is the next step, not DONE.
        labels.pop("DONE")
    question = {"type": "choice", "criteria": labels, "instructions": "Goal: %s\n%s" % (goal, next_action(state))}
    body = laya_model.page_state(state, goal, history, elements)
    usage = {}
    started = time.perf_counter()
    result = engine.predict(body, {"operation": question})
    add_usage(usage, result.get("usage"))
    operation_answer = validate_choice(result["answers"].get("operation", {}), labels)
    operation = operation_answer["choice"]
    can_scroll = state.get("native") and "SCROLL_DOWN" in controls  # offered only until a swipe moved nothing
    if operation == "BLOCKED" and targets and (not history or can_scroll):
        # Landing pages hide the goal behind an entry control; a native form hides its next field below the
        # keyboard. Nothing says the path is closed, so BLOCKED is premature.
        labels.pop("BLOCKED"), labels.pop("DONE", None)
        question["criteria"] = labels
        result = engine.predict(body, {"operation": question})
        add_usage(usage, result.get("usage"))
        operation_answer = validate_choice(result["answers"].get("operation", {}), labels)
        operation = operation_answer["choice"]
    target, target_probs, target_confidence, rounds = None, {}, None, 0
    if operation in targets:
        candidates = targets[operation]
        answer, rounds = choose_target(engine, body, goal, operation, candidates, usage)
        target, target_probs, target_confidence = answer["choice"], answer["probabilities"], answer["confidence"]
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
        "target_rounds": rounds,
        "raw_answers": result.get("answers", {}),
        "model": result.get("model", engine.model),
        "usage": usage,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {"model": engine.model, "state": body, "questions": {"operation": question}},
    }
