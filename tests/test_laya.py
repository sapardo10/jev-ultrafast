"""Offline contracts for the local Laya decision backend. No model downloads."""

import pytest

from jev_ultrafast import laya_model, local_text, model
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


def answer(ids, selected):
    return {
        "type": "choice",
        "choice": selected,
        "confidence": 1.0,
        "probabilities": {i: float(i == selected) for i in ids},
        "action": {"act_probability": 1.0},
    }


class Engine:
    def __init__(self, pick):
        self.pick = pick
        self.calls = []

    def predict(self, state, questions):
        self.calls.append((state, questions))
        return {"model": "fake-laya", "answers": self.pick(state, questions), "usage": {"input_tokens": 7}}


def plain(operation, targets=None):
    def pick(_state, questions):
        answers = {"operation": answer(questions["operation"]["criteria"], operation)}
        for key, question in questions.items():
            if key.endswith("_target"):
                answers[key] = answer(question["criteria"], (targets or {}).get(key, next(iter(question["criteria"]))))
        return answers

    return pick


def test_operation_and_every_target_head_are_one_predict_call():
    engine = Engine(plain("TYPE_TEXT", {"type_text_target": "1"}))
    decision = laya_model.choose(page(), "Find a book", [], engine=engine)
    assert len(engine.calls) == 1
    state, questions = engine.calls[0]
    assert set(questions) == {"operation", "type_text_target", "click_target"}
    assert decision["choice"] == "e1" and decision["operation"] == "TYPE_TEXT" and decision["target"] == "1"
    assert decision["probabilities"] == {"e1": 1.0}
    assert decision["target_probabilities"]["1"] == 1.0
    assert decision["model"] == "fake-laya" and decision["usage"] == {"input_tokens": 7}
    assert state["goal"] == "Find a book" and state["elements"][0]["index"] == "1"


def test_short_option_labels_keep_the_index_and_state_carries_the_details():
    engine = Engine(plain("CLICK", {"click_target": "2"}))
    decision = laya_model.choose(page(), "Open the first result", [], engine=engine)
    _state, questions = engine.calls[0]
    assert questions["click_target"]["criteria"]["2"] == "Go"
    assert decision["choice"] == "e3" and decision["probabilities"]["e3"] == 1.0


def test_control_operations_execute_without_a_target_head():
    engine = Engine(plain("WAIT"))
    decision = laya_model.choose(page(), "Wait for results", [], engine=engine)
    assert decision["choice"] == "wait" and decision["target"] is None
    assert decision["operation"] == "WAIT" and decision["probabilities"] == {"wait": 1.0}


def test_done_and_blocked_stop_the_loop_as_operations():
    for name in ("DONE", "BLOCKED"):
        decision = laya_model.choose(page(), "Find a book", [], engine=Engine(plain(name)))
        assert decision["choice"] == name and decision["target"] is None


def test_invented_operation_choice_is_rejected_before_any_action():
    engine = Engine(plain("INVENTED"))
    with pytest.raises(ValueError, match="Invalid model response"):
        laya_model.choose(page(), "Find a book", [], engine=engine)


def test_invented_target_choice_is_rejected_before_any_action():
    engine = Engine(plain("CLICK", {"click_target": "999"}))
    with pytest.raises(ValueError, match="Invalid model response"):
        laya_model.choose(page(), "Find a book", [], engine=engine)


def test_many_candidates_split_into_grouped_heads_and_product_probabilities(monkeypatch):
    monkeypatch.setenv("LAYA_CHUNK_SIZE", "2")

    def pick(_state, questions):
        answers = {"operation": answer(questions["operation"]["criteria"], "CLICK")}
        answers["click_target_group"] = answer(questions["click_target_group"]["criteria"], "2")
        for i in (1, 2, 3, 4):
            key = "click_target_%d" % i
            answers[key] = answer(questions[key]["criteria"], next(iter(questions[key]["criteria"])))
        return answers

    engine = Engine(pick)
    p = page(action_count=5)
    decision = laya_model.choose(p, "Open a result", [], engine=engine)
    _state, questions = engine.calls[0]
    assert set(questions) == {
        "operation", "type_text_target", "click_target_group", "click_target_1", "click_target_2", "click_target_3",
        "click_target_4",
    }
    assert len(questions["click_target_2"]["criteria"]) == 2
    assert decision["target"] == "3"
    assert decision["choice"] == "b0"
    assert abs(sum(decision["target_probabilities"].values()) - 1) < 1e-9
    assert set(decision["probabilities"]) == {a["id"] for a in p["actions"] if a["kind"] == "click"}


def test_select_options_keep_their_observed_element_option_index(monkeypatch):
    monkeypatch.setenv("LAYA_CHUNK_SIZE", "24")
    state = page()
    state["actions"].insert(
        0,
        {
            "id": "e9", "kind": "select", "label": "Destination → Lisbon", "value": "lisbon",
            "current_value": "Zurich", "node": 40, "role": "combobox",
        },
    )
    state["fingerprint"] = fingerprint(state)

    def pick(_state, questions):
        return {
            "operation": answer(questions["operation"]["criteria"], "SELECT"),
            "select_target": answer(questions["select_target"]["criteria"], "1:1"),
        }

    decision = laya_model.choose(state, "Fly to Lisbon", [], engine=Engine(pick))
    assert decision["target"] == "1:1" and decision["choice"] == "e9"
    assert decision["target_probabilities"]["1:1"] == 1.0


def test_remote_text_credentials_keep_the_openai_compatible_helper(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *_args: {"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    assert model.field_text({"goal": "Fly to Zurich"})[0] == "Zurich"


def test_local_text_helper_writes_values_without_a_key(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setattr(local_text, "generate", lambda context: ("Zurich", {"model": "local", "latency_ms": 1}))
    assert model.field_text({"goal": "Fly to Zurich"})[0] == "Zurich"


def test_local_text_parses_exactly_one_text_key():
    value, helper = local_text.generate({"goal": "Fly"}, writer=lambda _messages: '{"text": "Zurich"}')
    assert value == "Zurich" and helper["model"] == local_text.DEFAULT_MODEL


@pytest.mark.parametrize(
    "content", ['{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}', "Thinking: Zurich", "{}"]
)
def test_local_text_rejects_invalid_values(content):
    with pytest.raises(ValueError, match="nothing typed"):
        local_text.generate({"goal": "Fly"}, writer=lambda _messages: content)
