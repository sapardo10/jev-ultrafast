# Native apps

**Android is implemented as a spike** (`jev_ultrafast/native.py`, `JEV_ANDROID_APP=<package>`); iOS is still only this
design. The DOM snapshot has no DOM in a native app, so the loop stays
`page -> indexed elements -> operation + target -> execution`; only the page source changes.

## Android task matrix (emulator, Ollama nimble, 2026-10-04)

`scripts/native_matrix.py` runs each task 3 times on a fresh-state emulator and verifies through adb, not through jev's DONE.

| Task | Pass | Steps | Seconds | Notes |
|---|---|---|---|---|
| Settings: turn on dark theme | 3/3 | 2 | 29 | `cmd uimode night` |
| Settings: screen timeout 30 minutes | 3/3 | 3 | 37 | `screen_off_timeout` = 1800000 |
| Settings: turn on airplane mode | 3/3 | 7 | 73 | `airplane_mode_on` = 1 (target is not on the home screen) |
| Contacts: create "Jose Nunez", phone 5551234, save | 3/3 | 6 | 74 | `content query` finds both rows |
| Flutter app (pausactive): onboarding to Dashboard | 3/3 | 5 | 63 | screen text |
| Flutter probe: icon-only hamburger, open Settings | 3/3 | 2 | 27 | screen text |
| Settings: turn off Wi-Fi | 0/3 | 15 | 147 | nimble does not know "Internet" is the Wi-Fi row |

Total 18/21. Fixes the matrix drove, each with an offline regression test:

- Material text fields are named by the label drawn inside them ("Phone"), never by their contents; an EditText covered
  by a Spinner is a dropdown; a flag emoji or `+1` is not a label.
- The IME is dismissed after typing (only if showing), so fields below it become visible.
- Scroll and BACK are withdrawn once they change nothing; scroll stays withdrawn on an activity whose end was seen
  until something is clicked; a click that BACK reversed is not offered again on that screen.
- BACK that leaves the app (home or the previous app) relaunches it; a transient empty foreground is waited out.
- Nimble state for screens: `opened_screens` and `typed`, placed after `page_text` (a history key placed earlier moved
  CLICK from 0.92 to 0.30 on the same screen); premature BLOCKED is re-asked while the screen can scroll.
- Fields the text helper refused, or that already hold the typed text, are withheld from the next choice.
- Compact unnamed controls are offered by position; system dialogs over a live app are observed.
- Unicode text: through the ADBKeyboard IME (`com.android.adbkeyboard`) when installed, else refused before touching
  the device with that instruction. Not exercised on a device (no APK was installed).

Still open: counts ("twice"), per-decision latency (5-20 s), semantic navigation when the target is not on screen and
has an unfamiliar name, WebViews and ads (named by their URL), custom-drawn UI, real-device runs, iOS.

Earlier spike notes: a Flutter probe (`examples/flutter_probe`, `flutter test` green) reproduces the icon-only hamburger.
A false DONE after an unrelated permission "Allow" was fixed by requiring the clicked label to share a word with the goal.

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
