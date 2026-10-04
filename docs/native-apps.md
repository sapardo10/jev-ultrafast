# Native apps

**Android is implemented as a spike** (`jev_ultrafast/native.py`, `JEV_ANDROID_APP=<package>`); iOS is still only this
design. The DOM snapshot has no DOM in a native app, so the loop stays
`page -> indexed elements -> operation + target -> execution`; only the page source changes.

## Android spike results (emulator, Ollama nimble, 2026-10-04)

| Task | Run | Status | Steps | Seconds | Verified |
|---|---|---|---|---|---|
| Settings: "Turn on dark theme" | 1 | done | 2 | 66 | `cmd uimode night` = yes, screenshot shows switch on |
| Settings: "Turn off dark theme" | 2 | done | 1 | 19 | `cmd uimode night` = no |
| Flutter debug app (pausactive): "Tap Next twice" (app already past welcome) | 1 | done, **goal not met** | 1 | 24 | Model chose DONE after a permission "Allow"; screenshot shows Dashboard. A false DONE. |
| Same app, fresh data: "Next until Dashboard shown" | 2 | done | 5 | 69 | screenshot: Dashboard (granted the notification permission on the test AVD) |

Fixed on the way: a system permission dialog over a live app was reported as "app not in foreground"; screen size was
taken from the largest bounds (off-screen rows counted); multi-line Flutter labels ("Settings\nTab 2 of 2").

Not reproduced: the unlabeled-hamburger problem. Every clickable node in this Flutter app carried a semantics label
(`content-desc`); unnamed nodes are skipped by the same rule as `snapshot.js` and covered by an offline test, but I had no
unlabeled control to try on a device. Known limits: ~5-20 s per decision (nimble), ASCII-only typing, an ad WebView
shows up as a control named by its click URL, custom-drawn UI is invisible, and DONE is still not proof.

## Design (original note)

## Accessibility-tree sources

| Platform | Source | Per element | Execution |
|---|---|---|---|
| Android | `adb exec-out uiautomator dump /dev/tty` (XML) | `class`, `text`, `content-desc`, `resource-id`, `clickable`, `enabled`, `focused`, `scrollable`, `checked`, `bounds` | `adb shell input tap x y`, `input swipe`, `input text` (ASCII only; IME or ADBKeyboard for the rest), `input keyevent` |
| iOS | XCUITest / WebDriverAgent `GET /source?format=json` (Appium XCUITest, already used for iOS Safari here) | `type` (XCUIElementTypeButton/TextField/...), `label`, `name`, `value`, `enabled`, `visible`, `rect` | WDA `/wda/tap`, `/element/{id}/click`, `/value`, `/wda/dragfromtoforduration` |

## Mapping to jev's action space

- **Observed element** = a node with a role that can be acted on: Android `clickable|checkable|long-clickable` or
  an editable `EditText`; iOS Button/Link/Cell/TextField/Switch/Slider/Picker. Skip `enabled=false`, off-screen
  (`bounds`/`rect` outside the window), and secure text fields (`password=true` / `SecureTextField`), as snapshot.js skips password inputs.
- **Name** = `text` > `content-desc` > `hint` > nearest labelled descendant (Android); `label` > `name` > `value` (iOS).
  Elements with no name get no action, the same rule as snapshot.js.
- **Operations**: `click` (tap bounds centre), `fill` (tap, select all, type), `select` (iOS picker wheel
  `setValue`; Android spinner = click then click the option), `scroll` (swipe inside the nearest `scrollable`
  container; its bounds replace snapshot.js's panel `at`), `wait`.
- **Targets stay observed ids** (`e1`..`eN`) resolved by code to a bounds centre, never coordinates from the model.
- **Freshness**: hash `(package/bundle, activity/screen title, element names+bounds+states)`; recheck right before
  the tap, exactly like `Browser.fresh`. A dump is atomic enough on Android; on iOS a source call is ~0.5-1.5 s.
- **Page text** for `return_text` = names of visible non-interactive nodes (`TextView`, `StaticText`) in reading order.

## What the nimble prompts would need

- Say what the screen is ("Android app `com.x/.Main`, screen title ...") instead of a URL; drop URL-based hints.
- Platform vocabulary: tabs, bottom navigation, toolbars, back (system `BACK` / iOS back button) as a dedicated
  operation, since native apps have no browser history. Add a `BACK` action with no target.
- Larger element lists with weak names (icon buttons): include `resource-id` tail / accessibility `name` as a hint and
  cap at the same 250 actions with the same "what lies below" scroll hint from the scrollable container.
- Keyboard handling: after `fill`, an IME can cover targets; observe again and use bounds from the new dump.
- Failure modes to keep: no mutation retries, log execution before observing, verify the final screen independently.
- Not covered by either tree: custom-drawn UI (games, Flutter without semantics, canvas/maps). Those need a
  vision backend, which is out of scope for this loop.

## Prior art (checked 2026-10-04, from the awesome-jev list)

- [droidrun/mobile-jev](https://github.com/droidrun/mobile-jev) (MIT, TypeScript): Jev on a real Android phone. It
  observes through the Mobilerun cloud API (paid device and API keys), executes bounded actions (tap, type, back,
  open app from the installed-app list), keeps traces, and verifies the outcome by re-reading the screen. Reusable
  ideas: a `BACK` action, app discovery, separate setup/run/verify timing. Not reusable as is: hosted devices, TypeSafe
  keys, no local adb/emulator, no Ollama path.
- [awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use): macOS, OCR plus bounded Jev
  action selection (pixels, not an accessibility tree).
- [jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis): Android chat copilot; reads a visible conversation, fills the reply box, never sends.
- An "Agent Desktop" project using OS accessibility trees was mentioned by a search summary; not verified.

Conclusion: nobody offers a local, free adb/UiAutomator + local-model native loop, so the spike in this note is not duplicated work.
