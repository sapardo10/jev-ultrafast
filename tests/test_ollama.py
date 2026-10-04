"""Offline contracts for the staged Ollama System One backend. No network."""

import pytest

from jev_ultrafast import ollama_model
from jev_ultrafast.browser import fingerprint


def page(links=4):
    actions = [
        {"id": "e1", "kind": "fill", "label": "Search", "role": "searchbox", "value": "flutter", "node": 10},
        {"id": "e2", "kind": "click", "label": "Search", "role": "button", "value": "", "node": 20},
    ]
    for i in range(links):
        actions.append({"id": "b%d" % i, "kind": "click", "label": "Link %d" % i, "role": "link", "node": 30 + i})
    actions.append({"id": "scroll_down", "kind": "scroll", "label": "Scroll down"})
    state = {"url": "https://example.test/", "title": "T", "text": "T", "scroll": {"y": 0}, "actions": actions}
    state["fingerprint"] = fingerprint(state)
    return state


def answer(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


class Engine(ollama_model.OllamaEngine):
    """Real predict() shortcuts; a fake server picks via `pick(question_id, criteria)`."""

    def __init__(self, pick):
        super().__init__(base_url="http://fake", model="fake")
        self.pick = pick
        self.calls = []

    def predict(self, state, questions):
        self.calls.append(questions)
        answers = {qid: answer(q["criteria"], self.pick(qid, q["criteria"])) for qid, q in questions.items()}
        return {"model": "fake", "answers": answers, "usage": {"input_tokens": 3}}


def test_operation_is_asked_alone_then_only_its_targets():
    engine = Engine(lambda qid, criteria: "CLICK" if qid == "operation" else "2")
    decision = ollama_model.choose(page(), "Search for flutter", [], engine=engine)
    assert [set(call) for call in engine.calls] == [{"operation"}, {"click_target_1"}]
    assert decision["choice"] == "e2" and decision["operation"] == "CLICK" and decision["target"] == "2"
    assert decision["usage"] == {"input_tokens": 6} and decision["target_rounds"] == 1


def test_controls_need_no_target_call():
    engine = Engine(lambda qid, criteria: "SCROLL_DOWN")
    decision = ollama_model.choose(page(), "Scroll", [], engine=engine)
    assert len(engine.calls) == 1 and decision["choice"] == "scroll_down" and decision["target"] is None


def test_large_target_sets_run_a_final_round_among_chunk_leaders():
    # 60 links + Search box + button = 62 click targets -> 3 chunks, then a final question.
    def pick(qid, criteria):
        if qid == "operation":
            return "CLICK"
        return "40" if "40" in criteria else list(criteria)[-1]

    engine = Engine(pick)
    decision = ollama_model.choose(page(links=60), "Open link 37", [], engine=engine)
    first, final = engine.calls[1], engine.calls[2]
    assert len(first) == 3 and all(len(q["criteria"]) <= ollama_model.MAX_CANDIDATES for q in first.values())
    assert len(final) == 1 and "40" in next(iter(final.values()))["criteria"]
    assert decision["target"] == "40" and decision["choice"] == "b37" and decision["target_rounds"] == 2


def test_single_candidate_questions_never_reach_the_server(monkeypatch):
    def fail(*_args):
        raise AssertionError("no request expected")

    monkeypatch.setattr(ollama_model, "post_json", fail)
    result = ollama_model.OllamaEngine().predict({}, {"t": {"type": "choice", "criteria": {"1": "only"}}})
    assert result["answers"]["t"]["choice"] == "1"


def test_invalid_answer_executes_nothing():
    engine = Engine(lambda qid, criteria: "NOPE")
    with pytest.raises(ValueError, match="no action executed"):
        ollama_model.choose(page(), "Search", [], engine=engine)


def test_type_text_option_names_the_fields_still_empty():
    engine = Engine(lambda qid, criteria: "DONE")
    state = page()
    phone = {"id": "e9", "kind": "fill", "label": "Phone:", "role": "textbox", "value": "", "node": 11}
    state["actions"].insert(1, phone)
    ollama_model.choose(state, "Fill the form", [], engine=engine)
    assert engine.calls[0]["operation"]["criteria"]["TYPE_TEXT"].endswith(" Empty fields: Phone.")
    engine = Engine(lambda qid, criteria: "DONE")
    ollama_model.choose(page(), "Fill the form", [], engine=engine)
    assert engine.calls[0]["operation"]["criteria"]["TYPE_TEXT"].endswith(" Every field has a value.")


def test_first_step_blocked_is_re_asked_without_blocked_or_done():
    # Regression: a splash page ("Ingresar") made nimble answer BLOCKED before anything was tried.
    def pick(qid, criteria):
        return "BLOCKED" if "BLOCKED" in criteria else "CLICK" if qid == "operation" else "2"

    engine = Engine(pick)
    decision = ollama_model.choose(page(), "Open the panel", [], engine=engine)
    assert "BLOCKED" not in engine.calls[1]["operation"]["criteria"]
    assert decision["operation"] == "CLICK"


def test_blocked_after_actions_is_respected():
    engine = Engine(lambda qid, criteria: "BLOCKED")
    history = [{"action": "x", "kind": "click", "text": None, "page_changed": True}]
    assert ollama_model.choose(page(), "g", history, engine=engine)["choice"] == "BLOCKED"


def test_done_option_cites_the_click_that_changed_the_page():
    # Regression: nimble never chose DONE after opening a panel and clicked Back until BLOCKED.
    clicked = [{"action": "Bogotá invierte en su casa \n  x", "kind": "click", "page_changed": True}]
    seen = []

    def pick(qid, criteria):
        seen.append(criteria.get("DONE"))
        return "DONE" if qid == "operation" else "1"

    ollama_model.choose(page(), "Open it", clicked, engine=Engine(pick))
    assert "clicked 'Bogotá invierte en su casa'" in seen[0]
    assert ollama_model.done_label([], page()) == ollama_model.laya_model.DONE


def test_done_option_says_so_when_the_clicked_label_is_the_whole_goal():
    clicked = [{"action": "Censo inmobiliario \n x", "kind": "click", "page_changed": True}]
    assert "choose DONE" in ollama_model.done_label(clicked, page(), "Open the Censo inmobiliario panel.")
    long_goal = "Open Ver Datos, then open the Vivienda, Ciudad y Territorio category."
    clicked = [{"action": "Ver Datos \n x", "kind": "click", "page_changed": True}]
    assert "just to open" not in ollama_model.done_label(clicked, page(), long_goal)
