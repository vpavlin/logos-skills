---
name: logos-basecamp-module
description: "Use when building or shipping a Logos Basecamp app/module — a desktop QML view plus a shared engine/sync core, an always-on headless hub, building the .lgx with logos-module-builder, publishing to a Basecamp package repo, or debugging \"view won't open\" / \"Invalid response\" / dropped-method / hash-mismatch / hub-receives-nothing failures. Keywords: Basecamp, ui_qml, core module, mkLogosQmlModule, mkLogosModule, logoscore, logos.callModule, .lgx, metadata.json, universal authoring, headless hub, Qt Remote Objects."
---

Build a Logos Basecamp app as **two packages plus one engine**: a Qt-free **core module** that owns everything (engine, crypto, wire, sync, domain logic) and a **thin QML view** that only calls the core and renders its JSON. The same core binary runs behind the desktop view *and* standalone under `logoscore` as an always-on **headless hub** — one implementation, no drift between UI and server.[^1] This playbook is the how-to plus the silent failure modes that cost hours.

## Mental model

```
              ┌──────────────── your app ────────────────────┐
  desktop  →  thin ui_qml view (pure QML, no C++)            │  logos.callModule("<core>", action, [args])
              │        renders <core>'s JSON                  │  ← one engine, two front ends
  hub      →  logoscore daemon ── loads ── <core> module ────┘  headless, always-on peer
```

Rules that follow from this:
- **All logic lives in the core.** The view has no engine copy, no crypto, no wire code. It calls actions and draws the returned JSON. If you find yourself duplicating a calculation in QML, it belongs in the core.[^1]
- **The core is `type:"core"`, `interface:"universal"`, Qt-free.** You write only `src/<name>_impl.{h,cpp}`; the class is `<NameCamelCase>Impl : public LogosModuleContext`. Public methods = the dispatch API (return JSON-serializable `std::string`), a `logos_events:` section = emittable events, `onContextReady()` = setup, `modules().<dep>` = typed callers for declared `dependencies`. No `Q_OBJECT`, no `.rep`. The plugin glue is generated from the header.[^2]
- **The view is `type:"ui_qml"` with NO C++ backend** — pure QML like the shipped `counter_qml` sample. A ui_qml plugin *with* a C++ backend that depends on a *custom* core module is exactly the combination that silently fails to open on some Basecamp builds; dropping the backend removes it and is truer to thin-UI anyway.[^7]

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

**Pin SDK inputs by their FULL 40-char commit, never a branch or short rev.** A `github:logos-co/<repo>/<branch>` or short-rev url resolves through GitHub's API, which **422s the moment that branch is renamed or deleted** — and the SDK's feature branches are volatile — so a from-scratch `nix flake` eval breaks with no code change. In every `flake.nix` input, pin `github:logos-co/<repo>/<full-40-char-sha>`, and keep the three inputs (`logos-module-builder`, `delivery_module`, your core) on **one** builder rev via `follows`. Derive the current-good SHAs from a **known-good `flake.lock`** (`nix flake metadata`), not from memory. A working triple observed in practice: `logos-module-builder afe4430ee6eb7ba45c08a516a43e18500720c715`, `delivery_module 0fb3a7427b29c98ab0fa2465bcd1e90cbfdf50a3` — treat these as a starting point to verify, not gospel; the 0.2.0-era desktop builder is a different rev (`021013458d87…`), so match the rev to your target Basecamp.[^13]

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

**Never make a *blocking* `callModule` during load.** `logos.callModule(...)` is a **synchronous IPC** (20 s timeout). Calling it during `Component.onCompleted`, or reactively from a binding/`on<Prop>Changed` that first fires when the initial `snapshot()` populates state, runs a nested blocking call *inside view construction* and **wedges the QML render — the view "does not load"** (it compiled fine; it's frozen, not broken). Poll `snapshot()` on a `Timer` (that fires after the first render) and **defer any load-triggered call with `Qt.callLater(fn)`** so the view paints first. (Real case: a reactive `onSecretChanged: buildQr()` — where `buildQr` did a synchronous `callModule("shareQr")` — froze the whole view until it was changed to `Qt.callLater(root.buildQr)`.)

View skeleton that works:
```qml
function callCore(m, a) {
  if (typeof logos === "undefined" || !logos.callModule) return "";
  return String(logos.callModule("<app>_core", m, a || []));
}
// Bridge may return raw JSON or a quoted/escaped JSON string — accept both:
function asState(raw){ var s=String(raw||"").trim();
  if(s.charAt(0)==='"'){ try{s=String(JSON.parse(s)).trim()}catch(e){return null} }
  if(s.charAt(0)!=="{") return null; var o; try{o=JSON.parse(s)}catch(e){return null}
  return (o && o.error===undefined) ? s : null; }
function refresh(){ var b=asState(callCore("snapshot",[])); if(b) root.stateJson=b; }
Timer { interval: 2500; running: true; repeat: true; onTriggered: root.refresh() }
Component.onCompleted: { logos.onModuleEvent && logos.onModuleEvent("<app>_core","stateChanged"); refresh(); }
```
On a **mutation**, have the core return the fresh state JSON, so the view renders straight from the instance that applied the edit (don't wait for the next poll).[^8]

## Style the view with the Logos design system (do NOT hand-roll QtQuick)

A `ui_qml` view must use the official **`logos-design-system`**, not bespoke `QtQuick.Controls`. The Basecamp host **bundles** it (it's a transitive dep in the module `flake.lock`), so it resolves at runtime with **no extra flake input** — just import it:[^15]

```qml
import QtQuick
import Logos.Theme      // design tokens (singleton `Theme`)
import Logos.Controls   // themed components (Logos*)
```

- **Components** (use these, not raw QtQuick): `LogosText`, `LogosButton` / `LogosIconButton`, `LogosTextField`, **`LogosCopyableText`** (a selectable/copyable value — ideal for a pairing code / shareable secret / address), `LogosComboBox`, `LogosSearchBar`, `LogosTable` + `LogosTableColumn`, `LogosTabBar` + `LogosTabButton`, `LogosCheckbox`, `LogosDialog`.
- **Tokens** (never hardcode a color/spacing/radius/font): `Theme.palette.*` (`text`, `textTertiary`, `background`, `surface`, `surfaceRaised`, `primary`, `success`, `warning`, `border`, `borderHairline`, …), `Theme.spacing.*` (`tiny:4` … `xxlarge:40`, `radiusSmall` … `radiusPill`), `Theme.typography.*`.
- **Reference + catalog:** copy the consumption pattern from a real consuming view;[^15] browse the components + tokens with the design-system storybook (`nix run` in `logos-co/logos-design-system`).

A hand-rolled `QtQuick.Controls` view is the classic "why does this look off / terrible" smell — using the design system is what makes a view look like part of Logos instead of bespoke.

**Mind the host's bundled version (a load-failure trap).** The available components *and their properties* are gated by the design-system version the **target Basecamp bundles**, not the latest repo. On an older host (e.g. 0.2.0), a newer **type** is `"<X> is not a type"` and a newer **property** is `"Cannot assign to non-existent property <p>"` — **both stop the whole view from loading** (`Failed to compile ui_qml view … Failed to load UI module`), whereas a missing `Theme.*` **token** just evaluates to `undefined` and degrades to a default (ugly, not fatal). So: verify every `Logos*` type/property against the host you actually target. The proven-safe baseline for Basecamp 0.2.0 is what a known-good shipped view uses — **`LogosText` + `LogosButton` with basic props** (as in the Perun reference[^15]). For anything newer (`LogosTextField`, `LogosCopyableText`, `LogosButton.variant`/`Variant`, etc.), either confirm it exists in the host's bundle or fall back to a plain `QtQuick.Controls` control styled with `Theme.*` tokens (define one `component AppField: TextField { color: Theme.palette.text; background: Rectangle { color: Theme.palette.surface; … } }` and reuse it). A read-only selectable `TextField` + a `LogosButton` "Copy" (backed by an off-screen `TextEdit { }`.`copy()`) replaces `LogosCopyableText` safely.

## Builder + glue quirks (symptom → cause → fix)

| Symptom | Root cause | Fix |
|---|---|---|
| A core method is uncallable from the view / `"Invalid response"` | Generated dep-caller surfaces only **action-style** methods; zero-arg getters are dropped[^3] | Expose reads as an action (`snapshot()`) + a `logos_events:` event; never a bare getter |
| One method silently vanishes; others fine | The glue **drops any method with a trailing `//` comment** on its declaration line[^4] | Move the comment to the line *above* the declaration; keep decls comment-free |
| A method "succeeds" but never fires | The glue **drops/no-ops methods with >4 args**[^5] | Keep public methods ≤4 args; pass structured data as **one JSON string** (`editTxn(id, patchJson)` not 5 positional params) |
| Methods after some point in the header all disappear | **Non-ASCII** char in a header comment stops the interface generator; an em-dash/ellipsis in metadata `description` breaks the JSON stamper[^6] | Headers + metadata **ASCII-only** |
| `int.dump()` compile error | Universal dispatcher `.dump()`s the return | Return `std::string`, never `int`/`bool` |
| View's deps load but no tab/view ever opens | ui_qml + C++ backend + custom-core dep is the failing combo; also entry-point field mismatch by Basecamp version[^7] | Make the view **pure QML** (no backend); put `Main.qml` at root; match the Basecamp version's entry field (see below) |
| Cross-module `std::string` return arrives double-wrapped | Returns can come back **double-encoded** through the bridge[^11][^20] | Unwrap up to twice: `for(i=0;i<2&&typeof r==="string";i++) r=JSON.parse(r)` |

## Basecamp version differences (establish the target FIRST)

Behavior differs sharply by Basecamp version — pick the target version before designing or debugging, and test the artifact you actually ship on it.[^7]

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
- **Icon:** put `"icon":"icon.png"` in the view's metadata (flat path, sibling to `"view"`). The builder bundles it into the variant (`install -D -m644 ${icon} $out/lib/${icon}`, relocated under `variants/<platform>/`); the host loads it at runtime relative to the manifest dir (`setWindowIcon` from the metadata `icon` field). 512² PNG. `git add module/icon.png` or the build fails.[^12]

## Publishing to a Basecamp repo

- **Repos use the PORTABLE variant (`linux-amd64`), never `-dev`.** A `-dev` `.lgx` shows as **"NOT AVAILABLE"** in Basecamp's package manager (silent, no error). `lgpm install --file` (a *local* installer) demands `-dev` — a different code path; don't decide what to publish from lgpm's demand.[^15]
- A Basecamp repo is static files over **HTTPS**: `logos-repo.json` (identity card, schemaVersion 1, `indexUrl:https://HOST:PORT/.../index.json`) + `index.json` (schema 2, one entry per package, newest-version-first). Each version entry carries `url`, `size`, `sha256` (of the `.lgx` file), `rootHash` (= the manifest's merkle root), and the embedded `manifest`.[^15]
- **Regenerate `index.json` whenever any `.lgx` changes** — it embeds size/sha256/rootHash; a stale index means Basecamp downloads bytes that don't match the hash and **silently refuses to install**. **Bump the version on every republish** — the GUI caches the index and won't re-notice a same-version change.[^15]
- Host it as a `systemd --user` service with `Restart=always` + `loginctl enable-linger`; it serves files live (no restart after republish). TLS needs a **leaf** cert (`CA:FALSE`+`serverAuth`) — `openssl req -x509` defaults to `CA:TRUE` on OpenSSL 3 and the host rejects a CA cert used to terminate TLS. Verify the repo answer with **`lgpd`** (logos-package-downloader — the catalog tool the repo view uses), NOT lgpm: `repo add <…/logos-repo.json>` → `repo refresh` → `--json info <pkg>`, and compare your `variants` to an official package that IS available (e.g. `chat_ui`). A one-command `regen.sh` that builds both `.lgx-portable`, installs them, regenerates the index, and rewrites the identity card is the reliable shipping path.[^15]

For hot-swapping a single `.so` into a signed `.lgx` **without a full rebuild** (the merkle-hash repack), and the full publishing walkthrough, see `reference/publishing-and-repack.md`.
[^15]: The design system `logos-co/logos-design-system` (`Logos.Theme` tokens + `Logos.Controls` components), bundled by the Basecamp host (a transitive dep in the module `flake.lock`, so no explicit input needed). Consuming-view reference: `perun/module/src/qml/Main.qml` (`import Logos.Theme` / `import Logos.Controls`; `LogosText`, `Theme.palette.*`, `Theme.spacing.*`). KYM's `module/Main.qml`, by contrast, hand-rolls `QtQuick.Controls` — the anti-pattern this section exists to prevent.

## Running the core as a headless hub — the gotchas

The **same** core `.lgx` runs standalone as an always-on peer: daemon form `logoscore -D -m <modulesDir>` (background) then `logoscore load-module <core>` (its manifest pulls the delivery dep). Stage the bundled `capability_module` into the modules dir too, use PORTABLE `.lgx`, and run it as a `systemd --user` service with `Restart=always`.[^17] Three things silently break a headless hub — none surface as an error:

**Gotcha 0 — nothing drives it.** A GUI stays synced because its view polls `snapshot()` on a Timer, which lazily starts delivery and runs the periodic reconcile. A headless node has no such poll. Arm a self-drive tick from an env flag checked in `onContextReady()`; without it delivery never bootstraps and nothing syncs. It MUST be a **QTimer on the Qt event-loop thread**, never a `std::thread` — the delivery module's async calls (`createNodeAsync`/`send`) only dispatch their callbacks on the host event-loop thread, so a worker-thread driver leaves the callback undispatched and `createNode` hangs.[^17][^21]

**Gotcha 1 — it connects and SENDS but RECEIVES nothing (the important one).** The node meshes and publishes, `send` works, yet the receive counter stays 0. Cause: the delivery module emits its `messageReceived` signal **directly from its FFI/worker thread, unmarshaled**. Under `logoscore`, cross-module events replicate via **Qt Remote Objects, which silently DROPS a signal emitted off the object's Qt event-loop thread** (and worse, can tear down the QRO connection so later method calls stop too). Your subscribing core is blameless — its handler fires perfectly when the emitter behaves; it works under the GUI Basecamp only because that host runs modules in-proc / marshals the emit. **The fix is upstream, not in your module:** logos-cpp-sdk commit **`d77c3dd`** (PR #68, "marshal provider events onto the source thread"). Run the hub under a `logoscore` built on a cpp-sdk at or past that commit (the newer daemon-CLI `logoscore` embeds it). No code change to your core. Reproduce/confirm with a 2-module emitter→consumer rig: emit from a QTimer → events arrive; emit from a `std::thread` → 0, silently dropped.[^21]

**Gotcha 2 — it drifts off the fleet: "No peers for topic".** Starting the core with a bare delivery config (`{"mode":"Core","preset":"..."}`) gives the node **zero bootstrap peers** ("creating kademlia discovery as seed node (no bootstrap nodes)"); it relies purely on discovery, loses the fleet, and logs `No peers for topic` / `NoPeersToPublish` — nothing syncs. Fix: put **`entryNodes`** (the same fleet peers your mobile/desktop clients dial) in the delivery config, and have the core **merge an env-provided config JSON over its default** so you can pin the fleet without a rebuild.[^22]

Delivery/wire notes: a core module gets the **std** delivery caller (`createNode(std::string cfg)`, `subscribe`, `send(topic, LogosMap)`, `onMessageReceived(hash,topic,payload,ts)`); a ui_qml backend would get the Qt caller instead. Payloads ride as **base64 inside JSON** so binary crosses the FFI as a JSON string. `createNode`'s config is a named preset (`logos.dev`/`logos.test`/`twn`) plus, on builds that accept it, pinned `entryNodes` (gotcha 2); no self-hosted-nwaku or Store query is exposed, so backfill is republish-on-demand. One more silent trap: **newer delivery builds marshal the `send` payload as a JSON byte ARRAY and throw `type must be array, but is string` on a string** (→ the module aborts, signal 6). Probe once (array→string), cache the shape the local delivery accepts, and reuse it — so the same binary works on an old-SDK GUI host (string) and a new-SDK hub (array) with no env flag.[^19]

## Testing pitfalls

- **`logoscore -c 'mod.method(a,b)'` silently mangles args:** numbers are typed `int` (a `QString` slot then no-ops and returns current state with **no error**), double-quotes are **stripped** (breaks JSON args), and args **split on every comma** (shreds a JSON object). You cannot pass a number or a JSON string through `-c`. To test a method taking numbers/JSON, build the payload **in C++** (a temporary self-test method using your real serializer) and call the target directly; verify, then delete it. A non-numeric text arg *does* marshal as `QString` — use that to confirm the body even runs.[^16]
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
[^6]: `docs/logos-dev-notes.md` §"Codegen quirks" — em-dash/ellipsis in metadata `description` breaks the JSON stamper; a non-ASCII char in an impl header comment stops the interface generator (methods after it vanish). Memory: `kym-desktop-view-fix`.
[^7]: `docs/logos-dev-notes.md` §§"THE ui_qml-view-won't-open root cause" (failing-combo table: ui_qml + C++ backend + custom-core dep = the one cell that doesn't open; fix = pure QML calling `logos.callModule`), "The ACTUAL blocker was the manifest `main` field" (`view` field used by 0.2.0, ignored by 1.0.0 which reads `manifest.main.<variant>`; pinned builder emits `main:{}` and rejects a metadata `main`; workaround = build with `view` then post-patch the manifest; "shipped sample is ground truth"). Newer builder resolving `view` directly confirmed in the app source: `<builder>/app/mainwindow.cpp:133-150` (ui_qml contract: `view` required = QML entry, `main` optional backend lib) and `mkLogosQmlModule.nix:48` (asserts a `view` field). `~/vpavlin-home/regen.sh:27-30` (stock `view` build VERIFIED against a real 0.2.0; the `main` patch is a 1.0.0-only workaround). Memories: `logos-basecamp-version-matters`, `kym-desktop-view-fix`.
[^8]: `module/Main.qml:230-246` (`Connections { onModuleEventReceived }` + `logos.onModuleEvent("kym_core","budgetChanged"/"statusChanged")`), `:242` (2.5 s poll Timer), and mutations rendering from the call's own return. `docs/logos-dev-notes.md` §"Events are NOT delivered to QML in basecamp 1.0.0 — poll instead".
[^9]: `module/Main.qml:168-181` — `eventCountOf` guard: an empty-state poll must not blank a populated view (`if (eventCountOf(b)===0 && eventCountOf(root.budgetJson)>0) return;`). `docs/logos-dev-notes.md` §"Basecamp may run a dependency module as MULTIPLE instances". Memory: `kym-desktop-view-fix`.
[^10]: Memory `kym-view-core-version-skew`; confirmed in `module/Main.qml:209` (`readonly property bool hasMonthNav: budget.viewMonth !== undefined` gate) + `:220` ("Update kym_core to 0.5.0 for month navigation" fallback toast instead of the raw host error).
[^11]: Memory `basecamp-qr-core-unreachable`; confirmed in `kym_core/src/kym_core_impl.h:88-94` (`pairingQr()` returns a matrix; notes the `qr` core is Qt-free so `logos.callModule` returns null, and it vendors the same MIT encoder) + vendored `kym_core/src/qrcodegen.{hpp,cpp}` (in `CMakeLists.txt` SOURCES) + `module/Main.qml:616` (draw on a `Canvas`).
[^12]: Memory `kym-brand-logo`; icon bundling confirmed in `<builder>/lib/mkLogosQmlModule.nix:83-89` (`iconFiles = src + "/${config.icon}"`; `install -D -m644 ${icon} $out/lib/${config.icon}`) + runtime load in `<builder>/app/main.cpp:152-161` (`setWindowIcon` from metadata `icon` relative to the metadata dir) + `module/metadata.json` (`"icon":"icon.png"` sibling to `"view"`). Git-tracked-files trap: `docs/logos-dev-notes.md` §"logos-module-builder" (git-add new files so nix sees them).
[^13]: `module/flake.nix` (`mkLogosQmlModule { src=./.; configFile=./metadata.json; flakeInputs=inputs; }`; `kym_core.url="path:../kym_core"` with `logos-module-builder.follows` and `delivery_module.follows`) and `kym_core/flake.nix` (`mkLogosModule`, same pinned builder rev + delivery follows). Builder lib: `<builder>/lib/mkLogosQmlModule.nix`, `mkLogosModule.nix`.
[^14]: Memory `logos-lgx-hash-repack` — `.lgx` = gzip'd tar, `manifest.json` + `variants/<variant>/…`; merkle leaf/parent/root formula (self-verified in-session to reproduce real 0.2.1 hashes). See `reference/publishing-and-repack.md`.
[^15]: The design system `logos-co/logos-design-system` (`Logos.Theme` tokens + `Logos.Controls` components), bundled by the Basecamp host (a transitive dep in the module `flake.lock`, so no explicit input needed). Consuming-view reference: `perun/module/src/qml/Main.qml` (`import Logos.Theme` / `import Logos.Controls`; `LogosText`, `Theme.palette.*`, `Theme.spacing.*`). KYM's `module/Main.qml`, by contrast, hand-rolls `QtQuick.Controls` — the anti-pattern this section exists to prevent.
[^15]: `~/vpavlin-home/regen.sh` (portable build; comment: `-dev` shows "NOT AVAILABLE", lgpm wants `-dev` but is a different code path; `logos-repo.json` schemaVersion 1 + `indexUrl`; index schema 2 with per-version `size`/`sha256`(of file)/`rootHash`(=manifest.hashes.root)/embedded `manifest`; systemd --user repo service serving live) + `scripts/gen-lan-repo.sh` (same index generator; https-only, URL points at `logos-repo.json` not `index.json`) + `scripts/serve-lan.sh:20-31` (leaf cert: `basicConstraints=critical,CA:FALSE` + `extendedKeyUsage=serverAuth`, because `openssl req -x509` defaults to CA:TRUE and the host rejects a CA cert for TLS). Memories: `logos-repo-publishing` (portable vs -dev, verify with lgpd vs an official package, bump version), `kym-lan-repo-publishing`.
[^16]: `docs/logos-dev-notes.md` §"Running headless: logoscore" — `-c 'mod.method(a,b)'` mangles args three ways (numbers→`int` so a QString slot silently no-ops; double-quotes stripped; split on every comma); dispatch log says "Method call successful" while no event lands; test by building the JSON in C++ then deleting the self-test method; a non-numeric text arg does marshal as QString. Memory: `logoscore-cli-arg-mangling`.
[^17]: `hub/kym-hub.sh` (`logoscore -D -m <dir>` daemon then `load-module kym_core`; the manifest dep pulls `delivery_module`; `KYM_HUB=1` arms the self-drive tick) + `hub/kym-hub.service` (systemd --user, `Restart=always`). `docs/logos-dev-notes.md` §"Running headless: logoscore" (daemon keeps `capability_module` alive so returns come back; modules must be portable). Memory `kym-headless-hub` (self-drive must be a QTimer on the event-loop thread, not a std::thread — createNode hangs otherwise).
[^18]: Memory `kym-render-harness`; confirmed `scripts/qml-harness/render.sh:28-36` (offscreen `QT_QPA_PLATFORM=offscreen` + software `QT_QUICK_BACKEND=software`, loads `$ROOT/module/Main.qml` — the source-comment `module/qml/Main.qml` is stale, the actual arg is correct) + `harness.cpp` (mock `logos` backend, `budgetJson` property injected). The derived-property lag → `Qt.callLater` is from the memory note.
[^19]: `docs/logos-dev-notes.md` §"Delivery module API" (std vs Qt caller signatures; `LogosMap = nlohmann::json`; base64-in-JSON payload; createNode named preset). Send-payload byte-array-vs-string SIGABRT and the probe-and-cache fix: `kym_core/src/kym_core_impl.h:188-195` (`deliverySend` doc + `m_sendRepr` cache). Memory `kym-hub-runner` (gotcha 3). entryNodes-in-config caveat reconciled with §"Delivery module API" via `hub/kym-hub.sh` (see [^22]).
[^20]: `module/Main.qml:156` (`for (var k=0;k<2 && typeof res==="string";k++) res=JSON.parse(res)` unwrap loop on a `pairingQr()` bridge return). Memory: `basecamp-qr-core-unreachable` (double-encoded returns).
[^21]: Memory `kym-headless-hub` — root-caused with a minimal 2-module reproducer (`github.com/vpavlin/logoscore-event-repro`): under logoscore, the delivery module emits `messageReceived` from its Nim FFI callback thread unmarshaled; Qt Remote Objects serializes cross-module events and silently drops (and can tear down the connection for) a signal emitted off the source thread → the hub connects and SENDS but `rxSeen` stays 0. Upstream fix = logos-cpp-sdk `d77c3dd` (PR #68, "marshal provider events onto the source thread", merged 2026-05-25); confirmed locally under the new daemon-CLI logoscore (a std::thread emit now DELIVERED where the old SDK gave 0). GUI Basecamp unaffected (runs modules in-proc / marshals the emit). Also the QTimer-not-std::thread rule for `createNode`.
[^22]: `hub/kym-hub.sh` (comment + config: a bare `{"mode":"Core","preset":"logos.dev"}` gives ZERO bootstrap nodes → "No peers for topic"/"NoPeersToPublish"; fix pins `entryNodes` = the logos.dev fleet the mobile app dials; the core merges `KYM_DELIVERY_CFG` (env JSON) over its default, no rebuild) + `hub/kym-hub.service`. Memory `kym-hub-runner`.
[^23]: qaku desktop signing — `qaku_core/src/qaku_identity.hpp` (OpenSSL `EC_KEY` secp256k1: `identityFromPriv`/`generateIdentity`, address = last-20-bytes of `sha256(compressed_pub_33B)`, `cjson` sorted-key compact JSON, `canonicalMessage`, `ecdsaSignLowS`/`ecdsaVerify` compact r‖s, `signEvent`/`verifyEvent`) with the JS reference in `packages/contract/src/identity.mjs`; proven C++↔JS byte-parity bidirectionally. Must be added to the module CMakeLists SOURCES + git-tracked (Nix build trap). Provenance: memory `qaku-desktop-signing`.