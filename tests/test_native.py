"""Offline contracts for native Android screens: parsing, freshness, input safety, prompts. No device."""

import pytest

from jev_ultrafast import native, ollama_model, questions
from jev_ultrafast.browser import StalePage
from jev_ultrafast.native import NativeError, NativeScreen, adb_text, build_page, extract_xml, parse_screen

PKG = "com.example.app"


def node(attrs, children=""):
    base = {"class": "android.view.View", "package": PKG, "bounds": "[0,0][1080,200]", "enabled": "true"}
    base.update(attrs)
    body = " ".join('%s="%s"' % item for item in base.items())
    return "<node %s>%s</node>" % (body, children)


def screen(*nodes):
    return '<?xml version="1.0"?><hierarchy rotation="0">%s</hierarchy>' % "".join(nodes)


XML = screen(
    node({"bounds": "[0,0][1080,2400]"}),  # window root: the display size
    node({"package": "com.android.systemui", "text": "12:00", "clickable": "true", "bounds": "[0,0][1080,80]"}),
    node({"class": "android.widget.TextView", "text": "Display", "bounds": "[0,100][1080,180]"}),
    node(
        {"clickable": "true", "bounds": "[0,200][1080,400]"},
        node({"class": "android.widget.TextView", "text": "Dark theme", "bounds": "[0,200][800,300]"})
        + node(
            {"class": "android.widget.Switch", "checkable": "true", "checked": "true", "bounds": "[900,250][1000,350]"}
        ),
    ),
    node({"clickable": "true", "content-desc": "", "text": "", "bounds": "[0,400][100,500]"}),  # unnamed icon
    node({"clickable": "true", "text": "Off", "enabled": "false", "bounds": "[0,500][1080,600]"}),
    node({"clickable": "true", "text": "Below", "bounds": "[0,2500][1080,2600]"}),  # off-screen
    node({"class": "android.widget.EditText", "password": "true", "text": "secret", "bounds": "[0,600][1080,700]"}),
    node({"class": "android.widget.EditText", "hint": "Search", "text": "", "bounds": "[0,700][1080,800]"}),
    node({"scrollable": "true", "bounds": "[0,200][1080,2300]"}),
    node({"class": "android.widget.FrameLayout", "bounds": "[0,2300][1080,2400]"}),
)


def test_skips_disabled_offscreen_unnamed_password_and_systemui():
    elements, scrollables, text, size = parse_screen(XML)
    assert [e["label"] for e in elements] == ["Dark theme", "Unnamed icon button (top left of the screen)", "Search"]
    assert size == (1080, 2400)
    assert text == ["Display"]  # the row's own label is not repeated as plain text
    assert len(scrollables) == 1


def test_row_without_text_takes_name_and_switch_state_from_descendants():
    elements, *_ = parse_screen(XML)
    row = elements[0]
    assert row["role"] == "button" and row["checked"] == "true" and row["label"] == "Dark theme"


def test_unnamed_compact_control_is_offered_by_position_never_coordinates():
    flutter = screen(node({"bounds": "[0,0][1080,2400]"}), node({"clickable": "true", "bounds": "[0,100][120,220]"}))
    (element,) = parse_screen(flutter)[0]
    assert element["label"] == "Unnamed icon button (top left of the screen)"


def test_unnamed_screen_sized_container_is_not_offered():
    root = node({"bounds": "[0,0][1080,2400]"})
    assert parse_screen(screen(root, node({"clickable": "true", "bounds": "[0,0][1080,2000]"})))[0] == []


def test_page_has_browser_shape_and_control_actions():
    page = build_page(XML, PKG + "/.Main")
    assert page["url"] == PKG + "/.Main" and page["native"] and page["title"] == "Main"
    ids = [a["id"] for a in page["actions"]]
    assert ids[:2] == ["e1", "e2"] and {"scroll_down", "scroll_up", "back", "wait"} <= set(ids)
    assert page["guards"].keys() == {"1", "2", "3"} and page["page_key"] and page["marker"]
    assert next(a for a in page["actions"] if a["id"] == "e3")["kind"] == "fill"


def test_marker_ignores_geometry_but_guard_tracks_it_and_state():
    moved = XML.replace("[0,200][1080,400]", "[0,210][1080,410]")
    a, b = build_page(XML, PKG + "/.Main"), build_page(moved, PKG + "/.Main")
    assert a["marker"] == b["marker"] and a["guards"]["1"] != b["guards"]["1"]
    toggled = XML.replace('checked="true"', 'checked="false"')
    assert build_page(toggled, PKG + "/.Main")["guards"]["1"] != a["guards"]["1"]


def test_extract_xml_strips_status_line_and_rejects_garbage():
    dumped = "<?xml version='1.0'?><hierarchy></hierarchy>\nUI hierchary dumped to: /dev/tty"
    assert extract_xml(dumped).endswith("</hierarchy>")
    with pytest.raises(NativeError, match="ERROR: could not get idle state"):
        extract_xml("ERROR: could not get idle state")


@pytest.mark.parametrize("value, expected", [("a b", "a%sb"), ("50%", "50%%"), ("x&y", "x\\&y")])
def test_adb_text_escapes_shell_metacharacters(value, expected):
    assert adb_text(value) == expected


@pytest.mark.parametrize("value", ["café", "日本", "tab\there"])
def test_adb_text_refuses_non_ascii_before_any_input(value):
    with pytest.raises(NativeError, match="ASCII"):
        adb_text(value)


class FakeScreen(NativeScreen):
    def __init__(self, pages):
        self.serial, self.package, self.after_input, self.mobile = "emu", PKG, None, True
        self.sleep, self.pages, self.sent = lambda _s: None, list(pages), []
        self.scroll_probe, self.exhausted = None, set()
        self.trail, self.undone = [], set()

    def read(self):
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]

    def shell(self, *args):
        self.sent.append(args)


def fill_action(page):
    return next(a for a in page["actions"] if a["kind"] == "fill")


def pages(xml=XML):
    return build_page(xml, PKG + "/.Main")


def test_tap_uses_bounds_centre_after_fresh_check():
    page = pages()
    device = FakeScreen([page])
    device.act(page["actions"][0], page)
    assert device.sent == [("input", "tap", "540", "300")]


def test_stale_screen_blocks_input():
    page = pages()
    device = FakeScreen([pages(XML.replace("Dark theme", "Light theme"))])
    with pytest.raises(StalePage):
        device.act(page["actions"][0], page)
    assert device.sent == []


def test_unicode_text_without_the_ime_is_refused_without_touching_the_device():
    page = pages()
    device = FakeScreen([page])
    device.ime_available = lambda: False
    with pytest.raises(NativeError, match="ADBKeyboard"):
        device.act(fill_action(page), page, text="Bogot\u00e1")
    assert device.sent == []


def test_unicode_text_goes_through_the_ime_and_restores_the_keyboard(monkeypatch):
    page = pages()
    device = FakeScreen([page])
    device.ime_available = lambda: True
    monkeypatch.setattr(native, "adb", lambda *a, **k: "com.google.android.inputmethod.latin/.LatinIME")
    device.act(fill_action(page), page, text="Bogot\u00e1")
    commands = [" ".join(c) for c in device.sent]
    broadcast = next(c for c in commands if "ADB_INPUT_B64" in c)
    assert broadcast.endswith("Qm9nb3TDoQ==")  # UTF-8 base64 of "Bogot\u00e1"
    assert commands[-1] == "ime set com.google.android.inputmethod.latin/.LatinIME"
    assert not any(c.startswith("input text") for c in commands)


def test_fill_taps_clears_then_types():
    page = pages()
    device = FakeScreen([page])
    device.keyboard_shown = lambda: False
    device.act(fill_action(page), page, text="dark mode")
    assert [c[1] for c in device.sent] == ["tap", "keyevent", "text"] and device.sent[-1][-1] == "dark%smode"


def test_keyboard_is_dismissed_after_typing_only_when_it_is_showing():
    page = pages()
    device = FakeScreen([page])
    device.keyboard_shown = lambda: True
    device.act(fill_action(page), page, text="Jose")
    assert device.sent[-1] == ("input", "keyevent", "4")
    hidden = FakeScreen([page])
    hidden.keyboard_shown = lambda: False
    hidden.act(fill_action(page), page, text="Jose")
    assert hidden.sent[-1][1] == "text"


def test_back_and_scroll_are_single_input_calls():
    page = pages()
    device = FakeScreen([page])
    back = next(a for a in page["actions"] if a["kind"] == "back")
    device.act(back, page)
    down = next(a for a in page["actions"] if a["id"] == "scroll_down")
    device.act(down, page)
    assert device.sent[0] == ("input", "keyevent", "4")
    swipe = device.sent[1]
    assert swipe[:2] == ("input", "swipe") and int(swipe[3]) > int(swipe[5])  # finger moves up to scroll down


def test_observe_waits_for_two_identical_reads_and_defers_to_logged_execution():
    first, settled = pages(XML.replace("Dark theme", "Dark")), pages()
    device = FakeScreen([first, settled, settled])
    device.after_input = {"id": "e1"}
    page = device.observe(screenshot=False)
    assert page["marker"] == settled["marker"] and "fingerprint" in page and device.after_input is None


def test_back_is_a_decision_operation_and_screens_are_described_not_urled():
    page = build_page(XML, PKG + "/.Main")
    page["fingerprint"] = "x"

    class Engine(ollama_model.OllamaEngine):
        def __init__(self):
            super().__init__(base_url="http://fake", model="fake")
            self.calls = []

        def predict(self, state, questions_):
            self.calls.append((state, questions_))
            return {
                "answers": {
                    q: {
                        "choice": "BACK",
                        "confidence": 1.0,
                        "probabilities": {k: float(k == "BACK") for k in v["criteria"]},
                    }
                    for q, v in questions_.items()
                }
            }

    engine = Engine()
    decision = ollama_model.choose(page, "go back", [], engine=engine)
    state, asked = engine.calls[0]
    assert decision["choice"] == "back" and "BACK" in asked["operation"]["criteria"]
    assert state["screen"] == {"app": PKG, "screen": ".Main"} and "page" not in state
    assert "native app screen" in asked["operation"]["instructions"]


def test_web_prompt_is_unchanged():
    assert questions.next_action({"url": "https://x"}) == questions.NEXT_ACTION


class Foreign(NativeScreen):
    def __init__(self, activity, alive):
        self.serial, self.package, self.after_input, self.mobile = "emu", PKG, None, True
        self.sleep, self.front, self.is_alive = lambda _s: None, activity, alive
        self.scroll_probe, self.exhausted = None, set()
        self.trail, self.undone = [], set()

    def foreground(self):
        return self.front

    def alive(self):
        return self.is_alive

    def dump(self):
        return XML


def test_system_dialog_over_a_live_app_is_observed_not_an_error():
    dialog = "com.google.android.permissioncontroller/.GrantPermissionsActivity"
    page = Foreign(dialog, True).read()
    assert page["url"] == dialog and page["actions"]


@pytest.mark.parametrize(
    "front, alive", [("", True), ("com.google.android.apps.nexuslauncher/.Launcher", True), ("com.other/.A", False)]
)
def test_left_or_dead_app_is_an_error(front, alive):
    with pytest.raises(NativeError, match="not in the foreground"):
        Foreign(front, alive).read()


def test_multiline_flutter_semantics_label_is_one_line():
    tab = node({"clickable": "true", "content-desc": "Settings\nTab 2 of 2"})
    xml = screen(node({"bounds": "[0,0][1080,2400]"}), tab)
    assert parse_screen(xml)[0][0]["label"] == "Settings Tab 2 of 2"


def test_done_is_not_nudged_by_an_unrelated_native_tap():
    history = [{"kind": "click", "page_changed": True, "action": "Allow"}]
    plain = ollama_model.laya_model.DONE
    assert ollama_model.done_label(history, {"native": True}, "Tap Next twice") == plain
    related = ollama_model.done_label(
        [{"kind": "click", "page_changed": True, "action": "Dark theme"}], {"native": True}, "Turn on dark theme"
    )
    assert "choose DONE" in related
    # Web behaviour is unchanged.
    assert "choose DONE" in ollama_model.done_label(history, {}, "Tap Next twice")


def test_text_helper_asks_again_once_after_a_malformed_answer():
    from jev_ultrafast import local_text

    answers = iter(["not json at all", '{"text": "London"}'])
    value, _ = local_text.generate({"goal": "g", "field": {"label": "Where to?"}}, writer=lambda _m: next(answers))
    assert value == "London"
    with pytest.raises(ValueError, match="nothing typed"):
        local_text.generate({"goal": "g", "field": {"label": "x"}}, writer=lambda _m: "garbage")


def test_done_is_not_nudged_while_a_confirm_control_is_still_on_screen():
    history = [{"kind": "click", "page_changed": True, "action": "Friday, November 20, 2026"}]
    goal = "Find flights on November 20, 2026"
    long_label = "Done. Search for one-way flights, departing on November 20, 2026"
    picker = {"actions": [{"kind": "click", "label": long_label}, {"kind": "click", "label": "Reset"}]}
    assert "choose DONE" not in ollama_model.done_label(history, picker, goal)
    assert "choose DONE" in ollama_model.done_label(history, {"actions": [{"kind": "click", "label": "Flights"}]}, goal)


def test_done_option_is_withheld_while_a_picker_is_still_open():
    class Engine(ollama_model.OllamaEngine):
        def __init__(self):
            super().__init__(base_url="http://fake", model="fake")
            self.operations = []

        def predict(self, state, questions_):
            question = questions_.get("operation")
            if question:
                self.operations.append(set(question["criteria"]))
            keys = next(iter(questions_.values()))["criteria"]
            first = next(iter(keys))
            answers = {
                q: {
                    "choice": first,
                    "confidence": 1.0,
                    "probabilities": {k: float(k == first) for k in v["criteria"]},
                }
                for q, v in questions_.items()
            }
            return {"answers": answers}

    page = {
        "url": "u", "title": "t", "text": "x", "fingerprint": "f",
        "actions": [
            {"id": "e1", "kind": "click", "label": "Done", "role": "button", "node": 1},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    history = [{"kind": "click", "page_changed": True, "action": "Friday, November 20, 2026"}]
    engine = Engine()
    ollama_model.choose(page, "Find flights on November 20, 2026", history, engine=engine)
    assert "DONE" not in engine.operations[0] and "BLOCKED" in engine.operations[0]
    engine = Engine()
    ollama_model.choose(page, "Find flights on November 20, 2026", [], engine=engine)
    assert "DONE" in engine.operations[0]


def test_back_and_close_are_withheld_right_after_the_goals_own_control_was_opened():
    targets = {
        "CLICK": {
            "1": {"label": "Volver \n Bogotá invierte en su casa", "id": "e1"},
            "2": {"label": "Censo inmobiliario", "id": "e2"},
        }
    }
    goal = "Open the Bogotá invierte en su casa panel."
    opened = [{"kind": "click", "page_changed": True, "action": "Bogotá invierte en su casa \n x"}]
    assert list(ollama_model.drop_undo_targets(targets, opened, goal)["CLICK"]) == ["2"]
    assert ollama_model.drop_undo_targets(targets, [], goal) == targets  # nothing opened yet
    late = [{"kind": "click", "page_changed": False, "action": "Bogotá invierte en su casa \n x"}]
    assert list(ollama_model.drop_undo_targets(targets, late, goal)["CLICK"]) == ["2"]
    unrelated = [{"kind": "click", "page_changed": True, "action": "Menu"}]
    assert ollama_model.drop_undo_targets(targets, unrelated, goal) == targets


def test_scroll_is_withdrawn_once_a_swipe_changed_nothing():
    page = pages()
    device = FakeScreen([page])
    down = next(a for a in page["actions"] if a["id"] == "scroll_down")
    device.act(down, page)
    unchanged = device.observe(screenshot=False)  # same screen after the swipe: the end of the list
    ids = [a["id"] for a in unchanged["actions"]]
    assert "scroll_down" not in ids and "scroll_up" in ids
    assert unchanged["marker"] == page["marker"]  # freshness still compares the unfiltered screen
    moved = FakeScreen([pages(XML.replace("Dark theme", "Light theme"))])
    moved.exhausted = device.exhausted
    assert "scroll_down" in [a["id"] for a in moved.observe(screenshot=False)["actions"]]


def test_a_click_that_back_reversed_is_not_offered_again_on_that_screen():
    page = pages()
    device = FakeScreen([page])
    row = next(a for a in page["actions"] if a["label"] == "Dark theme")
    device.act(row, page)
    other = pages(XML.replace("Dark theme", "Sub screen"))
    device.pages = [other]
    device.act(next(a for a in other["actions"] if a["kind"] == "back"), other)
    device.pages = [page]
    back_home = device.observe(screenshot=False)
    labels = [a["label"] for a in back_home["actions"] if a["kind"] == "click"]
    assert "Dark theme" not in labels and any(name.startswith("Unnamed icon") for name in labels)
    # A toggle clicked twice without BACK in between stays available.
    page = pages()
    again = FakeScreen([page])
    again.act(row, page)
    again.act(row, page)
    assert any(a["label"] == "Dark theme" for a in again.observe(screenshot=False)["actions"])


def test_back_that_exits_the_app_relaunches_it_instead_of_failing():
    device = Foreign("com.google.android.apps.nexuslauncher/.NexusLauncherActivity", True)
    device.trail = [("back", "k", "")]
    launched = []

    def relaunch(target=""):
        launched.append(target)
        device.front = PKG + "/.Main"

    device.launch = relaunch
    assert device.read()["url"] == PKG + "/.Main" and launched == [""]
    device.front, device.trail = "com.google.android.apps.nexuslauncher/.A", [("click", "k", "x")]
    with pytest.raises(NativeError, match="not in the foreground"):
        device.read()
    other = Foreign("com.other.app/.Main", True)  # BACK fell through to the previously used app
    other.trail, other.launch = [("back", "k", "")], lambda target="": setattr(other, "front", PKG + "/.Main")
    assert other.read()["url"] == PKG + "/.Main"


def test_back_is_withdrawn_when_it_changed_nothing():
    page = pages()
    device = FakeScreen([page])
    device.act(next(a for a in page["actions"] if a["kind"] == "back"), page)
    assert "back" not in [a["id"] for a in device.observe(screenshot=False)["actions"]]



def test_native_state_reports_opened_screens_and_typed_values_not_an_action_log():
    from jev_ultrafast import laya_model

    page = build_page(XML, PKG + "/.Main")
    history = [
        {"kind": "click", "page_changed": True, "action": "Display & touch — Dark theme, font size", "text": None},
        {"kind": "fill", "page_changed": True, "action": "Search", "text": "dark"},
        {"kind": "scroll", "page_changed": True, "action": "Scroll down", "text": None},
    ]
    body = laya_model.page_state(page, "g", history, [])
    assert "recent_actions" not in body
    assert body["opened_screens"] == ["Display & touch"] and body["typed"] == [{"field": "Search", "text": "dark"}]
    web = laya_model.page_state({"url": "u", "title": "t", "text": "x"}, "g", history, [])
    assert "recent_actions" in web and "opened_screens" not in web


def test_native_history_key_comes_after_the_page_text():
    from jev_ultrafast import laya_model

    page = build_page(XML, PKG + "/.Main")
    body = laya_model.page_state(page, "g", [{"kind": "click", "page_changed": True, "action": "Display"}], [])
    keys = list(body)
    assert keys.index("opened_screens") > keys.index("page_text")


def test_blocked_is_asked_again_while_a_native_screen_can_still_scroll():
    class Engine(ollama_model.OllamaEngine):
        def __init__(self):
            super().__init__(base_url="http://fake", model="fake")
            self.operations = []

        def predict(self, state, questions_):
            answers = {}
            for qid, q in questions_.items():
                keys = list(q["criteria"])
                first = "BLOCKED" if qid == "operation" and "BLOCKED" in keys else keys[0]
                if qid == "operation":
                    self.operations.append(set(keys))
                probabilities = {k: float(k == first) for k in keys}
                answers[qid] = {"choice": first, "confidence": 1.0, "probabilities": probabilities}
            return {"answers": answers}

    page = build_page(XML, PKG + "/.Main")
    page["fingerprint"] = "x"
    history = [{"kind": "fill", "page_changed": True, "action": "First name", "text": "Jose"}]
    engine = Engine()
    decision = ollama_model.choose(page, "Create a contact", history, engine=engine)
    assert len(engine.operations) == 2 and "BLOCKED" not in engine.operations[1]
    assert decision["operation"] != "BLOCKED"
    page["actions"] = [a for a in page["actions"] if a["kind"] != "scroll"]  # end of the list: BLOCKED may stand
    engine = Engine()
    assert ollama_model.choose(page, "Create a contact", history, engine=engine)["operation"] == "BLOCKED"


def test_a_transient_empty_foreground_is_waited_out():
    device = Foreign("", True)
    fronts = iter(["", "", PKG + "/.Main"])
    device.foreground = lambda: next(fronts)
    assert device.read()["url"] == PKG + "/.Main"


def test_material_text_field_is_named_by_its_overlapping_label_not_its_contents():
    field = node({"class": "android.widget.EditText", "text": "Jose", "bounds": "[100,300][900,450]"})
    label = node({"class": "android.widget.TextView", "text": "First name", "bounds": "[130,300][400,340]"})
    xml = screen(node({"bounds": "[0,0][1080,2400]"}), field, label)
    (element,) = parse_screen(xml)[0]
    assert element["label"] == "First name" and element["value"] == "Jose"
    prefix = node({"class": "android.widget.EditText", "text": "+1", "bounds": "[100,500][900,650]"})
    phone = node({"class": "android.widget.TextView", "text": "Phone", "bounds": "[130,500][260,540]"})
    xml = screen(node({"bounds": "[0,0][1080,2400]"}), prefix, phone)
    assert parse_screen(xml)[0][0]["label"] == "Phone"


def test_edittext_covered_by_a_spinner_is_a_dropdown_not_a_text_field():
    box = "[126,908][954,1076]"
    field = node({"class": "android.widget.EditText", "text": "Mobile", "bounds": box})
    spinner = node({"class": "android.widget.Spinner", "bounds": box})
    plain = node({"class": "android.widget.EditText", "text": "", "hint": "Notes", "bounds": "[126,1916][954,2147]"})
    elements = parse_screen(screen(node({"bounds": "[0,0][1080,2400]"}), field, spinner, plain))[0]
    kinds = {e["label"]: (e["kind"], e["role"]) for e in elements}
    assert kinds["Mobile"] == ("click", "combobox") and kinds["Notes"] == ("fill", "textbox")


def test_field_label_skips_symbols_and_prefixes_inside_the_field():
    field = node({"class": "android.widget.EditText", "text": "+1", "bounds": "[100,500][900,650]"})
    flag = node({"class": "android.widget.TextView", "text": "\U0001f1fa\U0001f1f8", "bounds": "[110,520][160,560]"})
    label = node({"class": "android.widget.TextView", "text": "Phone", "bounds": "[170,500][300,540]"})
    xml = screen(node({"bounds": "[0,0][1080,2400]"}), field, flag, label)
    assert parse_screen(xml)[0][0]["label"] == "Phone"
