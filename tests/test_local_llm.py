"""Offline contracts for the local instruct-LLM decision backend. No model downloads."""

import pytest

from jev_ultrafast import local_llm, model
from jev_ultrafast.browser import fingerprint


def page(action_count=4):
    actions = [
        {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
        {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
        {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
    ]
    for i in range(action_count):
        actions.append(
            {"id": "b%d" % i, "kind": "click", "label": "Result %d" % i, "role": "link", "value": "", "node": 30 + i}
        )
    actions.append({"id": "wait", "kind": "wait", "label": "Wait for the page to update"})
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": actions,
    }
    state["fingerprint"] = fingerprint(state)
    return state


AUDIT = "MET: nothing. NEXT: fill the search field."


def chat_of(replies, audit=AUDIT):
    calls, decisions = [], []

    def chat(messages, max_new_tokens=160):
        calls.append(messages)
        if messages[0]["content"].startswith("You audit"):
            return audit
        decisions.append(messages)
        return replies[min(len(decisions) - 1, len(replies) - 1)]

    chat.calls = calls
    chat.decisions = decisions
    return chat


def test_valid_choice_maps_to_the_observed_action():
    chat = chat_of(['{"operation": "TYPE_TEXT", "target": "1"}'])
    decision = local_llm.choose(page(), "Find a book", [], chat_fn=chat)
    assert decision["choice"] == "e1" and decision["operation"] == "TYPE_TEXT" and decision["target"] == "1"
    assert decision["probabilities"] == {"e1": 1.0}
    prompt = chat.calls[0][-1]["content"]
    assert "[1] textbox Search" in prompt
    assert "TYPE_TEXT: 1" in prompt and "CLICK: 1, 2, 3" in prompt and "No target:" in prompt
    assert "Goal: Find a book" in prompt and "Recent actions: (none)" in prompt
    assert ("Progress audit:\n" + AUDIT) in chat.decisions[0][-1]["content"]


def test_echoed_select_label_still_resolves_to_the_offered_option():
    state = page()
    state["actions"].insert(
        4,
        {
            "id": "s1", "kind": "select", "label": "Stay category → Design", "value": "Design",
            "current_value": "All stays", "node": 50, "role": "combobox",
        },
    )
    state["fingerprint"] = fingerprint(state)
    _elements, targets, _controls = model.action_space(state["actions"])
    key = next(iter(targets["SELECT"]))
    chat = chat_of(['{"operation": "SELECT", "target": "%s Stay category → Design"}' % key])
    decision = local_llm.choose(state, "Pick the Design category", [], chat_fn=chat)
    assert decision["target"] == key and decision["choice"] == "s1"
    assert "SELECT: %s Design" % key in chat.calls[0][-1]["content"]


def test_extra_keys_in_the_reply_are_tolerated():
    chat = chat_of(['{"unmet": "results are loading", "operation": "WAIT", "target": null}'])
    decision = local_llm.choose(page(), "Wait for results", [], chat_fn=chat)
    assert decision["choice"] == "wait"


def test_controls_and_done_do_not_need_a_target():
    wait = local_llm.choose(page(), "Wait for results", [], chat_fn=chat_of(['{"operation": "WAIT", "target": null}']))
    assert wait["choice"] == "wait" and wait["target"] is None and wait["probabilities"] == {"wait": 1.0}
    done = local_llm.choose(page(), "Find a book", [], chat_fn=chat_of(['{"operation": "DONE", "target": null}']))
    assert done["choice"] == "DONE" and done["target"] is None and done["target_probabilities"] == {}


def test_invalid_reply_is_retried_once_with_the_reason():
    chat = chat_of(["not json at all", '{"operation": "CLICK", "target": "3"}'])
    decision = local_llm.choose(page(), "Open a result", [], chat_fn=chat)
    assert len(chat.decisions) == 2 and decision["choice"] == "b0"
    assert "Invalid choice" in chat.decisions[1][-1]["content"]


def test_two_invalid_replies_execute_nothing():
    chat = chat_of(['{"operation": "CLICK", "target": "999"}'])
    with pytest.raises(ValueError, match="no valid choice"):
        local_llm.choose(page(), "Open a result", [], chat_fn=chat)
    assert len(chat.decisions) == 2


def test_target_on_a_control_operation_is_invalid():
    chat = chat_of(['{"operation": "WAIT", "target": "1"}'])
    with pytest.raises(ValueError, match="no valid choice"):
        local_llm.choose(page(), "Wait", [], chat_fn=chat)


def test_unoffered_operation_is_invalid():
    chat = chat_of(['{"operation": "SUBMIT", "target": null}'])
    with pytest.raises(ValueError, match="no valid choice"):
        local_llm.choose(page(), "Submit", [], chat_fn=chat)


def test_dispatch_defaults_to_the_local_llm_without_a_typesafe_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("DECISION_BACKEND", raising=False)
    calls = []
    monkeypatch.setattr(local_llm, "choose", lambda state, goal, history: calls.append(goal) or {"choice": "DONE"})
    assert model.choose(page(), "Find a book", []) == {"choice": "DONE"}
    assert calls == ["Find a book"]


def test_dispatch_uses_laya_when_selected(monkeypatch):
    from jev_ultrafast import laya_model

    monkeypatch.setenv("DECISION_BACKEND", "laya")
    calls = []
    monkeypatch.setattr(laya_model, "choose", lambda state, goal, history: calls.append(goal) or {"choice": "DONE"})
    assert model.choose(page(), "Find a book", []) == {"choice": "DONE"}
    assert calls == ["Find a book"]


def test_remote_base_url_routes_chat_to_the_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8080/v1")
    calls = []

    def remote(messages, max_new_tokens):
        calls.append(messages)
        return '{"operation": "WAIT", "target": null}'

    monkeypatch.setattr(local_llm, "remote_chat", remote)
    messages = [{"role": "user", "content": "hello"}]
    assert local_llm.chat(messages) == '{"operation": "WAIT", "target": null}'
    assert calls == [messages]
