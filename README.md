<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
uv run jev
```

No API key is required. By default a local instruct model (`Qwen/Qwen3-1.7B` through transformers, or any OpenAI-compatible local server) answers the operation/target questions and writes `TYPE_TEXT` values. `DECISION_BACKEND=laya` selects the non-autoregressive Laya engine; `DECISION_BACKEND=typesafe` (or a `TYPESAFE_API_KEY`) selects TypeSafe's Jev. The first run downloads the selected checkpoint.

Laya is the fastest option (~107 ms per decision, no text generation) but its own benchmarks describe the base checkpoints as near chance on out-of-domain typed decisions, and browser control is not one of its training workflows — measured here, its operation probabilities on real pages are near uniform. Treat Laya as a base to fine-tune (their Kaggle notebook), not a zero-shot browser policy. The local instruct model is the working default; decision quality is limited by the small model size, and the backend swap stays confined to one module in each case.

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

### Use from any agent (MCP)

`jev` ships an MCP server, `jev_ultrafast.mcp_server`, exposing one tool:

- `browser_task(url, goal, max_steps=25, screenshot=False, return_text=False)` — runs the browser agent to completion and returns a JSON trace (status, final URL, actions). `screenshot=True` also saves a JPEG and returns its path; `return_text=True` adds the final page's visible text (`page_text`) so a caller can read a price or status. `done` means the agent stopped acting, not that the answer is right; verify with `page_text` or the screenshot.

Register it once per agent, globally:

```bash
claude mcp add --scope user jev -- uv --directory ~/jev-ultrafast run python -m jev_ultrafast.mcp_server
```

```toml
# ~/.codex/config.toml
[mcp_servers.jev]
command = "uv"
args = ["--directory", "~/jev-ultrafast", "run", "python", "-m", "jev_ultrafast.mcp_server"]
```

OpenCode takes the same entry under `mcp` in `~/.config/opencode/opencode.json` (`"type": "local"`, `"command": [...]`). gstack runs inside these host sessions, so it inherits the tool.

The server starts its own headless Chrome (CDP port 9333, profile `~/.cache/jev/chrome`, `JEV_HEADED=1` to show it) and its own Browser Harness daemon, so nothing needs to be running first. If `LOCAL_LLM_BASE_URL` is unreachable it falls back to the in-process model.

### Local model configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECISION_BACKEND` | `local-llm` | `local-llm`, `laya`, `typesafe` (auto-`typesafe` when `TYPESAFE_API_KEY` is set) |
| `LOCAL_LLM_MODEL` | `Qwen/Qwen3-1.7B` | In-process instruct model for decisions and `TYPE_TEXT` |
| `LOCAL_LLM_BASE_URL` | unset | OpenAI-compatible chat endpoint instead of in-process, e.g. `http://127.0.0.1:8080/v1` |
| `LOCAL_LLM_API_KEY` | unset | Bearer token for that endpoint, if it needs one |
| `LAYA_MODEL` | `convaiinnovations/laya` | Laya checkpoint for decisions |
| `LAYA_SUBFOLDER` | `typed-decisions` | `typed-decisions` (fine-tuned), `root` (English base), or `multilingual` |
| `LAYA_DEVICE` | auto | `mps`, `cuda`, or `cpu` |
| `LAYA_MAX_LEN` | `2048` | State context budget in tokens |
| `LAYA_HEAD_MAX_LEN` | `512` | Option budget shared by each question's choices |
| `LAYA_CHUNK_SIZE` | `24` | Target candidates per question; larger pages are split into grouped heads |

To serve the model with MLX instead of transformers:

```bash
mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8080
# .env: LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
```

The local backend audits the page against the goal first, then chooses one operation and target; invalid choices are retried once with the reason, then refused. In-process Qwen3-1.7B takes roughly 1–2.5 s per action on an M-series GPU; a served MLX model or a larger checkpoint improves both latency and decision quality. The text helper uses the OpenAI-compatible endpoint when `TEXT_MODEL_API_KEY` is set and the local model otherwise; a `{"text": null}` response stops the step instead of guessing a value.

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Mobile: Android Chrome and iOS Safari

Same loop, same action space, same `browser_task` trace. Desktop is unchanged; set one env var to switch.

**Android (Chrome over CDP).** Needs `adb` and a booted emulator or device with Chrome and USB debugging.

```bash
export JEV_ANDROID_SERIAL=emulator-5554   # `adb devices`; turns Android mode on
export JEV_CDP_PORT=9444                  # local forward port (default 9444, never the desktop 9333)
# optional: JEV_ADB (adb path), JEV_ANDROID_BOOT_TIMEOUT (default 120 s), JEV_ANDROID_CHROME (package)
```

jev checks the device, waits for boot, forwards `localabstract:chrome_devtools_remote`, starts Chrome with first-run
screens disabled if it is not running, and uses its own harness daemon (`jev-android-<port>`). On a phone it keeps the
real viewport (no 1120x780 override), opens a foreground tab (a phone does not paint background tabs), taps with touch
events, scrolls with touch gestures, scrolls targets into the visual viewport (the keyboard shrinks it), waits longer
for the page to settle, and takes screenshots in CSS pixels of the visible viewport (a 2.6x phone does not return a 2.6x image).

**iOS (Safari on a Simulator, through Appium).** Safari on iOS does not speak CDP, so jev uses W3C WebDriver via
Appium XCUITest. `snapshot.js` and the action space are unchanged; only the transport differs (`jev_ultrafast/webdriver.py`).

```bash
npm i appium && npx appium driver install xcuitest      # one-time, free
npx appium --port 4733 &                                # keep running
xcrun simctl create jev-ios com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro com.apple.CoreSimulator.SimRuntime.iOS-26-5
xcrun simctl boot <UDID>
export JEV_IOS_UDID=<UDID> JEV_WEBDRIVER_URL=http://127.0.0.1:4733
# optional: JEV_WEBDRIVER_CAPS='{"appium:platformVersion":"26.5"}'
```

The first call builds WebDriverAgent on the simulator (about 1-4 minutes); the Safari session id is cached in
`~/.cache/jev/ios-<udid>.session` and reused. `JEV_TASK_TIMEOUT` (default 240 s) bounds a whole task on every platform.

Failures return JSON with `status: "error"` (or `"timeout"`) and a `hint` that says what died: adb missing, device
offline/unauthorized/absent, emulator still booting, dead port forward, Chrome closed or crashed mid-task,
Appium down, simulator not booted, WebDriver session gone, a page that never settles.

**Native Android apps (spike).** No browser at all: set `JEV_ANDROID_APP=<package>` next to `JEV_ANDROID_SERIAL` and
jev reads the screen with `adb exec-out uiautomator dump`, builds the same page dict (`url` = `package/activity`,
actions `e1..eN`, markers, guards), and acts with `adb shell input` (tap the bounds centre, swipe inside the
largest scrollable, `input text`, and a new `BACK` operation). Disabled, off-screen and password nodes are not offered, and a compact unnamed
icon button is offered by position ("top left of the screen"), never by coordinates; a Settings row takes its name and switch state from its children. Typing is ASCII only (`input text`);
anything else is refused before touching the device. `browser_task`'s `url` is optional: pass an activity
(`.MainActivity`) or leave it empty for the launcher activity. Use a dedicated adb server
(`ANDROID_ADB_SERVER_PORT=5038`) and `-port` for a test emulator: other Android Studio/Gradle runs attach to all devices.
See [docs/native-apps.md](docs/native-apps.md) for results and limits.

Flights on Android Chrome (checked 2026-10-04): the date picker is a full-screen sheet whose button reads
"Done. Search for one-way flights, departing on ...". jev no longer offers DONE right after a page-changing click while a
Done/Apply/Confirm/OK control is visible, so the run now closes the picker. The goal date must be in the future (the
September 20 example is past on that date; the picker starts at the current month). The run is still unreliable after the
picker: the destination field was cleared in two of three runs and nimble repeated WAIT until the step cap.

Limits: elements that expose no
role and no name (an icon-only hamburger) are not offered to the model, so some mobile layouts cannot be driven. On iOS,
scroll is a JS scroll, screenshots are full device resolution, and typing uses WebDriver keys. Other Android emulators or
test runners attached to the same adb server can steal the foreground from Chrome; use a dedicated adb server or device.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
