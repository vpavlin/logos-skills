---
name: logos-basecamp-module
description: "Use when building or shipping a Logos Basecamp app/module — a desktop QML view plus a shared engine/sync core, an always-on headless hub, building the .lgx with logos-module-builder, publishing to a Basecamp package repo, or debugging \"view won't open\" / \"Invalid response\" / dropped-method / hash-mismatch / hub-receives-nothing failures. Keywords: Basecamp, ui_qml, core module, mkLogosQmlModule, mkLogosModule, logoscore, logos.callModule, .lgx, metadata.json, universal authoring, headless hub, Qt Remote Objects, Basecamp 0.3, builder 0.3.1, LIDL, onContextReady, AsyncResult, delivery v0.3.0."
---

Build a Logos Basecamp app as **two packages plus one engine**: a Qt-free **core module** that owns everything (engine, crypto, wire, sync, domain logic) and a **thin QML view** that only calls the core and renders its JSON. The same core binary runs behind the desktop view *and* standalone under `logoscore` as an always-on **headless hub** — one implementation, no drift between UI and server.[^1] This playbook is the how-to plus the silent failure modes that cost hours.

## Mental model

```
              ┌──────────────── your app ────────────────────┐
  desktop  →  thin ui_qml view (pure QML, no C++)            │  logos.callModuleAsync("<core>", action, [args], cb)
              │        renders <core>'s JSON                  │  ← one engine, two front ends
  hub      →  logoscore daemon ── loads ── <core> module ────┘  headless, always-on peer
```

Rules that follow from this:
- **All logic lives in the core.** The view has no engine copy, no crypto, no wire code. It calls actions and draws the returned JSON. If you find yourself duplicating a calculation in QML, it belongs in the core.[^1]
- **The core is `type:"core"`, `interface:"universal"`, Qt-free.** You write only `src/<name>_impl.{h,cpp}`; the class is `<NameCamelCase>Impl : public LogosModuleContext`. Public methods = the dispatch API (return JSON-serializable `std::string`), a `logos_events:` section = emittable events, `onContextReady()` = setup, `modules().<dep>` = typed callers for declared `dependencies`. No `Q_OBJECT`, no `.rep`. The plugin glue is generated from the header.[^2]
- **The view is `type:"ui_qml"` with NO C++ backend** — pure QML like the shipped `counter_qml` sample. A ui_qml plugin *with* a C++ backend that depends on a *custom* core module is exactly the combination that silently fails to open on some Basecamp builds; dropping the backend removes it and is truer to thin-UI anyway.[^7] (Note the qualifier: the failing cell is specifically **backend + a _custom_ core dep**. A ui_qml C++ backend that depends only on the **built-in `delivery_module`** loads fine — so a delivery-driven ui_qml module is a legitimate shape, and channels are reachable from its Qt caller per logos-reliable-channels.)
  - **Mechanically converting an existing `.rep`-backed view that won't load:** if the backend is a **1:1 proxy** (every slot is `return modules().<core>.<sameMethod>(args)`), it adds nothing and IS the failure cause. Delete it wholesale — `metadata.json`: drop `main` + `codegen`; remove `src/<name>_backend.{rep,cpp,h}` + the view's `CMakeLists.txt`; then in QML replace the replica with an **async** shim so call-sites barely change: `readonly property var backend: ({ createEvent: function(c,e,cb){ core("createEvent",[c,e],cb) }, … })` where `core(m,a,cb)` is the `callVia` helper below (`logos.callModuleAsync`). Code that used `logos.watch(backend.X(args), ok, err)` maps 1:1 to `backend.X(args, ok)`. Do NOT use a synchronous `callModule` shim, even though it keeps call-sites identical: it freezes the view during construction and on every slow call. Also **pin the view's builder to the core's rev** (an unpinned `logos-module-builder` builds the view against a different SDK than the core = a second cause of "won't load"). Verified on Scala `scala_ui`.[^7]

## Stand up a new core + view (checklist)

Core module (`<app>_core/`):
1. `metadata.json`: `{"name":"<app>_core","type":"core","interface":"universal","main":"<app>_core_plugin","dependencies":["delivery_module"]}` (drop the dep if no sync). **ASCII only** — a non-ASCII char in `description` breaks the JSON stamper.[^6]
2. `src/<app>_core_impl.h`: class `<App>CoreImpl : public LogosModuleContext`. Public methods return `std::string`. Add a `logos_events:` section for push updates. Add a `snapshot()` action that returns the full folded state JSON (see read-state rule below).
3. `CMakeLists.txt`: `include(LogosModule.cmake)` then `logos_module(NAME ${MODULE_NAME} SOURCES src/... INCLUDE_DIRS src ...)`. List **every** source incl. headers.
4. `flake.nix`: `logos-module-builder.lib.mkLogosModule { src=./.; configFile=./metadata.json; flakeInputs=inputs; }`.[^13]

View (`module/`):
5. `metadata.json`: `{"name":"<app>","type":"ui_qml","view":"Main.qml","icon":"icon.png","dependencies":["<app>_core"]}`.
6. `Main.qml` **at the module root**. The builder also accepts `qml/Main.qml` or `src/qml/Main.qml`, but keeping the entry at root avoids the path-resolution surprises that differ across Basecamp versions (the host resolves the view relative to the plugin root).[^7]
7. `flake.nix`: `logos-module-builder.lib.mkLogosQmlModule { src=./.; configFile=./metadata.json; flakeInputs=inputs; }`. Wire the core as a local input: `<app>_core.url = "path:../<app>_core"` with `.inputs.logos-module-builder.follows` and `.inputs.delivery_module.follows` so the view, core, and delivery all build against ONE SDK rev (avoids cross-module IPC skew).[^13]
8. `git add` every new file (`icon.png`, new sources) — nix flakes only see git-tracked files (see trap below).[^12]
9. Build: `nix build .#lgx-portable` for each. Publish both to the repo (below).

**Always build the PORTABLE variant. Make this a reflex, not a decision.** `.#lgx` is a
*dev* build: its variant is `linux-x86_64-dev`, and Basecamp is a portable build that
only takes `linux-amd64`. A dev package does not error — Basecamp passes it over in
silence, and the package manager shows it as "NOT AVAILABLE". The one place a dev build
is wanted is a **local** `lgpm install --file` (a different code path — do not decide
what to publish from what lgpm accepts). So: `.#lgx-portable` for anything you install
or publish, and if a package you built does not show up in Basecamp at all, **check the
variant inside the package first** (`tar tzf x.lgx | grep variants/`) before debugging
anything else — it costs one command and rules out the most common cause.

**Pin SDK inputs by their FULL 40-char commit, never a branch or short rev.** A `github:logos-co/<repo>/<branch>` or short-rev url resolves through GitHub's API, which **422s the moment that branch is renamed or deleted** — and the SDK's feature branches are volatile — so a from-scratch `nix flake` eval breaks with no code change. In every `flake.nix` input, pin `github:logos-co/<repo>/<full-40-char-sha>`, and keep the three inputs (`logos-module-builder`, `delivery_module`, your core) on **one** builder rev via `follows`. Derive the current-good SHAs from a **known-good `flake.lock`** (`nix flake metadata`), not from memory. A working triple observed in practice: `logos-module-builder afe4430ee6eb7ba45c08a516a43e18500720c715`, `delivery_module 0fb3a7427b29c98ab0fa2465bcd1e90cbfdf50a3` — treat these as a starting point to verify, not gospel; the 0.2.0-era desktop builder is a different rev (`021013458d87…`), so match the rev to your target Basecamp.[^13] For **Basecamp 0.3.x** the builder is the release tag `0.3.1` (release tags are stable; it's branches that vanish), and every module in the chain must use it — see the 0.3.x section.

## The read-state rule (the #1 gotcha)

The generated cross-module dependency-caller surfaces **only action-style methods**, and events are **not always delivered** to QML. So:

- **Do NOT expose read state as a zero-arg getter** and expect the view to call it. Getters like `budgetJson()`/`status()`/`fingerprint()` empirically get dropped from the generated caller (and a call to a method the installed core lacks returns the host-level string `{"error":"Invalid response"}`).[^3]
- **DO deliver read state two ways, both belt-and-suspenders:**
  1. A dispatchable **action** `snapshot()` that returns the full state JSON with side data (status, fingerprint, etc.) folded in. The view **polls it on a Timer** (~2.5 s) and on `Component.onCompleted`.[^3][^8]
  2. A `logos_events:` event (e.g. `stateChanged(std::string json)`) emitted on every change, subscribed via `logos.onModuleEvent(...)` + a `Connections { onModuleEventReceived }`. Seed the initial emit from an action (`resync()`), since events may arrive only after a nudge.[^3]

**Core side — declare + emit an event (minimal):** declare it in the header's `logos_events:` block and emit through the generated helper the builder injects for each declared event; emit from any state-changing method (and from `resync()` to seed):
```cpp
// in <App>CoreImpl (header):  logos_events:  void stateChanged(std::string json);
void <App>CoreImpl::pushState() { emitStateChanged(snapshotJson()); }   // generated emit<EventName>(...)
```
If the emit fires on a non-Qt thread (e.g. a delivery callback), marshal it back onto the module's thread before emitting — cross-thread signals are dropped (see the headless-hub gotcha below).

**Calling a dependency core (the caller cheat-sheet).** For each name in `dependencies`, the builder generates a typed caller reachable as `modules().<dep>`. The exact generated method names are derived from that dep's public action signatures — read the dep's own header (or an existing consumer's `.cpp`) to get them; there is no separate registry. Sync-transport shape you'll use most (the `delivery_module` dependency): `createNodeAsync(cfgJson, cb)`, `startAsync(cb)`, `subscribeAsync(topic, cb)`, `channelCreateAsync(channelId, contentTopic, senderId, cb)`, `channelSendAsync(channelId, msgJson, cb)`, plus receive callbacks registered via `onMessageReceived(cb)` / `onChannelMessageReceived(cb)`. Confirm the async vs sync variants and arg order against the pinned rev's header before wiring — see `logos-reliable-channels` for the semantics.

**Never make a *blocking* cross-module call from a view — not at load, not on a click, not in a Timer.** `logos.callModule(...)` is a **synchronous IPC** with a 20 s timeout: while it waits, the view's whole thread waits. During load that is "the view does not load" (it compiled; it's frozen); on a click it's a UI that hangs for seconds whenever the core is busy; in a poll it's a periodic stutter that compounds into a wedge when calls pile up. Use **`logos.callModuleAsync(module, method, args, callback, timeoutMs)`**, and on a host without it fall back to a `callModule` deferred with `Qt.callLater` so at least the current frame paints. Route **every** call through one helper so nothing slips back to the blocking form.[^async]

Two companions make async safe:
- **Single-flight every poll.** A `Timer` that fires again before the previous async answer arrives stacks requests on a slow core. Keep a `busy` flag per poll; if a refresh is requested while one is in flight, set `again` and run once more when it lands. Time out a stuck `busy` (e.g. 45 s) so one lost reply can't stop the poll forever.
- **Guard double-fire on actions** (a create/save button tapped twice while the first call is in flight) with a per-action flag, and show success/failure when the callback returns — no silent actions.

**Basecamp 0.2.3 hands string results to QML JSON-quoted** (`"abc"` arrives as `"\"abc\""`). Anything you pass back to the core — an id, a content reference — must be unquoted first, or the core looks up a key that doesn't exist and the feature "hangs" with no error. Strip one layer for scalars (`unq`), and parse up to a few layers for JSON (`asState`).[^quoted]

View skeleton that works:
```qml
property int callTimeoutMs: 20000
function callVia(mod, method, args, cb) {           // the ONLY way the view talks to a module
  var a = args || []
  var done = function (raw) { if (cb) { try { cb(raw === undefined || raw === null ? "" : raw) } catch (e) { console.warn(e) } } }
  if (typeof logos === "undefined" || logos === null) { Qt.callLater(function () { done("") }); return }
  if (typeof logos.callModuleAsync === "function") {
    try { logos.callModuleAsync(mod, method, a, done, root.callTimeoutMs) } catch (e) { Qt.callLater(function () { done("") }) }
    return
  }
  Qt.callLater(function () { var r = ""; try { r = logos.callModule(mod, method, a) } catch (e) {} done(r) })
}
function core(method, args, cb) { root.callVia("<app>_core", method, args, cb) }
function unq(raw) { return String(raw === undefined || raw === null ? "" : raw).replace(/^"|"$/g, "") }
// Bridge may return raw JSON or a quoted/escaped JSON string — accept both:
function asState(raw){ var s=String(raw||"").trim();
  if(s.charAt(0)==='"'){ try{s=String(JSON.parse(s)).trim()}catch(e){return null} }
  if(s.charAt(0)!=="{") return null; var o; try{o=JSON.parse(s)}catch(e){return null}
  return (o && o.error===undefined) ? s : null; }
property bool refreshBusy: false
property bool refreshAgain: false
function refresh() {
  if (root.refreshBusy) { root.refreshAgain = true; return }
  root.refreshBusy = true
  root.core("snapshot", [], function (raw) {
    var b = asState(raw); if (b) root.stateJson = b
    root.refreshBusy = false
    if (root.refreshAgain) { root.refreshAgain = false; root.refresh() }
  })
}
Timer { interval: 2500; running: true; repeat: true; onTriggered: root.refresh() }
Component.onCompleted: { logos.onModuleEvent && logos.onModuleEvent("<app>_core","stateChanged"); refresh(); }
```
On a **mutation**, have the core return the fresh state JSON, so the view renders straight from the instance that applied the edit (don't wait for the next poll).[^8]

**Inputs vs the polled state.** A `TextField { text: root.st.me.name }` is re-evaluated on every
poll, so it wipes what the user is typing ("it clears before I can hit save"). Give the field a
`saved` property (bound to the state) and an `edited` flag set by `onTextEdited`. Copy `saved` into
`text` only while the field isn't focused or edited. Clear `edited` after a successful save, or
when focus leaves an unchanged field, and let Escape restore the saved value. Test it by typing
into the field in an offscreen harness and then forcing refreshes.[^fields]

## Style the view with the Logos design system (do NOT hand-roll QtQuick)

A `ui_qml` view must use the official **`logos-design-system`**, not bespoke `QtQuick.Controls`. The Basecamp host **bundles** it (it's a transitive dep in the module `flake.lock`), so it resolves at runtime with **no extra flake input** — just import it:[^ds]

```qml
import QtQuick
import Logos.Theme      // design tokens (singleton `Theme`)
import Logos.Controls   // themed components (Logos*)
```

- **Components** (use these, not raw QtQuick): `LogosText`, `LogosButton` / `LogosIconButton`, `LogosTextField`, **`LogosCopyableText`** (a selectable/copyable value — ideal for a pairing code / shareable secret / address), `LogosComboBox`, `LogosSearchBar`, `LogosTable` + `LogosTableColumn`, `LogosTabBar` + `LogosTabButton`, `LogosCheckbox`, `LogosDialog`.
- **Tokens** (never hardcode a color/spacing/radius/font): `Theme.palette.*` (`text`, `textTertiary`, `background`, `surface`, `surfaceRaised`, `primary`, `success`, `warning`, `border`, `borderHairline`, …), `Theme.spacing.*` (`tiny:4` … `xxlarge:40`, `radiusSmall` … `radiusPill`), `Theme.typography.*`.
- **Reference + catalog:** copy the consumption pattern from a real consuming view;[^ds] browse the components + tokens with the design-system storybook (`nix run` in `logos-co/logos-design-system`).

A hand-rolled `QtQuick.Controls` view is the classic "why does this look off / terrible" smell — using the design system is what makes a view look like part of Logos instead of bespoke.

**Mind the host's bundled version (a load-failure trap).** The available components *and their properties* are gated by the design-system version the **target Basecamp bundles**, not the latest repo. On an older host (e.g. 0.2.0), a newer **type** is `"<X> is not a type"` and a newer **property** is `"Cannot assign to non-existent property <p>"` — **both stop the whole view from loading** (`Failed to compile ui_qml view … Failed to load UI module`), whereas a missing `Theme.*` **token** just evaluates to `undefined` and degrades to a default (ugly, not fatal). So: verify every `Logos*` type/property against the host you actually target. The proven-safe baseline for Basecamp 0.2.0 is what a known-good shipped view uses — **`LogosText` + `LogosButton` with basic props** (as in the Perun reference[^ds]). For anything newer (`LogosTextField`, `LogosCopyableText`, `LogosButton.variant`/`Variant`, etc.), either confirm it exists in the host's bundle or fall back to a plain `QtQuick.Controls` control styled with `Theme.*` tokens (define one `component AppField: TextField { color: Theme.palette.text; background: Rectangle { color: Theme.palette.surface; … } }` and reuse it). A read-only selectable `TextField` + a `LogosButton` "Copy" (backed by an off-screen `TextEdit { }`.`copy()`) replaces `LogosCopyableText` safely.

## Error handling — every outcome is visible, nothing fails silently

The transport already fails silently (`logos-distributed-debugging`); the app on top must not add to it. The contract across core and view:

- **Core methods return a result object, never throw across the IPC boundary:** `{"ok":true, …data}` or `{"ok":false,"error":"<a sentence the user can act on>"}` ("Form is closed", "Not a member of this calendar" — not `"exception"` or an errno). Catch at the method boundary; an exception escaping into the host can take the module down. Log the technical detail with a subsystem tag; return the human sentence.
- **Refuse up front instead of dropping later.** If the fold would discard a write (not a member, closed container, wrong identity), check the same condition before authoring and return the error — never store-then-fold-away, which looks like success and then vanishes.
- **The view reports both outcomes of every action:** a toast on success ("Form published") and the core's `error` on failure. An empty or unparseable result is a failure too, with a hint at the likely cause ("Request failed - is <app>_core loaded?"). One helper (`act(method, args, okMsg, onOk)`) keeps this uniform.
- **Timeouts are failures with a message.** `callModuleAsync` takes a timeout; when it fires, say what was being attempted. Polls that time out stay quiet, but surface "can't reach the core" after several misses rather than showing stale state as current.
- **Long operations show progress and a terminal state** (pending → done/failed, with a timeout), never an indefinite spinner. Storage transfers, joins and catch-up are the usual suspects.
- **Show versions and a copyable diagnostics block** somewhere (core version, view version, delivery status, peer count, counters). It turns a support thread into one paste (see `logos-distributed-debugging`, Move 8).

## Render peer-supplied text as PLAIN text (privacy)

Qt's `Text`/`Label`/`ToolTip` default to `textFormat: Text.AutoText`: anything that *looks* like HTML is rendered as rich text, and a remote `<img src="https://…">` is **fetched the moment the item is shown**. In a multi-writer app the titles, names and messages come from other people, so one crafted event title makes every reader's machine contact a stranger's server (IP + timing leak), and `<b>`/`<br>`/`<font>` can fake UI. Set `textFormat: Text.PlainText` on every text-displaying item. Input controls (`TextField`/`TextArea`/`TextEdit`/`TextInput`) don't render HTML and are fine.[^plain]

`qml-plaintext.py` (next to this file) adds it to every `Text`/`LogosText`/`Label` that lacks one; `--check` exits non-zero if any is missing — run it in CI or before each release:
```sh
python3 qml-plaintext.py module/*.qml            # rewrite in place
python3 qml-plaintext.py --check module/*.qml    # gate
```
If you genuinely need formatting, build it from structure (separate `Text` items, bold via `font.bold`), never by interpolating peer strings into markup.

## Builder + glue quirks (symptom → cause → fix)

| Symptom | Root cause | Fix |
|---|---|---|
| A core method is uncallable from the view / `"Invalid response"` | Generated dep-caller surfaces only **action-style** methods; zero-arg getters are dropped[^3] | Expose reads as an action (`snapshot()`) + a `logos_events:` event; never a bare getter |
| One method silently vanishes; others fine | The glue **drops any method with a trailing `//` comment** on its declaration line[^4] | Move the comment to the line *above* the declaration; keep decls comment-free |
| A method "succeeds" but never fires | The glue **drops/no-ops methods with >4 args**[^5] | Keep public methods ≤4 args; pass structured data as **one JSON string** (`editTxn(id, patchJson)` not 5 positional params) |
| Metadata rejected / stamp fails on an em-dash | An em-dash/ellipsis in metadata `description` breaks the JSON stamper (still real) | Keep metadata `description` **ASCII-only**. NB: the old "non-ASCII in a header *comment* stops the generator" is builder-rev-specific (0.2.0-era) — NOT reproducible at `afe4430e`, which strips comments Unicode-safely; header comments are fine there[^6] |
| `int.dump()` compile error | Universal dispatcher `.dump()`s the return | Return `std::string`, never `int`/`bool` |
| View's deps load but no tab/view ever opens | ui_qml + C++ backend + custom-core dep is the failing combo; also entry-point field mismatch by Basecamp version[^7] | Make the view **pure QML** (no backend); put `Main.qml` at root; match the Basecamp version's entry field (see below) |
| Cross-module `std::string` return arrives double-wrapped | Returns can come back **double-encoded** through the bridge[^11][^20] | Unwrap up to twice: `for(i=0;i<2&&typeof r==="string";i++) r=JSON.parse(r)` |

## Basecamp version differences (establish the target FIRST)

Behavior differs sharply by Basecamp version — pick the target version before designing or debugging, and test the artifact you actually ship on it.[^7] The table below covers the 0.2.x line and an early 1.0.0 dev build; **Basecamp 0.3.x / builder 0.3.1 is a bigger step and has its own section next.**

| | older release (e.g. 0.2.0) | newer dev (e.g. 1.0.0) |
|---|---|---|
| ui_qml entry point | **`view` field** (stock builder output) | `manifest.main.<variant>` (needs post-build patch) |
| module→QML events | **delivered** | may never arrive → must poll |
| dependency module instances | one | 2+, round-robined → divergent in-memory state |
| repo `.lgx` variant | portable `linux-amd64` | `linux-amd64-dev` |

Consequences you must code for:
- **Multi-instance guard.** If Basecamp round-robins `callModule` across several core instances, one that hasn't loaded the shared log yet returns *empty* state. Don't let a poll blank a populated view: `if (eventCountOf(new)===0 && eventCountOf(current)>0) return;` — keep what you have, let the next poll (from a loaded instance) refresh.[^9]
- **Entry field.** On an older target the stock `view:"Main.qml"` build just works. On a 1.0.0-style target you must post-process the built manifest to `main:{"<variant>":"Main.qml"}` (the *pinned* builder emits `main:{}` and refuses a metadata `main`; a newer builder accepts `view` directly). Don't apply the `main` patch to an 0.2.0 target — it's a 1.0.0-only workaround.[^7]
- The **shipped sample inside the installed Basecamp is ground truth** — when the tutorial and the installed app disagree, mirror `counter_qml` from *that* Basecamp.[^7]

## Basecamp 0.3.x / module-builder 0.3.1 — what changes

Everything above still holds unless noted here. The 0.3 line changes the runtime protocol (0.2 → 0.9), packaging, headless tooling, and the bundled delivery/storage modules. The step-by-step migration of an existing app is `logos-basecamp-0.3-port`; these are the rules a module author needs either way.[^p03]

- **Rebuild the whole stack on one builder.** Pin `github:logos-co/logos-module-builder/0.3.1` (a release tag; feature branches are still the volatile thing to avoid). Modules built on 0.2.x *load* in a 0.3 runtime, but their startup calls to other modules are rejected, so a mixed stack silently never starts its transport. Ship every module of an app chain rebuilt together.
- **Contracts are LIDL, derived from your impl header.** The builder generates `<name>.lidl` from `src/<name>_impl.h` (same as late 0.2.x) and ships it in the package under `assets/lidl/`. For a universal core there is nothing new to write. Point the builder at a non-default header with `"codegen": {"impl_header": "path/to/x_impl.h"}`.
- **Every dependency must publish a contract.** 0.3.1 refuses a `dependencies` entry that has no LIDL (and a core without `"interface"`). A legacy Qt-plugin dependency blocks your build until it is ported to a universal impl header (or a hand-written `.lidl` is supplied). See `loam-keycard` for a worked port.
- **Never call another module from `onContextReady()`.** The 0.3 runtime rejects those calls ("auth token not recognized" — the module's token isn't registered yet; official modules hit it too). Set up local state there and defer every cross-module call (start the transport, init storage, subscribe) with a `QTimer::singleShot(~1000, …)` on the module's thread.
- **Default arguments are dropped from the contract.** `createCalendar(name, identityId = "")` becomes a one-argument method over IPC and the caller's second argument is silently lost (this is true on 0.2.x too). Declare every parameter explicitly; handle "empty" in the body. The trailing-`//`-comment and >4-argument rules from the quirks table still apply.
- **Call timeouts.** From QML, `logos.callModule` waits at most ~1.5 s for a module that isn't reachable yet and returns `{"error":"Module not reachable yet"}`; `callModuleAsync` waits for the module, bounded by its timeout (default 30 000 ms). From C++, the generated `fooAsync(…, cb)` still times out at the runtime default; when the callee may legitimately take longer (Storage waits up to 30 s for a manifest), use `fooAsyncResult(…, cb(logos::AsyncResult<T>), timeoutMs)` and treat a timeout as "maybe still running", not "failed".
- **Events before arming are lost.** `logos.onModuleEvent` is non-blocking and re-arms itself, but anything emitted before you armed it is gone — always pair it with a state read (`snapshot()`), which this skill already requires.
- **Views run under their own identity.** Declare every module a view calls in its `dependencies` (e.g. the view calls `loam_core` directly → list it). Enforcement is fail-open today; don't rely on that. Remove `"interface"` from `ui_qml` metadata.
- **Dependencies may carry version ranges** (`{"name":"x","version":"^1.2"}`), enforced at load, fail-closed; prereleases never satisfy a caret. The installed version is read from the plugin's **embedded** metadata, so bump `metadata.json` — editing only the `.lgx` manifest leaves the old version inside.
- **Icons are 256×256** (see the icon note below). Packages may also carry `manifest.sig` and `assets/`; keep both in any repack.
- **The host owns some modules.** Basecamp 0.3 and `logosctl` bundle `storage_module` 3.0 and initialise it themselves from `~/.logos_storage/config.json`; your `init()` is refused. Adopt the host's node instead (`logos-storage`).
- **Delivery v0.3.0 (upstream) has a different event and config shape** — `messageReceived` gains a `source` argument, start completion arrives as `nodeStarted`, config is layered — and RLN can stop every send. See "Delivery/wire notes" below and `logos-rln-budget`.
- **Headless = `logosctl`.** `logoscore`/`logos-hub` are replaced by the `logosctl` CLI (sessions, `install`, `module load`, `call`, `watch`). See `logos-headless-logosctl`.
- **Cross-repo auto-upgrade.** With several repos added, Basecamp offers the **highest version of a package across all of them**. A fork you publish as `delivery_module 0.1.4` will be "upgraded" to the official 0.3.x the moment a user adds the official catalog. Prefer renaming the fork (or pinning dependency ranges); versioning it above upstream only works until your apps move to upstream, after which the fork outranks upstream the other way. Test 0.3 builds from a separate repo so nothing auto-upgrades an 0.2 user.

## View ↔ core version skew

The view and core are **independently-installed packages** and can skew. When the view calls a core method the *installed* core lacks, `logos.callModule` returns `{"error":"Invalid response"}` — that string comes from the **Basecamp host**, not your binary. It almost always means **the view is newer than the core**, not a dispatch bug.[^10] Defenses:
- Whenever a view change calls a **new** core method, bump and publish **both** packages and tell the user to update both.
- **Gate new features on a field the new core adds** to the state JSON, so an old core hides the feature instead of erroring: `readonly property bool hasFeatureX: state.someNewField !== undefined`, and have the mutation's fallback toast "Update <core> to X" instead of the raw host error.[^10]

## Calling another core module (e.g. `qr`) returns null

`logos.callModule("qr","generate",[text])` returns **`null`** from a pure-QML view: that core is Qt-free and the legacy synchronous `callModule` bridge can't reach it. Its README documenting `callModule` is stale — the shipped source is truth.[^11] **Fix: vendor it into your own core.** For QR, drop Nayuki's MIT `qrcodegen.{hpp,cpp}` into your core, expose an action returning the **matrix** `{"ok":true,"n":N,"cells":[bool...]}`, and draw it on a QML `Canvas` (the sandbox blocks image `data:` URIs). This keeps the app dependency-free — nothing extra for the user to install.[^11]

## Cross-platform identity & signing parity (secp256k1) — when the desktop core must match the phone byte-for-byte

If events carry per-author signatures, the C++ core has to produce identities and signatures **byte-identical** to the JS/mobile reference, or a desktop-authored event verifies as invalid on the phone (and vice-versa) and gets dropped by the sig-gate. The recipe that achieved bidirectional parity:[^23]
- **Address** = `"0x" + hex(sha256(compressed_pubkey_33B))[24:64]` (last 20 bytes of the hash of the *compressed* 33-byte pubkey). Same on both sides — never hash the uncompressed key.
- **Canonical message** to sign = a fixed-order pipe-joined string, e.g. `"<app>-sig-v1|" + type + "|" + wall + "|" + ctr + "|" + dev + "|" + id + "|" + cjson(payload)`, where **`cjson` = sorted-key, compact (no-space) JSON**. The one thing that silently breaks parity is JSON canonicalization — key order and whitespace must match the JS `JSON.stringify`-with-sorted-keys exactly.
- **Sign** = ECDSA over secp256k1 with **low-S normalization**, output compact `r‖s` 64 bytes (NOT DER). Skipping low-S makes half your signatures fail the other side's verify at random.
- **C++ impl**: OpenSSL `EC_KEY` (the deprecated-but-present API is fine; don't compile the core with `-Werror` or the deprecation warnings fail the build). Keep it in one header (`<app>_identity.hpp`) and **add it to the module CMakeLists `SOURCES` + `git add` it** — the Nix build only copies git-tracked, listed files (the same trap as §"Building the .lgx" below).
- **Guard with golden vectors**: sign the same fixtures in JS and C++ and assert equal bytes both directions before trusting it. Provenance and the participant-vs-gated admission split live in `logos-multiwriter-sync` (its sig-gate note).[^23]

## Building the .lgx, the git-tracked-files trap, and the icon

`mkLogos{,Qml}Module` produces `.#lgx` (dev) and `.#lgx-portable` outputs. An `.lgx` is a **gzip'd tar** with `manifest.json` + `variants/<variant>/…` at root; the builder relocates your flat source files under `variants/<platform>/`, and the manifest carries `name`/`view`/`icon`/`main`/`hashes`.[^13][^14]

- **Git-tracked-files trap:** nix flakes only see **git-tracked** files. A new asset (icon, vendored source) that isn't `git add`ed makes the build error ("To make it visible to Nix…" or CMake "Cannot find source file"). `git add` before every build. A dirty tree is otherwise fine — tracked-file edits are picked up.[^12]
- **Icon:** put `"icon":"icon.png"` in the view's metadata (flat path, sibling to `"view"`). The builder bundles it into the variant (`install -D -m644 ${icon} $out/lib/${icon}`, relocated under `variants/<platform>/`); the host loads it at runtime relative to the manifest dir (`setWindowIcon` from the metadata `icon` field). `git add module/icon.png` or the build fails.[^12] **Size is version-scoped:** builder 0.2.x accepted any square PNG (512² was common); **builder 0.3.x packages a `ui_qml` icon only if it is exactly 256×256** (the packager checks it for manifest ≥ 0.4.0), so a 512² or 1024² icon blocks the build. Ship 256² for 0.3.x — it also works on 0.2.x.[^p03]

## Publishing to a Basecamp repo

- **Repos use the PORTABLE variant (`linux-amd64`), never `-dev`.** A `-dev` `.lgx` shows as **"NOT AVAILABLE"** in Basecamp's package manager (silent, no error). `lgpm install --file` (a *local* installer) demands `-dev` — a different code path; don't decide what to publish from lgpm's demand.[^15]
- A Basecamp repo is static files over **HTTPS**: `logos-repo.json` (identity card, schemaVersion 1, `indexUrl:https://HOST:PORT/.../index.json`) + `index.json` (schema 2, one entry per package, newest-version-first). Each version entry carries `url`, `size`, `sha256` (of the `.lgx` file), `rootHash` (= the manifest's merkle root), and the embedded `manifest`.[^15]
- **Regenerate `index.json` whenever any `.lgx` changes** — it embeds size/sha256/rootHash; a stale index means Basecamp downloads bytes that don't match the hash and **silently refuses to install**. **Bump the version on every republish** — the GUI caches the index and won't re-notice a same-version change.[^15]
- Host it as a `systemd --user` service with `Restart=always` + `loginctl enable-linger`; it serves files live (no restart after republish). TLS needs a **leaf** cert (`CA:FALSE`+`serverAuth`) — `openssl req -x509` defaults to `CA:TRUE` on OpenSSL 3 and the host rejects a CA cert used to terminate TLS. Verify the repo answer with **`lgpd`** (logos-package-downloader — the catalog tool the repo view uses), NOT lgpm: `repo add <…/logos-repo.json>` → `repo refresh` → `--json info <pkg>`, and compare your `variants` to an official package that IS available (e.g. `chat_ui`). A one-command `regen.sh` that builds both `.lgx-portable`, installs them, regenerates the index, and rewrites the identity card is the reliable shipping path.[^15]

**More platforms (macOS Apple Silicon, Linux ARM64):** build them on GitHub runners and merge them into the same package and version — see the `logos-multiplatform-modules` skill.

For hot-swapping a single `.so` into a signed `.lgx` **without a full rebuild** (the merkle-hash repack), and the full publishing walkthrough, see [`publishing-and-repack.md`](publishing-and-repack.md) (next to this file).

## Running the core as a headless hub — the gotchas

The **same** core `.lgx` runs standalone as an always-on peer. **On the 0.3.x runtime use `logosctl`** — the session layout, install/load/call commands and its traps are in `logos-headless-logosctl`; the gotchas below are about the core and still apply. On 0.2.x: daemon form `logoscore -D -m <modulesDir>` (background) then `logoscore load-module <core>` (its manifest pulls the delivery dep). Stage the bundled `capability_module` into the modules dir too, use PORTABLE `.lgx`, and run it as a `systemd --user` service with `Restart=always`.[^17] Three things silently break a headless hub — none surface as an error:

**Gotcha 0 — nothing drives it.** A GUI stays synced because its view polls `snapshot()` on a Timer, which lazily starts delivery and runs the periodic reconcile. A headless node has no such poll. Arm a self-drive tick from an env flag checked in `onContextReady()`; without it delivery never bootstraps and nothing syncs. It MUST be a **QTimer on the Qt event-loop thread**, never a `std::thread` — the delivery module's async calls (`createNodeAsync`/`send`) only dispatch their callbacks on the host event-loop thread, so a worker-thread driver leaves the callback undispatched and `createNode` hangs.[^17][^21]

**Gotcha 1 — it connects and SENDS but RECEIVES nothing (the important one).** The node meshes and publishes, `send` works, yet the receive counter stays 0. Cause: the delivery module emits its `messageReceived` signal **directly from its FFI/worker thread, unmarshaled**. Under `logoscore`, cross-module events replicate via **Qt Remote Objects, which silently DROPS a signal emitted off the object's Qt event-loop thread** (and worse, can tear down the QRO connection so later method calls stop too). Your subscribing core is blameless — its handler fires perfectly when the emitter behaves; it works under the GUI Basecamp only because that host runs modules in-proc / marshals the emit. **The fix is upstream, not in your module:** logos-cpp-sdk commit **`d77c3dd`** (PR #68, "marshal provider events onto the source thread"). Run the hub under a `logoscore` built on a cpp-sdk at or past that commit (the newer daemon-CLI `logoscore` embeds it). No code change to your core. Reproduce/confirm with a 2-module emitter→consumer rig: emit from a QTimer → events arrive; emit from a `std::thread` → 0, silently dropped.[^21]

**Gotcha 2 — it drifts off the fleet: "No peers for topic".** Starting the core with a bare delivery config (`{"mode":"Core","preset":"..."}`) gives the node **zero bootstrap peers** ("creating kademlia discovery as seed node (no bootstrap nodes)"); it relies purely on discovery, loses the fleet, and logs `No peers for topic` / `NoPeersToPublish` — nothing syncs. Fix: put **`entryNodes`** (the same fleet peers your mobile/desktop clients dial) in the delivery config, and have the core **merge an env-provided config JSON over its default** so you can pin the fleet without a rebuild.[^22]

**Gotcha 3 — the hub's delivery_module must match the clients'.** A hub on an older delivery build than the desktops/phones can mesh, receive live traffic, and still never converge: a client's catch-up that the newer build sends **segmented** can't be reassembled by the older one, so a calendar joined on the hub stays empty with no error. Upgrade the hub's delivery_module to what clients ship, and check versions first when "the hub has the room but no history".[^hubver]

**Hub as a Logos Storage (Codex) cache for NAT-ed users.** Two desktops behind different NATs can't fetch each other's uploads directly. A public hub fixes it when it (a) runs `storage_module` with its public address as `extip` plus `autonat-server` and `relay-server` enabled, so NAT-ed nodes learn they're private and can be reached through it, and (b) **caches every content reference it sees** (fetch on first sight, retry for ~30 min while the uploader comes online), so later readers fetch from the hub, not the uploader. One nim-libp2p trap: `getProviders` ignored the node's *own* local provider records, so the hub couldn't find content it had just been told about; that needed a patch (`kad: include local records`) on the hub's storage build.[^storhub]

Delivery/wire notes: a core module gets the **std** delivery caller (`createNode(std::string cfg)`, `subscribe`, `send(topic, LogosMap)`, `onMessageReceived(hash,topic,payload,ts)`); a ui_qml backend would get the Qt caller instead. Payloads ride as **base64 inside JSON** so binary crosses the FFI as a JSON string (newer builds emit `{"_bytes":"<URL-safe base64>"}` — decode `-`/`_` too, or long frames truncate). `createNode`'s config is a named preset (`logos.dev`/`logos.test`/`twn`) plus pinned `entryNodes` (gotcha 2).

**The delivery API is version-scoped — check which `delivery_module` is installed before wiring it:**

| | 0.1.x (channels fork, lib v0.38.1) | upstream v0.3.0 (lib v0.39) |
|---|---|---|
| `createNode` config | **flat** `{mode, preset, relay, entryNodes, tcpPort}`; the layered shape is *rejected* ("Failed to create Delivery context") | **layered** `{mode, preset, messagingOverrides:{entryNodes, …}}`; flat still accepted as legacy (any bare top-level key switches to it; unknown keys are errors) |
| `messageReceived` | `(hash, topic, payload, ts)` | `(hash, topic, payload, **source**, ts)` — `source` = `live`/`history`; read the timestamp from the *last* argument |
| start completion | `start()` returns | wait for the **`nodeStarted(success, msg)`** event; `start()` succeeding proves nothing |
| history | no Store query (backfill = republish/RBSR) | `storeQuery`; store catch-up **on by default** → replays arrive with `source:"history"`, dedup by id |
| channel payload on the wire | raw SDS content | wrapped in a `SegmentMessage` protobuf (same marker) — see `logos-reliable-channels` |
| RLN on `logos.test` | dormant | on: without a membership the node **receives but every send fails** — see `logos-rln-budget` |
| other | `version()` | `version()` removed; `getConnectionStatus`, RLN state events; metrics prefixed `logos_delivery_`; QUIC on by default |

On 0.1.x, `start()` reports success even when the node then fails to start (the error is only in the log) — on v0.3.0 trust only `nodeStarted`. On both, the event's topic argument is not always the topic (on the raw path it can be the message hash) — derive the topic from the frame.[^p03] One more silent trap: **newer delivery builds marshal the `send` payload as a JSON byte ARRAY and throw `type must be array, but is string` on a string** (→ the module aborts, signal 6). Probe once (array→string), cache the shape the local delivery accepts, and reuse it — so the same binary works on an old-SDK GUI host (string) and a new-SDK hub (array) with no env flag.[^19]

## Testing pitfalls

- **(0.2.x) `logoscore -c 'mod.method(a,b)'` silently mangles args** (the later daemon-CLI `call` and 0.3's `logosctl call` with `str:`/`json:` prefixes don't): numbers are typed `int` (a `QString` slot then no-ops and returns current state with **no error**), double-quotes are **stripped** (breaks JSON args), and args **split on every comma** (shreds a JSON object). You cannot pass a number or a JSON string through `-c`. To test a method taking numbers/JSON, build the payload **in C++** (a temporary self-test method using your real serializer) and call the target directly; verify, then delete it. A non-numeric text arg *does* marshal as `QString` — use that to confirm the body even runs.[^16]
- **Render the view without a real host:** an offscreen `QQuickView` (software backend) that loads the module's `Main.qml` with a mock `logos` backend injecting a fixture via `setProperty("stateJson", ...)` catches runtime QML errors qmllint can't and produces screenshots. A `state` property derived from the JSON **lags** its change-handler — wrap follow-up reads in `Qt.callLater(...)` so bindings settle. Point the harness at the real entry file (`module/Main.qml`), not a stale `module/qml/Main.qml` path.[^18]
- **Version skew "Invalid response"** and **the multi-instance empty-state blank** both look like your code is broken but are host behaviors — check versions and the instance guard before chasing a logic bug.[^9][^10]

## Where else this applies

The split generalizes to any Basecamp app with multi-writer sync or cross-user sharing — the origin project happens to be a budget, but nothing here is domain-specific:

- **Shared calendar / scheduling app:** core module owns the event store, invite crypto, and sync; a `snapshot()` action returns the folded month/agenda JSON the pure-QML view renders; the same core runs as a headless hub so invites keep syncing while every device is asleep. Version-skew gating: a new "RSVP" feature is gated on an `rsvp` field appearing in the snapshot JSON.
- **Activity / run tracker:** core folds an append-only log of activities from phone + desktop; the headless hub is the always-on peer that backfills a device that was offline; QR pairing (vendored encoder) shares a training group. Keep the "record activity" method ≤4 args by passing the sample as one JSON string.
- **Notes / Q&A app:** core holds the CRDT note log + membership; the thin view polls `snapshot()` and never re-implements merge logic; publishing a new note-type is a bump-both-packages event, and the view degrades gracefully on an old core by hiding the type behind a JSON-field gate.

In every case the load-bearing reusable pieces are identical: action-only read surface (`snapshot()` + event), ≤4-arg / JSON-string methods, ASCII-only headers, pure-QML view, portable-`.lgx` + version-bumped index repo, and the headless-hub recipe with its three gotchas (self-drive QTimer on the event-loop thread; the cross-thread `messageReceived` drop fixed upstream in cpp-sdk `d77c3dd`; `entryNodes` or the node is isolated).

## Sources & evidence

All paths under `github.com/vpavlin/kym` (checked out at `/home/vpavlin/kym`) unless noted; memory files under `~/.claude/projects/-home-vpavlin/memory/`. Every claim was re-verified against current source in this session.

[^1]: `kym_core/src/kym_core_impl.h:15-25` (class doc: the core runs BOTH standalone under logoscore AND behind the ui_qml view, one implementation of engine/sync) + `docs/decisions.md` "ui + core split". Memory: `kym-desktop-view-fix`.
[^2]: `docs/logos-dev-notes.md` §"Module types" (`core`: universal authoring, class `<NameCamelCase>Impl : LogosModuleContext`, public methods = API, `logos_events:`, `onContextReady()`, `modules().<dep>`; Qt-free) + `kym_core/src/kym_core_impl.h` (public methods return `std::string`, `logos_events:` at line 129, `onContextReady()` at 127), `kym_core/CMakeLists.txt` (`logos_module(NAME … SOURCES … INCLUDE_DIRS src)`), `kym_core/flake.nix` (`mkLogosModule`).
[^3]: `docs/logos-dev-notes.md` §"Codegen quirks" (dep-caller exposes only action-style methods; getters `budgetJson`/`status`/`fingerprint` dropped; workaround = deliver read-state via a `logos_events:` event + fold fields into the JSON, seed via `resync()`) + `kym_core/src/kym_core_impl.h:82-86` (`snapshot()` dispatchable read, doc: "the deployed basecamp does not deliver `budgetChanged` to QML") + `module/Main.qml:173` (poll `snapshot`). Memory: `kym-desktop-view-fix`.
[^4]: `kym_core/src/kym_core_impl.h:114-115` — in-source warning: "keep these declarations free of trailing // comments — the module glue generator skips any method with a trailing comment on its declaration line." Memory: `kym-multibudget`.
[^5]: `docs/logos-dev-notes.md` §"Running headless" ("the module glue silently drops a method that has too many arguments — a 5-string-param `editTxn` never fired; collapsing to `editTxn(txnId, patchJson)` fixed it; keep public methods ≤4 args") + `kym_core/src/kym_core_impl.h:61` (`editTxn(std::string txnId, std::string patchJson)`). Memory: `logoscore-cli-arg-mangling`.
[^6]: `docs/logos-dev-notes.md` §"Codegen quirks" — em-dash/ellipsis in metadata `description` breaks the JSON stamper (STILL applies). The companion claim — "a non-ASCII char in an impl header *comment* stops the interface generator (methods after it vanish)" — was observed on the **0.2.0-era** builder and is **NOT reproducible at `afe4430e`**: `logos-cpp-sdk/cpp-generator/experimental/impl_header_parser.cpp` reads the header via `QString::fromUtf8` and strips comments with `stripCommentsFrom()` (full Unicode, no byte-wise ASCII assumption) — so header comments may contain non-ASCII. Verified by inspection in the Proteus `vpavlin/scala` review (2026-09-16): `src/scala_impl.h` has ~20 non-ASCII lines (em-dashes, box-drawing) and builds. Scope the caution to the builder rev. Memory: `kym-desktop-view-fix`.
[^7]: `docs/logos-dev-notes.md` §§"THE ui_qml-view-won't-open root cause" (failing-combo table: ui_qml + C++ backend + custom-core dep = the one cell that doesn't open; fix = pure QML calling `logos.callModule`), "The ACTUAL blocker was the manifest `main` field" (`view` field used by 0.2.0, ignored by 1.0.0 which reads `manifest.main.<variant>`; pinned builder emits `main:{}` and rejects a metadata `main`; workaround = build with `view` then post-patch the manifest; "shipped sample is ground truth"). Newer builder resolving `view` directly confirmed in the app source: `<builder>/app/mainwindow.cpp:133-150` (ui_qml contract: `view` required = QML entry, `main` optional backend lib) and `mkLogosQmlModule.nix:48` (asserts a `view` field). `~/vpavlin-home/regen.sh:27-30` (stock `view` build VERIFIED against a real 0.2.0; the `main` patch is a 1.0.0-only workaround). Memories: `logos-basecamp-version-matters`, `kym-desktop-view-fix`.
[^8]: `module/Main.qml:230-246` (`Connections { onModuleEventReceived }` + `logos.onModuleEvent("kym_core","budgetChanged"/"statusChanged")`), `:242` (2.5 s poll Timer), and mutations rendering from the call's own return. `docs/logos-dev-notes.md` §"Events are NOT delivered to QML in basecamp 1.0.0 — poll instead".
[^9]: `module/Main.qml:168-181` — `eventCountOf` guard: an empty-state poll must not blank a populated view (`if (eventCountOf(b)===0 && eventCountOf(root.budgetJson)>0) return;`). `docs/logos-dev-notes.md` §"Basecamp may run a dependency module as MULTIPLE instances". Memory: `kym-desktop-view-fix`.
[^10]: Memory `kym-view-core-version-skew`; confirmed in `module/Main.qml:209` (`readonly property bool hasMonthNav: budget.viewMonth !== undefined` gate) + `:220` ("Update kym_core to 0.5.0 for month navigation" fallback toast instead of the raw host error).
[^11]: Memory `basecamp-qr-core-unreachable`; confirmed in `kym_core/src/kym_core_impl.h:88-94` (`pairingQr()` returns a matrix; notes the `qr` core is Qt-free so `logos.callModule` returns null, and it vendors the same MIT encoder) + vendored `kym_core/src/qrcodegen.{hpp,cpp}` (in `CMakeLists.txt` SOURCES) + `module/Main.qml:616` (draw on a `Canvas`).
[^12]: Memory `kym-brand-logo`; icon bundling confirmed in `<builder>/lib/mkLogosQmlModule.nix:83-89` (`iconFiles = src + "/${config.icon}"`; `install -D -m644 ${icon} $out/lib/${config.icon}`) + runtime load in `<builder>/app/main.cpp:152-161` (`setWindowIcon` from metadata `icon` relative to the metadata dir) + `module/metadata.json` (`"icon":"icon.png"` sibling to `"view"`). Git-tracked-files trap: `docs/logos-dev-notes.md` §"logos-module-builder" (git-add new files so nix sees them).
[^13]: `module/flake.nix` (`mkLogosQmlModule { src=./.; configFile=./metadata.json; flakeInputs=inputs; }`; `kym_core.url="path:../kym_core"` with `logos-module-builder.follows` and `delivery_module.follows`) and `kym_core/flake.nix` (`mkLogosModule`, same pinned builder rev + delivery follows). Builder lib: `<builder>/lib/mkLogosQmlModule.nix`, `mkLogosModule.nix`.
[^14]: Memory `logos-lgx-hash-repack` — `.lgx` = gzip'd tar, `manifest.json` + `variants/<variant>/…`; merkle leaf/parent/root formula (self-verified in-session to reproduce real 0.2.1 hashes). See `publishing-and-repack.md` (next to this file).
[^ds]: The design system `logos-co/logos-design-system` (`Logos.Theme` tokens + `Logos.Controls` components), bundled by the Basecamp host (a transitive dep in the module `flake.lock`, so no explicit input needed). Consuming-view reference: `perun/module/src/qml/Main.qml` (`import Logos.Theme` / `import Logos.Controls`; `LogosText`, `Theme.palette.*`, `Theme.spacing.*`). KYM's `module/Main.qml`, by contrast, hand-rolls `QtQuick.Controls` — the anti-pattern this section exists to prevent.
[^15]: `~/vpavlin-home/regen.sh` (portable build; comment: `-dev` shows "NOT AVAILABLE", lgpm wants `-dev` but is a different code path; `logos-repo.json` schemaVersion 1 + `indexUrl`; index schema 2 with per-version `size`/`sha256`(of file)/`rootHash`(=manifest.hashes.root)/embedded `manifest`; systemd --user repo service serving live) + `scripts/gen-lan-repo.sh` (same index generator; https-only, URL points at `logos-repo.json` not `index.json`) + `scripts/serve-lan.sh:20-31` (leaf cert: `basicConstraints=critical,CA:FALSE` + `extendedKeyUsage=serverAuth`, because `openssl req -x509` defaults to CA:TRUE and the host rejects a CA cert for TLS). Memories: `logos-repo-publishing` (portable vs -dev, verify with lgpd vs an official package, bump version), `kym-lan-repo-publishing`.
[^16]: `docs/logos-dev-notes.md` §"Running headless: logoscore" — `-c 'mod.method(a,b)'` mangles args three ways (numbers→`int` so a QString slot silently no-ops; double-quotes stripped; split on every comma); dispatch log says "Method call successful" while no event lands; test by building the JSON in C++ then deleting the self-test method; a non-numeric text arg does marshal as QString. Memory: `logoscore-cli-arg-mangling`.
[^17]: `hub/kym-hub.sh` (`logoscore -D -m <dir>` daemon then `load-module kym_core`; the manifest dep pulls `delivery_module`; `KYM_HUB=1` arms the self-drive tick) + `hub/kym-hub.service` (systemd --user, `Restart=always`). `docs/logos-dev-notes.md` §"Running headless: logoscore" (daemon keeps `capability_module` alive so returns come back; modules must be portable). Memory `kym-headless-hub` (self-drive must be a QTimer on the event-loop thread, not a std::thread — createNode hangs otherwise).
[^18]: Memory `kym-render-harness`; confirmed `scripts/qml-harness/render.sh:28-36` (offscreen `QT_QPA_PLATFORM=offscreen` + software `QT_QUICK_BACKEND=software`, loads `$ROOT/module/Main.qml` — the source-comment `module/qml/Main.qml` is stale, the actual arg is correct) + `harness.cpp` (mock `logos` backend, `budgetJson` property injected). The derived-property lag → `Qt.callLater` is from the memory note.
[^19]: `docs/logos-dev-notes.md` §"Delivery module API" (std vs Qt caller signatures; `LogosMap = nlohmann::json`; base64-in-JSON payload; createNode named preset). Send-payload byte-array-vs-string SIGABRT and the probe-and-cache fix: `kym_core/src/kym_core_impl.h:188-195` (`deliverySend` doc + `m_sendRepr` cache). Memory `kym-hub-runner` (gotcha 3). entryNodes-in-config caveat reconciled with §"Delivery module API" via `hub/kym-hub.sh` (see [^22]).
[^20]: `module/Main.qml:156` (`for (var k=0;k<2 && typeof res==="string";k++) res=JSON.parse(res)` unwrap loop on a `pairingQr()` bridge return). Memory: `basecamp-qr-core-unreachable` (double-encoded returns).
[^21]: Memory `kym-headless-hub` — root-caused with a minimal 2-module reproducer (`github.com/vpavlin/logoscore-event-repro`): under logoscore, the delivery module emits `messageReceived` from its Nim FFI callback thread unmarshaled; Qt Remote Objects serializes cross-module events and silently drops (and can tear down the connection for) a signal emitted off the source thread → the hub connects and SENDS but `rxSeen` stays 0. Upstream fix = logos-cpp-sdk `d77c3dd` (PR #68, "marshal provider events onto the source thread", merged 2026-05-25); confirmed locally under the new daemon-CLI logoscore (a std::thread emit now DELIVERED where the old SDK gave 0). GUI Basecamp unaffected (runs modules in-proc / marshals the emit). Also the QTimer-not-std::thread rule for `createNode`.
[^22]: `hub/kym-hub.sh` (comment + config: a bare `{"mode":"Core","preset":"logos.dev"}` gives ZERO bootstrap nodes → "No peers for topic"/"NoPeersToPublish"; fix pins `entryNodes` = the logos.dev fleet the mobile app dials; the core merges `KYM_DELIVERY_CFG` (env JSON) over its default, no rebuild) + `hub/kym-hub.service`. Memory `kym-hub-runner`.
[^23]: qaku desktop signing — `qaku_core/src/qaku_identity.hpp` (OpenSSL `EC_KEY` secp256k1: `identityFromPriv`/`generateIdentity`, address = last-20-bytes of `sha256(compressed_pub_33B)`, `cjson` sorted-key compact JSON, `canonicalMessage`, `ecdsaSignLowS`/`ecdsaVerify` compact r‖s, `signEvent`/`verifyEvent`) with the JS reference in `packages/contract/src/identity.mjs`; proven C++↔JS byte-parity bidirectionally. Must be added to the module CMakeLists SOURCES + git-tracked (Nix build trap). Provenance: memory `qaku-desktop-signing`.
[^async]: Rule set 2026-09-30 after a hung view: every Basecamp view in the ecosystem (scala `CalendarView.qml` `callVia`/`coreAsync`, qaku `module/Main.qml`, kym `module/Main.qml` `callAsync`, kith `kith-ui/qml/Main.qml`, perun `Main.qml` + `QrCard.qml`, loam `ui/Main.qml` `callCore`) was converted to `logos.callModuleAsync` with single-flight polls (`refreshBusy`/`refreshAgain`). The QML render harnesses gained a `callModuleAsync` mock so the async path is what gets tested. Memory: `no-blocking-ipc-in-qml`.
[^quoted]: Scala 0.9.41 / view 0.8.35: desktop attachment transfers "stuck forever" because the view passed the JSON-quoted content reference back to `attachmentStatus`; the core matched nothing. Fixed by unquoting in the view and defensively in the core.
[^plain]: Verified with Qt 6.10 on 2026-09-30: an AutoText `Text` whose string contains `<img src="http://…">` issues the HTTP request when shown. All five apps' views were converted with `qml-plaintext.py` (scala 0.8.34, qaku 0.1.28, kym 0.6.10, kith 0.2.5, perun 0.8.2).
[^hubver]: Scala VPS hub, 2026-09-30: on delivery_module 0.1.3 it could not reassemble the segmented catch-up from 0.1.4 clients, so a newly added calendar stayed empty; upgrading the hub to 0.1.4 fixed it. Memory: `scala-vps-hub`.
[^storhub]: Scala 0.9.36–0.9.38: hub Storage root with `autonat-server` + `relay-server`, cache-on-see with retries (`cacheAttachments`/`retryCacheFetches`), and the nim-libp2p patch `0002-kad-getproviders-include-local-records.patch` (vpavlin/scala `mobile/native/logosstorage/patches/nim-libp2p/`). Proven desktop→hub→desktop byte-identical across NATs. Memory: `storage-nat-hub-cache`.
[^p03]: Basecamp 0.3 port analysis, 2026-10-02 (`loam-basecamp` branch `port/0.3`, `docs/port-0.3/analysis-basecamp-builder.md` + `analysis-delivery.md` + `TESTING.md`), source-read against builder 0.3.1 (`16e2f6b`), logos-protocol 0.9.0, cpp-sdk `3f34c0b`, delivery_module v0.3.0 (`bec8594`, lib `ca28145`), and exercised with `logosctl` 0.3.1 two-node tests of every app. 256² icon: logos-package `package.cpp` (manifest ≥ 0.4.0). onContextReady rejection: seen on scala and official modules; all ported cores defer startup calls by ~1 s (`scala_impl.cpp` `startModules`). Default-arg drop: `impl_header_parser.cpp:893-905` (scala `createCalendar`/`handleShareLink` lost `identityId`). `AsyncResult` timeouts: `scala_impl.cpp` `downloadToUrlAsyncResult(…, 60000)`. Cross-repo upgrade incident 2026-10-02 (Basecamp replaced the 0.1.4 fork with upstream 0.3.x; nothing synced). Memories: `port-0-3`, `delivery-upstream-vs-fork`, `scala-gui-delivery-flat-config`, `loam-node-ports`.
[^fields]: Swamp 0.5.3 `module/Main.qml` `component Field` (`saved`/`edited`), reported by vpavlin in the first GUI test; `scripts/qml-harness/harness.cpp` types "Bog Witch" into the profile field and refreshes 3x - the old view fails (the field holds the saved name again), the new one passes.
