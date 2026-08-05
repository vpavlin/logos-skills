# HANDBOOK — Standing up a new multi-writer Logos app from zero

A repeatable recipe for a **new Logos/Waku app where several devices or people edit shared state offline and must converge to identical state without losing any write** — delivered as a desktop **Basecamp core+view module**, an **RN/Expo mobile app**, and an optional **always-on headless hub**, all syncing over **SDS Reliable Channels**.

This is generic. It fits a shared calendar, an activity/habit tracker, collaborative notes, a Q&A board, a household ledger — any app where "last write wins" would silently drop someone's change. The origin project appears only as footnoted evidence.[^1]

The five sibling skills are the depth for each layer; this handbook is the spine that orders them and names the seams between them.

---

## 0. The thesis (internalize before building)

**Never store derived state. Never mutate a record.** Every change is an immutable, append-only **event**. Current state is a pure **deterministic fold** over the **merged** event log. Merge of two replicas is the **union of events by `id`** — nothing is ever overwritten, so no concurrent write is lost.[^2]

Everything else — HLC ordering, encryption, reliable channels, the JNI bridge, the QML view — is machinery to move those events between replicas and re-fold. If the fold is pure and the merge is a union, offline-merge, idempotent redelivery, and eventual convergence come for free. If it isn't, no transport can save you.

> The named hard problem: a shared dataset has **two or more concurrent writers by definition**. LWW sync "cannot handle a second writer: two devices editing the same record disagree permanently, with no error and no convergence."[^1] A tracker might tolerate that; a ledger cannot. Design around it from line one.

---

## 1. Decisions up front (a checklist to close before coding)

Make these explicit and write them into an ADR/decision log. Reversing them later is expensive.

### 1.1 Event schema
```
Event = { v:1, id:UUIDv4, type, hlc:{wall,ctr,dev}, dev, payload }
```
- `id` — client-generated UUIDv4, the **idempotency key**. Redelivery dedups on `id`; merge is union-by-`id`.[^2]
- `v` — schema version, present from the first event. You will migrate.
- `type` — a closed enum of event types (`thing.create`, `thing.edit`, `thing.delete`, plus your domain verbs).
- `hlc` / `dev` — see 1.2. `dev` also lives at top level for convenience but is `hlc.dev`.
- `payload` — per-type shape; keep each constructor documenting its fields.

Provide a single `makeEvent(type, hlc, payload, id=uuid())` and thin typed constructors over it; never hand-build event objects at call sites.[^3]

### 1.2 Ordering: Hybrid Logical Clock
Every event carries an HLC so **all replicas order events identically**, independent of arrival order or wall-clock skew.[^4]
```
HLC = { wall, ctr, dev }   // wall = max observed ms; ctr = same-ms counter; dev = device id
```
- A per-device `Clock` with `send()` (stamp a local event) and `receive(remoteHlc)` (advance past a cause before authoring anything after it).
- `compareHlc` totally orders by `wall`, then `ctr`, then `dev` lexicographically — deterministic on every replica.[^4]
- **The wall component is for ordering ONLY.** Never derive money, counts, or any domain value from it.

### 1.3 Delta ops — what must be commutative vs LWW
Classify every mutation:
- **Commutative deltas** — quantities that concurrent writers both change (moving budgeted money, incrementing counters, reordering). Model as **net-zero, two-legged deltas that sum** instead of clobbering. Example: `move(from, to, amount)`; `assign(cat, month, amount, mode:'delta')`.[^5]
- **LWW-by-HLC** — single-valued attributes where "last edit wins" is acceptable (a rename, a flag, an absolute set: `assign(..., mode:'set')`). LWW is resolved by `compareHlc`, not wall clock.[^5]
- **Edit / delete are events, never overwrites.** An edit is a **superseding** event (same target id, higher HLC); a delete is a **tombstone**. The fold applies supersede/tombstone during projection. Sync semantics are unchanged — they are just more events in the union.[^6]

### 1.4 Tenant / group model
- One shared dataset = one **32-byte secret** = one derived **content topic** = one **reliable channel**. A device can hold several at once and sync them all in the background.[^7]
- **Sharing = who has the secret.** A dataset whose pairing code you never share stays private (but still pairs across your own devices + hub).[^7]
- If you need **roles**: fold membership from events (`group.init` → founding admin, `member.add/role/remove`) in HLC order and admit each event by its author (`hlc.dev`).[^8] Until a `group.init` appears, treat the dataset as personal/household and admit everything (backward compatible).
- **Enforcement is on merge, and it is the only enforcement possible** in an append-only p2p log. Roles are attribution + admission, not cryptographic access control. Anyone with the secret can write. Per-member cryptographic ACLs (MLS/forward secrecy) are a later, separate layer — don't block on them.[^8]

### 1.5 Transport
Use **SDS Reliable Channels** (see logos-reliable-channels). It gives ordering, gap detection, retransmit, segmentation, causal-history references — inside the delivery layer, for free.[^9] Prefer it over hand-rolling ack/retransmit on raw `subscribe`/`send`, and over libchat (whose groups are plain relay, unpersisted, add-only, no mobile).[^10]

### 1.6 Crypto
Derive everything from one pre-shared secret `S`:
```
K            = HKDF-SHA256(S, salt="app-pair-v1")           // room key
contentTopic = "/app/1/" + hex(HMAC-SHA256(K,"topic|epoch")[0..15]) + "/proto"
Ke           = HKDF-SHA256(K, info="app/payload/v1")        // AEAD key
wire payload = nonce(12) ‖ ChaCha20-Poly1305(Ke, nonce, plaintext, aad=contentTopic)
```
The topic is HMAC-derived (unlinkable without `K`); the payload is AEAD-sealed with the topic as AAD, so a wrong key or wrong topic fails the tag.[^11] The channel layer does **not** give you content encryption — you seal before handing bytes to the transport, and `open()` after receive.[^9]

### 1.7 Cold-start history
The reliable channel syncs **live** traffic; it does **not** backfill state across a cold start / fresh join.[^9] Pick one before you ship:
- **rebroadcast-on-join** — an online peer re-sends its log to newcomers (simple; noisy; fine as a safety net).
- **store-query pull** — a joiner pulls history from a fleet store node via `waku_store_query` (reliable, no rebroadcast; mobile-only on some builds — the desktop `delivery_module` may not bridge it).[^12]
- **RBSR / Negentropy reconciliation** — peers exchange range fingerprints and transfer only the symmetric difference of event-ids.[^13] Most bandwidth-efficient; more code.

### 1.8 Identity / senderId
A stable **per-device id**, generated once and persisted, distinct from the tenant secret. It is the HLC `dev` tie-break **and** the channel `senderId`. Give the hub and each client distinct ids so you can tell whose traffic is whose on the wire (and not mistake a peer's echo for your own).[^14]

---

## 2. Build order

Each step converges/tests before the next. Do not skip ahead — a green step is your baseline when a later step "syncs nothing."

### Step 1 — Contract + engine + fold (pure, no network)
Load **logos-multiwriter-sync**.

Build, in a portable language (a TS reference is convenient; you'll re-implement in C++ for the core):
- the **contract**: event schema, HLC, money/quantity type, type enums;
- the **fold**: `mergeEvents(...logs)` (union by `id`, sort by `compareHlc`) → deterministic projection to current state;[^2]
- an **invariant oracle** if your domain has one (e.g. a ledger's zero-based check).

**Gate:** a **convergence property test** — generate N random event logs, split across "devices", merge in every order, assert identical folded state. Plus golden vectors to pin the C++ port against the reference.[^15]

### Step 2 — Crypto + wire envelope
Still logos-multiwriter-sync.
- `seal/open` (§1.6); topic derivation from the secret.
- Wire envelope: one event per message, `encode/decodeEvent` → `{v, type:"EVENT", event}` bytes, sealed then published; `decodeEvent` throws on a non-EVENT envelope.[^16]

**Gate:** round-trip seal→open with the right key succeeds; wrong key throws.

### Step 3 — Reliable-channel transport (two desktop cores converge)
Load **logos-reliable-channels**. Load **logos-distributed-debugging** now and keep it open.

The SDS app-facing surface is three calls + three events:[^9]
- `channelCreate(channelId, contentTopic, senderId)` — build the channel + its listener.
- `channelSend(channelId, {payload, ephemeral})` — `payload` is base64 at the FFI boundary.
- `onChannelMessageReceived → {channelId, senderId, payload}` — payload base64 from the FFI serializer.
- plus `onChannelMessageSent` / `onChannelMessageError`.

Convention: **`channelId == contentTopic`** (the tenant's derived topic), so all devices in a dataset join one channel; `senderId` identifies the device.[^17]

FFI C exports if you bind the lib directly:[^18]
```
logosdelivery_channel_create(ctx, cb, ud, channelId, contentTopic, senderId)
logosdelivery_channel_send  (ctx, cb, ud, channelId, messageJson)   // messageJson = {"payload":"<base64>","ephemeral":false}
logosdelivery_channel_close (ctx, cb, ud, channelId)
```

**Gate:** two cores (or a core + the CLI) editing offline, then online, fold to identical state. Watch the debug counters — `rxRaw` climbing means the mesh delivers; `rxOpened` climbing means it decrypts.

### Step 4 — Basecamp core+view module
Load **logos-basecamp-module**.

Two packages, versioned separately:[^19]
- **core** (`type:"core"`, `interface:"universal"`, `main:"<name>_plugin"`, depends on `delivery_module`) — the C++ port of the fold + crypto + the channel wiring from step 3. No Qt.
- **view** (`type:"ui_qml"`, `view:"Main.qml"`, depends on the core) — a thin pure-QML surface.

The bridge:
- view → core: `logos.callModule("<core_name>", method, args)`.[^20]
- core → view: emit module events; view listens via `logos.onModuleEvent("<core_name>", "stateChanged")` and re-polls.[^20]

Package with the module builder to a portable `.lgx` (linux-amd64 variant).[^21]

**Gate:** the view renders the fold in a live host; an edit in the view produces an event that syncs to the step-3 peer.

### Step 5 — Headless hub (always-on availability, no server)
Same core binary, run standalone under the daemon CLI (`KYM_USE_CHANNELS`-style flag on). This is availability without custody: the hub holds and relays the encrypted log but, given the enforcement model, is just another member.[^22]

**Gotcha:** a default config often has **no entry nodes** → the hub is silently isolated ("No peers for topic") and its tick may need arming via an env flag. Give it entry/store peers, `Restart=always`, and enable-linger.[^22]

### Step 6 — Mobile (RN/Expo, Android-first)
Load **logos-mobile-app**.

There is no public x86_64 build and no autolinkable package. Bridge the prebuilt **arm64-only** `liblogosdelivery.so` yourself:[^23]
- a hand-written JNI shim (`logos_messaging_ffi.c`) wrapping the **stable high-level API** (`logosdelivery_create_node/start_node/stop_node/destroy/subscribe/send/get_node_info/channel_create/channel_send/channel_close/set_event_callback`) plus a few kernel symbols (`waku_relay_subscribe`, `waku_store_query`). Note `waku_stop`/`waku_destroy` are **not exported** — call `logosdelivery_stop_node`/`_destroy` (same ABI).[^18]
- a Kotlin `ReactContextBaseJavaModule` → an RN `NativeModule` (e.g. `LogosMessaging`); received messages come back as a single `DeviceEventManagerModule` JS event.[^23]
- a **config plugin** (`withLogosDelivery.js`) that re-copies the `.so`s and Kotlin into `android/` on **every** `expo prebuild` (CNG regenerates `android/` from scratch, so nothing hand-added there survives), registers the package, and forces legacy jniLibs packaging.[^24]

**Gate:** phone joins the same channel as desktop + hub and converges both directions, live and after offline edits — tested on **real arm64 hardware**, not an x86_64 emulator.[^25]

### Step 7 — Instrument (the whole time)
Load **logos-distributed-debugging** from step 3 onward. One counter per receive stage, surfaced in a debug blob / status card:[^26]
- `rxRaw` — native transport callback fired at all (mesh delivering).
- `rxSeen` — a message with non-null payload extracted.
- `rxOpened` — payload decrypted+authenticated with a room key.
- by-eventType: `dChan` (`channel_message_received` — SDS-unwrapped, openable) vs `dMsg` (raw `message_received` — still SDS-framed, won't open) vs `dErr`.

Symptom → stage: `rxSeen 0` ⇒ no peer / mesh not delivering. `rxSeen>0, rxOpened 0` ⇒ wrong key/topic or payload-encoding mismatch. `dMsg` climbing but `dChan` 0 ⇒ channel layer never firing (you subscribed but didn't `channelCreate`, or vice-versa).[^26]

### Step 8 — Finalize & ship (go-live)
A converging build is not a shipped one. The last mile is its own step, and each surface's mechanics live in its skill (**logos-basecamp-module** for the `.lgx`, **logos-mobile-app** for the APK). The reproducible sequence:

1. **Version in lockstep.** The core, the view, and the mobile app version independently but must be **co-released** — a view calling a core method the deployed core lacks is the opaque "Invalid response" (pitfall 5). Bump all that changed; record it in a CHANGELOG.
2. **Build the artifacts.**
   - Desktop: `nix build .#lgx-portable` for **both** the core module and the view module → one `.lgx` each. (Git-track every new file first — nix flakes only see tracked files — and pin SDK inputs by full 40-char SHA.)
   - Mobile: `expo prebuild --platform android` then `./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a` → a release-key-signed APK (arm64-only).
3. **Publish to the repo the client actually reads** (the #1 publish trap — publishing to a repo nothing points at looks like success and ships nothing):
   - Basecamp: drop each `.lgx` into a **package repo**, regenerate its `index.json` + `logos-repo.json`, serve over HTTPS (a LAN host, or a public catalog repo that points at released artifacts).
   - Mobile: a self-hosted **F-Droid** repo — a `metadata/<pkg>.yml` is **required** or `fdroid update` produces an *empty* index (the device sees no app); `fdroid update` re-signs the index with the repo keystore (the APK is separately release-key-signed).
4. **Cut a release.** Commit + docs/CHANGELOG, tag, `gh release create <tag>` with **the artifacts attached** (each `.lgx` + the `.apk`). Optionally a public catalog repo whose index points at the released artifact URLs, so installs come from GitHub releases, not your laptop.
5. **Verify from the published path, not your build output.** Install the app on a *clean* client from the published repo — the exact version a user fetches — and confirm it launches and syncs. This is the "verify via the real path" discipline: your `dist/` working correctly proves nothing about what the repo serves (a real regression this session: an APK was published to the wrong F-Droid repo and the phone kept seeing the old version).

---

## 3. Where each sibling skill plugs in

```
                 ┌─────────────────────────── logos-distributed-debugging
                 │                              (counters across every stage, steps 3–7)
                 ▼
 EVENT MODEL            TRANSPORT             DESKTOP              MOBILE
 logos-multiwriter-sync logos-reliable-       logos-basecamp-      logos-mobile-app
 · schema, HLC, fold    channels              module               · JNI bridge to .so
 · commutative deltas   · channelCreate/Send  · core (universal)   · config plugin
 · union merge          · onChannelMessage    · ui_qml view        · cross-thread event
 · roles/admission      · segmentation/SDS    · callModule bridge  · store_query pull
 · crypto + wire        · NOT history/ACL/enc · .lgx packaging     · F-Droid release
        │                      │                     │                    │
        └──────────────── the SAME sealed EVENT envelope on the SAME channel ──────────────┘
                     (desktop core, hub, and phone are peers, not client/server)
```

The seam that ties them: **one sealed event envelope format + one channel per tenant.** The C++ core, the headless hub, and the RN phone all encode the same `{type:"EVENT", event}`, seal it with the same room key, and put it on the same `channelId == contentTopic`. Any of them can author; all of them fold identically.

---

## 4. Cross-cutting pitfalls (the silent ones)

These each, alone, produce "syncs nothing" with no error.

1. **Payload double-encoding mismatch.** The FFI base64-encodes on the way out and in. If one peer hands the transport already-base64 *text as bytes* (so it gets base64'd again) and another sends single-encoded, they can't decode each other. Pick one convention and make every peer match it; the receive side must mirror the send side's encoding exactly.[^27]

2. **The `subscribeContentTopic` gate.** `channelCreate` builds the channel + listener but does **not** subscribe the content topic. The receive service only emits for topics in its subscribed set, and the channel's ingress rides that same event — so without an explicit content-topic subscribe, the node drops every incoming message *before the channel sees it*. Desktop cores get this via their node's subscription path; a raw mobile FFI does not — subscribe **and** channelCreate.[^28]

3. **Cross-thread signal drop.** The delivery layer may emit `messageReceived` off the transport/Qt thread; a naive connection drops the cross-thread signal and the app receives nothing while the wire is fine. Ensure the received-event hop is thread-safe (queued connection / posting onto the JS or event-loop thread).[^29]

4. **Synchronous delivery calls freezing the core.** A blocking `send`/`getNodeInfo` on the event-loop thread stalls on the IPC/lightpush timeout and freezes the module ("buttons stuck"). Make every delivery call **async/fire-and-forget**; the transport reports outcomes via its own events.[^30]

5. **View/core version skew.** The view and core are separate packages; a view calling a core method the deployed core doesn't have gets an opaque "Invalid response." Bump and ship them together; gate new view features on a minimum core version.[^19]

6. **Manifest `main` vs `view`.** A `ui_qml` view module loads QML from the key the host expects for its version (`view:` vs `main:`); getting it wrong renders nothing with no error. Verify against the host version you actually run.[^31]

7. **Hub silently isolated.** No entry nodes in the default config ⇒ "No peers for topic" and zero sync, looking exactly like an app bug. Configure entry/store peers explicitly and confirm peer count before blaming the app.[^22]

8. **arm64-only `.so`.** No x86_64 build — an emulator will never load the node. Test mobile sync on a real phone.[^25]

9. **Enforcement theater.** Roles/attribution in the log are not access control; anyone with the secret can write. Don't design a feature that assumes the log can keep a member out — it can't until a crypto-membership layer exists.[^8]

---

## 5. "Done" definition
Two bars — **converging** (the engineering is right) and **shipped** (a user can actually get it).

Converging:
- Convergence property test green (step 1).
- Two desktop cores + hub converge over the channel, live and after offline edits (steps 3–5).
- Phone converges with them both directions on real hardware (step 6).
- The debug counters distinguish every failure stage (step 7).
- A cold-start joiner gets full history via your chosen §1.7 mechanism.

Shipped (step 8):
- Core + view `.lgx` published to a Basecamp package repo; APK published to an F-Droid repo *with* a `metadata/<pkg>.yml`.
- A tagged release with the artifacts attached (and, if public, a catalog repo pointing at them).
- Installed on a **clean client from the published repo** (not your build output) — launches and syncs.

---

## Sources & evidence

All file paths are repo-relative to **github.com/vpavlin/kym** (the origin project, cited as evidence only); provenance notes name the persistent-memory file the lesson is recorded in.

[^1]: The "second writer breaks LWW" framing and the append-only-ledger response — `README.md` ("The one hard problem, named up front"); memory `kym-project.md`. https://github.com/vpavlin/kym/blob/master/README.md
[^2]: Union-by-`id` merge + deterministic fold — `mergeEvents()` in `packages/engine/src/engine.mjs`; `makeEvent` in `packages/contract/src/events.mjs`. https://github.com/vpavlin/kym/blob/master/packages/engine/src/engine.mjs
[^3]: Event constructors (`makeEvent` + typed `ev.*`) — `packages/contract/src/events.mjs`. https://github.com/vpavlin/kym/blob/master/packages/contract/src/events.mjs
[^4]: HLC `{wall,ctr,dev}`, `Clock.send/receive`, `compareHlc` — `packages/contract/src/hlc.mjs`. https://github.com/vpavlin/kym/blob/master/packages/contract/src/hlc.mjs
[^5]: Commutative `assign(mode:'delta')` vs LWW `mode:'set'`, net-zero `move` — `ev.assign` / `ev.move` in `packages/contract/src/events.mjs`. https://github.com/vpavlin/kym/blob/master/packages/contract/src/events.mjs
[^6]: Edit = superseding event, delete = tombstone — `EventType.TXN_EDIT/TXN_DELETE` in `packages/contract/src/events.mjs`; memory `kym-tx-edit-attribution.md`.
[^7]: One secret → one topic → one log per dataset; sharing = who holds the code — `README.md` ("Households & multiple budgets"); memory `kym-multibudget.md`.
[^8]: Role admission folded from `group.init`/`member.*`, enforcement-on-merge only — `admitEvents()` in `packages/engine/src/engine.mjs`; `Role`/`MEMBER_*` in `packages/contract/src/events.mjs`; memory `kym-tx-edit-attribution.md`.
[^9]: SDS Reliable Channel surface + "does NOT give you history/ACL/encryption" — `mobile/src/lib/delivery.ts` (`joinRoute`, `USE_CHANNELS`) and `mobile/native/logosdelivery/jni/liblogosdelivery.h` ("Reliable Channels API"); memory `kym-libchat-vs-reliable-channels.md`, `kym-sds-desktop-proven.md`.
[^10]: SDS vs libchat vs raw relay verdict — memory `kym-libchat-vs-reliable-channels.md`.
[^11]: HKDF room key, HMAC topic, ChaCha20-Poly1305 with topic-as-AAD — `packages/sync/src/crypto.mjs` (`deriveIdentity`, `topicFor`, `seal`, `open`). https://github.com/vpavlin/kym/blob/master/packages/sync/src/crypto.mjs
[^12]: Store-query pull is mobile-only on the current build; desktop `delivery_module` doesn't bridge it — `mobile/native/logosdelivery/jni/logos_messaging_ffi.c` (`waku_store_query`) and `.../liblogosdelivery_kernel.h`; memory `kym-delivery-ffi-surface.md`, `kym-store-pull.md`.
[^13]: RBSR / Negentropy reconciliation (fingerprint = XOR of `sha256(id)`, order by `(hlc.wall, id)`) — `packages/sync/src/reconcile.mjs`. https://github.com/vpavlin/kym/blob/master/packages/sync/src/reconcile.mjs
[^14]: senderId per device (hub vs client) and reading whose traffic is on the wire — memory `kym-senderid-and-shard.md`.
[^15]: Convergence property test + golden-vector parity — `README.md` ("Try it": `npm test` 200-trial convergence + golden vectors), `module/README.md` (`test/parity.cpp`, 24/24).
[^16]: Wire envelope `{v,type:"EVENT",event}`, `encodeEvent`/`decodeEvent` — `packages/sync/src/wire.mjs`. https://github.com/vpavlin/kym/blob/master/packages/sync/src/wire.mjs
[^17]: `channelId == contentTopic == derived topic`, `senderId == deviceId` — `KymCoreImpl::joinBudgetTransport` (`channelCreateAsync`) in `kym_core/src/kym_core_impl.cpp`; `joinRoute` in `mobile/src/lib/delivery.ts`.
[^18]: FFI C exports incl. `logosdelivery_channel_create/send/close`, high-level node API, and `waku_stop`/`waku_destroy` NOT exported — `mobile/native/logosdelivery/jni/liblogosdelivery.h` and `logos_messaging_ffi.c`; memory `kym-delivery-ffi-surface.md`, `kym-android-liblogosdelivery-build.md`.
[^19]: core + view are separate versioned packages; skew → "Invalid response" — `module/metadata.json` (`type:ui_qml`, `view:Main.qml`, `dependencies:[kym_core]`) and `kym_core/metadata.json` (`type:core`, `interface:universal`, `main:kym_core_plugin`); memory `kym-view-core-version-skew.md`.
[^20]: `logos.callModule(core, method, args)` and `logos.onModuleEvent(core, "…Changed")` — `module/Main.qml`. https://github.com/vpavlin/kym/blob/master/module/Main.qml
[^21]: Portable `.lgx` (linux-amd64) build — `module/README.md` (`nix build ./#lgx-portable`); memory `logos-repo-publishing.md`.
[^22]: Headless hub as a peer + "no entry nodes = silently isolated", needs Restart=always/linger — `hub/` (`kym-hub.service`, `kym-hub.sh`); memory `kym-hub-runner.md`, `kym-headless-hub.md`.
[^23]: JNI shim + Kotlin RN module + single JS event; arm64-only `.so` — `mobile/native/logosdelivery/jni/logos_messaging_ffi.c`, `.../android/java/com/receiverandroid/LogosMessagingModule.kt`; memory `kym-android-liblogosdelivery-build.md`.
[^24]: Config plugin re-copies on every `expo prebuild` — `mobile/plugins/withLogosDelivery.js`. https://github.com/vpavlin/kym/blob/master/mobile/plugins/withLogosDelivery.js
[^25]: Must test on real arm64 hardware, not emulator — memory `perun-native-delivery.md`, `kym-mobile-channels-working.md`.
[^26]: Per-stage counters `rxRaw/rxSeen/rxOpened`, `dChan/dMsg/dErr` — `mobile/src/lib/delivery.ts` (counter block ~L147–L420); memory `kym-mobile-channels-receive-rootcause.md`.
[^27]: Double-base64 payload convention/mismatch — `mobile/src/lib/delivery.ts` (`publishSealed`, `doubled = fromByteArray(utf8Bytes(sealedB64))`) and `KymCoreImpl::deliverySend` (`bytesPayload`) in `kym_core/src/kym_core_impl.cpp`; memory `kym-sync-root-cause-bytes-payload.md`, `kym-mobile-channels-working.md`.
[^28]: `subscribeContentTopic` gate before `channelCreate` (the `ours:0` bug) — `joinRoute` in `mobile/src/lib/delivery.ts`; memory `kym-mobile-channels-receive-rootcause.md`.
[^29]: Cross-thread `messageReceived` drop — memory `kym-headless-hub.md` (delivery emits off the Qt thread → QRO drops cross-thread signal).
[^30]: Async delivery calls (sync send freezes the event loop = "stuck buttons") — comment + `channelSendAsync`/`sendAsync` in `KymCoreImpl::deliverySend`, `kym_core/src/kym_core_impl.cpp`; memory `kym-stuck-buttons-async-delivery.md`.
[^31]: Manifest `main` vs `view` renders-nothing gotcha — memory `kym-desktop-view-fix.md`, `logos-basecamp-version-matters.md`.