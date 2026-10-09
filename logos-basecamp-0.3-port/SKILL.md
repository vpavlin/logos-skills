---
name: logos-basecamp-0.3-port
description: "Port an existing Basecamp app (core + view + its dependency chain) from Basecamp 0.2.x / logos-module-builder 0.2.x to Basecamp 0.3.x / builder 0.3.1 — and, with it, from a delivery_module fork to upstream delivery v0.3.0 and from storage_module 2.x to 3.0. Use when planning or executing that migration, when a 0.2 module 'loads but never connects' on 0.3, when a user's Basecamp silently auto-upgraded a module to an incompatible version, or when desktops on the new delivery library and phones on the old one stop understanding each other. Covers: why the stack moves as one, the phone-first wire-compat shim (SegmentMessage), the per-module checklist (builder pin, LIDL contracts, 256² icons, onContextReady defer, default args, view dependencies), delivery v0.3.0 and storage 3.0 API changes, RLN-off-by-config, a separate test repo, logosctl two-node tests, and what stays unverified."
---

# Port a Basecamp app to Basecamp 0.3.x

The 0.3 line is not a point release for module authors: the runtime protocol, the builder, the
packaging, the headless tool, and the bundled `delivery_module` and `storage_module` all changed at
once. This is the order of operations that worked for a six-app ecosystem sharing one transport
module, and the traps found on the way. Module-level rules live in `logos-basecamp-module`
(§ 0.3.x); this skill is the migration plan.[^plan]

## Three facts that shape the whole plan

1. **The desktop stack moves together.** Modules built on builder 0.2.x load in the 0.3 runtime,
   but their startup calls to other modules are rejected, so a transport module never starts. Every
   module in a chain (transport, delivery, storage, keycard, each app core, each view) is rebuilt on
   builder 0.3.1 and released together.[^plan]
2. **Phones are the hard constraint.** Delivery library v0.39 (inside `delivery_module` v0.3.0)
   wraps every reliable-channel payload in a `SegmentMessage` protobuf with an unchanged wire
   marker. Old (v0.38.1) and new nodes can't read each other's channel messages, and neither knows
   it. Ship an **unwrap shim on both sides first** — phones before desktops — so mixed versions
   interoperate during the rollout.[^deliv]
3. **Users can be upgraded under you.** Basecamp installs the highest version of a package across
   every repo the user added. A fork published as `delivery_module 0.1.4` is replaced by the
   official 0.3.x the day a user adds the official catalog — and every app stops syncing. Before
   anything else, version the fork above upstream (we bumped it to 0.9.0: same code, with the version embedded in the plugin patched too — the runtime reads that, not just the manifest) and
   publish 0.3 test builds to a **separate** repo.[^incident] Beware the reverse trap: a fork at 0.9.0 now
   outranks upstream 0.3.0, so a 0.3 user who also added a repo carrying the fork gets the OLD fork over the
   delivery their ported apps need. Prefer renaming the fork or pinning dependency ranges (e.g. `^0.3`), and
   remove the bumped fork from shared repos once the apps have moved.

**How the trap shows up** (2026-10-09, Duet and laptop). The app on Basecamp 0.3 sends but never
receives (Swamp's counters: rx 0 / tx 140), while headless nodes on the same network sync fine. The
log is full of `SDS pending-content stash full, dropping oldest entry` on the app's topics: the
fork can't decode the 0.3.0 nodes' channel messages. Check
`<profile>/modules/delivery_module/manifest.json` for the version and
`<profile>/module_data/package_downloader/*/repositories.json` for which repo offered it. The fork
has since been removed from apps.vpavlin.xyz.[^fork09]

## Phase 0 — safe on the current stack

- **Wire shim in the transport, both platforms.** Accept a frame as a `SegmentMessage` only if it
  parses as one: field 1 is a 32-byte hash, field 4 (data-segment count) = 1, field 3 (index) = 0,
  not parity (field 6), and `keccak256(field 7) == field 1`; then the payload is field 7. Otherwise
  treat the bytes as the old raw content. Nesting is `WakuMessage.payload = SDS{f5 = SegmentMessage{f7 = app bytes}}`.
  Unit-test it with real frames captured from both library versions. On a phone using a shared node,
  apply it on the client path too — the service forwards candidate payloads and the app only tries
  what it receives.[^deliv]
- **Decode URL-safe base64.** Both the fork and v0.3.0 emit event payloads as
  `{"_bytes":"<URL-safe base64>"}`; a standard-alphabet decoder truncates at the first `-`/`_`, and
  new-library frames (~25 KB) always contain one.[^p03mem]
- Ship the shim to phones through the normal channel, then check old↔new on the real fleet before
  touching desktops.

## Phase 1 — rebuild every desktop module on builder 0.3.1

Per module (the checklist that caught every real failure):[^builder]

| Item | Why |
|---|---|
| `logos-module-builder.url = "github:logos-co/logos-module-builder/0.3.1"`, same in every module | mixed builders = rejected calls; `lgx merge` also refuses differing contract bytes across platforms |
| Remove `path:` inputs (absolute and `../sibling`) and unpinned inputs; drop stale `follows` | builds must reproduce off the dev box (`logos-multiplatform-modules`) |
| Every dependency publishes a LIDL contract | 0.3.1 refuses a dep without one; port legacy Qt-plugin deps to a universal impl header (`codegen.impl_header`) — `loam-keycard` has the worked example |
| Defer cross-module calls out of `onContextReady()` (~1 s `QTimer::singleShot`) | 0.3 rejects them: "auth token not recognized" |
| Remove default arguments from public methods | silently dropped from the contract; the caller's argument vanishes over IPC |
| No trailing `//` comment on a method declaration; ≤ 4 args | still silently drops the method |
| View icons exactly 256×256 | the 0.3.1 packager blocks other sizes |
| Views: drop `"interface"`; list every module the view calls in `dependencies` | views run under their own identity |
| Views: `callModuleAsync` at startup; pair `onModuleEvent` with a state read | `callModule` gives up after ~1.5 s on a module still loading; events before arming are lost |
| Bump `version` in `metadata.json` (not just the manifest) | the runtime reads the version embedded in the plugin |
| Bundle any library the host lacks via the build (`lgx add` / an `lgx-portable` override), not a hand repack | e.g. QtBluetooth: Basecamp ships ~80 Qt libs but not Bluetooth; check each module's `NEEDED libQt6*` against the AppImage |

**Delivery fork → upstream v0.3.0** (in the transport module):[^deliv]
- `messageReceived` gained `source` (`live`/`history`) before the timestamp — read the timestamp
  from the last argument. Store catch-up is on by default; dedup the `history` replays.
- Wait for the `nodeStarted(success, msg)` event before reporting "ready"; surface `msg` on failure.
- Config: the layered `{mode, preset, messagingOverrides:{…}}` shape. Set `tcpPort`/`discv5UdpPort`
  to 0 explicitly. QUIC is on by default; phones are TCP-only, which still works (QUIC first, TCP
  fallback).
- **RLN:** on `logos.test` a v0.3.0 node with no membership receives but cannot send. Decide per
  `logos-rln-budget` — we run RLN off by config (`preset:""` plus explicit cluster/shard/entry-node
  parameters) for development, behind a config flag that restores the preset.
- Upstream doesn't carry two library patches we relied on (memory-only SDS persistence; deliver
  despite missing causal deps). The raw-receive path in our transport module isn't affected; a
  module consuming the channel event is — re-test it.

**Storage 2.x → 3.0** (only for modules using `storage_module`):[^stor]
- New boolean parameters: `uploadUrl(path, chunk, advertise)`, `fetch(cid, isPrivate, advertise)`,
  `downloadToUrl(cid, path, local, chunk, isPrivate, advertise)`; `togglePrivateQueries` and
  `migrateConfig` removed.
- `fetch`/`downloadToUrl` can now block ~30 s waiting for a manifest — longer than the default IPC
  timeout. Call them with `…AsyncResult(…, cb, 60000)`, never in a loop on the module's thread, and
  treat a timeout as "maybe still running".
- `destroy()` now fails while the node is busy: stop first, or check `isRunning()`.
- **The host owns Storage**: Basecamp 0.3 and `logosctl` initialise it from
  `~/.logos_storage/config.json`; your `init()` is refused. Adopt the host's node (`logos-storage`).

**An app that drove delivery_module itself** (its own `createNode`, `channelCreate`,
`onMessageReceived`) is easiest to port by moving it onto loam_core instead of re-fitting it to
0.3.0's config and event changes. That was WhisperBox 0.3.8 → 0.4.0:[^wb]
- `loam_core`: `setSenderId(deviceId)`, `start(cfg)` (`{"mode":"Core","preset":"logos.test","useChannels":true}`),
  `join(topic)` once `statusChanged` says `Connected` (also poll `status()`: the event can be
  lost), `sendSealed(topic, base64(bytes))`, `received(topic, senderId, base64(bytes), ts)`.
- Keep the wire: put the same bytes on the topic as before (and as the phones' loam-transport),
  so old phones keep interoperating. Hand `received`'s payload to the old ingest **still
  base64'd** if that ingest peels one or two layers itself; decoding it first broke the
  raw-envelope interop test.
- Don't start the transport in `onContextReady()`; let the first timer tick do it. Don't call
  modules inside a received/status callback: queue the work onto the module's thread.
- Replace the test fake of delivery_module with a fake loam_core (shared node, SDS self-filter
  by senderId, base64 payloads).

## Phase 2 — test without the GUI first

- **`logosctl` two-node sessions** (`logos-headless-logosctl`): install the rebuilt packages in
  two sessions, join the same room, write on one, read on the other — for every app. Then one old
  node (0.2 stack, old library) ↔ one new node, through the shim, in both directions.
- Only then the GUI: a **separate Basecamp profile** (`HOME=/abs/path/basecamp-0.3-home` before
  launching the 0.3 AppImage) so the user's 0.2 install and data are untouched, with **only** the
  test repo added (the official catalog carries other versions of some modules).
- Write the tester a checklist with what "working" looks like (status reaches Connected within
  ~30 s; an event made on the phone appears on the desktop and vice versa; restart keeps data and
  identities), and the known limitations.

## Phase 3 — later, separately

Phone library v0.39 (a JNI rewrite for the new C ABI, plus our Android patches re-applied —
`logos-mobile-app`), hubs moved to `logosctl`, macOS/ARM variants rebuilt with the same builder and
merged.

## Known open items (say so; don't paper over them)

- Every new-library channel message carries an ~18.7 KB SDS bloom filter (~25 KB per message,
  regardless of payload size). Asked upstream; it affects bandwidth on phones.
- Storage between two NAT-ed users needs a reachable hub that is an AutoNAT + relay server and
  caches; clients must bootstrap from it in the host's Storage config. How an app should set the
  host's Storage config is an open question for the platform team.
- A module reloaded into an already-running node can stay "Connecting…" (edge case, seen headless).

## Sources & evidence

[^plan]: `loam-basecamp` branch `port/0.3`: `docs/port-0.3/PLAN.md` (phases, decisions, risks) and `TESTING.md` (Basecamp 0.3.1 test setup, separate repo, checklist, known limitations). Memory `port-0-3` (all apps ported and two-node-tested with logosctl 0.3.1, 2026-10-03/04; awaiting the user's GUI test).
[^deliv]: `docs/port-0.3/analysis-delivery.md` §§ 1, 4, 5 (SegmentMessage nesting and field checks, API changes, layered config, QUIC/TCP), § 3 (lost patches). Shim: `loam-transport` `port/0.3` `src/segment-compat.ts` (`6c0474c`) and `2a472be` (applied on the shared-node client path after a device test showed Basecamp 0.3 → phone events still missing).
[^incident]: `docs/delivery-upstream-vs-fork.md` § "Incident 2026-10-02"; memory `delivery-upstream-vs-fork`, `port-0-3` (fork re-published as 0.9.0 with a 2-byte embedded-version patch).
[^p03mem]: Memory `port-0-3` ("v0.3.0 (and the old fork!) emit event payloads as {"_bytes": URL-SAFE base64} → old decoder truncated at '-'/'_'"; loam_core 0.5.2 + compat 0.4.19).
[^builder]: `docs/port-0.3/analysis-basecamp-builder.md` § Key findings + § Checklist (builder 0.3.1 `16e2f6b`; LIDL requirement `lib/common.nix:167-213`; icons `package.cpp:54-121`; version ranges `module_manager.cpp:851-868`; default args `impl_header_parser.cpp:893-905`; QML `logos` timeouts). QtBluetooth: memory `ble-mesh-qtbluetooth-bundle`.
[^stor]: `docs/port-0.3/analysis-storage.md` § 2a (API table, 30 s manifest wait, busy `destroy`) and memory `port-0-3` finding 2 (host-owned Storage, `AsyncResult` with a 60 s timeout).
[^fork09]: Swamp 0.5.6 on a Lenovo Duet and vpavlin's laptop, 2026-10-09: delivery_module 0.9.0 installed from `apps.vpavlin.xyz/logos-repo.json` + the 0.2-era LAN repo; 1375 "stash full" lines and `channel_message_received` 0 in the newest log; after installing delivery 0.3.2 the Duet received a hub-only model. Fork versions 0.9.0/0.2.3/0.1.4 dropped from the public catalogue the same day (the maintainer's agent).
[^wb]: `vpavlin/whisperbox-logos` branch `port/0.3` (`0b13b60` desktop, `4faf244` Android with loam-transport `2a472be`): all 8 test layers green (e2e 201/201, JS<->C++ interop with raw and base64 envelopes); live logosctl two-node on logos.test, form A->B in 64 s and the answer back decrypted in 3 s.
