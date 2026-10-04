---
name: logos-app-charter
description: "The house rules for how WE build Logos/Loam apps (perun, kym, qaku, scala, kith, shrooms) — the non-negotiable principles and the why behind them, so a fresh app or a fresh agent doesn't rediscover them. Read at project kickoff, when scoping a new app or feature, when deciding a cross-cutting question (which platforms, where state lives, how sync works, how identity/crypto works, how to publish), or when onboarding another agent to this codebase's conventions. Covers: always ship Basecamp desktop + Android together; build on Loam (loam_core transport, loam-sync CRDT brain, deterministic-nonce crypto, loam-keycard identity); local-first and offline-first as hard requirements; append-only event-log CRDT with LWW/tombstones/edit-supersede; zero-trust household sealing; swappable infra seams (transport, sync, storage); the publish + docs/ADR discipline; and which sibling skill to load for each layer. This is the CHARTER/index of conventions; the layer skills (logos-multiwriter-app-blueprint, logos-multiwriter-sync, logos-reliable-channels, logos-basecamp-module, logos-basecamp-0.3-port, logos-headless-logosctl, logos-mobile-app, logos-publish-artifacts, logos-fdroid, logos-multiplatform-modules, logos-storage, logos-rln-budget, logos-distributed-debugging, loam-integrate-app, loam-update-app, loam-keycard) carry the mechanics. Also covers: never await the wire send, design for an RLN message budget, version-scoped delivery config, and the cross-repo auto-upgrade trap."
---

# Logos app charter — how we build, and why

These are the standing rules for the apps in this ecosystem (perun, kym, qaku, scala, kith,
shrooms). They exist so we don't re-derive the same decisions on every new app, and so
another agent can pick up the conventions without archaeology. Each rule states the *why* —
follow the rule, but understand it so you know when a genuine exception applies.

Depth for each layer lives in a sibling skill (named at the end). This is the index of
*principles*; those are the *mechanics*.

## The rules

### 1. Ship Basecamp (desktop) AND Android — together, at parity
Every app is a **Basecamp `ui_qml` module** (QML view + C++ backend) **and** an
**RN/Expo Android app**, folding the **same event log** off the **same wire contract**.
- *Why:* the value is multi-device/household convergence; one platform alone isn't the
  product. Parity is the feature.
- *How:* a wire-contract change lands on **both** platforms in the same change, never one
  ahead of the other (a stale peer silently diverges). Verify each side (mobile: expo
  export + on-device; desktop: nix `.lgx` build; there's rarely a QML render harness, so
  eyeball on real Basecamp).

### 2. Build on Loam, don't re-implement it
Use the shared Loam bits instead of hand-rolling per app:
- **Transport:** `loam_core` (Basecamp) / `logos-transport` (mobile) — ONE shared delivery
  node per device, SDS Reliable Channels, Core/Edge, future BLE bearer. Apps are *clients*.
- **Sync brain:** `loam-sync` (aka logos-sync) — the event-log CRDT: HLC ordering,
  range-based reconciliation (RBSR) for cold-start backfill, the merge/fold. Import it as an
  in-tree submodule with a committed dist (Metro can't bundle it out-of-tree); genuinely
  *use* it, don't keep a byte-equivalent copy.
- **Crypto:** the deterministic id-derived nonce schedule (ADR 0011) lives in loam-sync;
  reuse it (domain = the app name) so every platform's seal is byte-identical.
- **Identity:** `loam-keycard` when a person (not a device) must sign — hardware key, per
  container binding, one identity across phone (NFC) + desktop (PC/SC).
- *Why:* one node per phone (not N), one audited crypto path, one convergence engine — less
  surface, fewer divergence bugs, shared fixes.
- *Integration guides:* **Android** shared node + owner approval — `loam/INTEGRATE.md` +
  the `loam-integrate-app` skill (the `preferServiceBackend` ordering + consent gotchas).
  **Basecamp/desktop** — `loam-basecamp/INTEGRATE.md` (depend on the `loam_core` module:
  the `start`/`join`/`sendSealed`/`received`/`statusChanged` surface, no binder approval
  in-process). The delivery config shape depends on the installed `delivery_module`: **flat** on
  the 0.1.x fork (the layered shape is rejected there), **layered** on upstream v0.3.0 — see rule 11.

### 3. Local-first is a hard requirement
State is the user's; it lives **on the device first and always**. Authoring **never blocks
on the network** and never fails without it.
- *Why:* the app must be fully usable offline and feel instant; sync is an enhancement, not
  a precondition.
- *How:* persist locally, then best-effort send; carry an unsynced flag and retry when the
  receiver comes up.
- **Never `await` the wire send in the write path.** Append to the local log, update the UI,
  return — then send in the background. The send is only the fast path; reconciliation (RBSR)
  delivers anything the send missed, so waiting on it buys nothing and freezes every edit when the
  node is slow or absent. Show a "syncing" badge on events not yet on the wire instead.
- *Also off the edit path:* long loops of awaited native calls (rescheduling hundreds of
  notifications, IPC) and per-event crypto in the fold (signature verify is ~40 ms on Hermes —
  memoize it by (pubkey, sig, digest) and persist the memo). Both looked like "sync is slow". Media bytes are stored on-device at capture; a server/Codex is a
  *replication target*, never the source of truth and never on the capture path.

### 4. Must work offline — and converge later
Capture, edit, and view work with no network. When peers reconnect they converge with no
lost writes and no coordinator.
- *How:* SDS gives ordering/gap/retransmit *within a session*; it does **not** backfill
  pre-join history (the 0.1.x fork exposes no store query to modules; v0.3.0's store catch-up
  returns only what fleet store nodes still hold) — layer app-level RBSR
  (loam-sync) on top for cold-start. If a device shows nothing after joining, that's the
  missing layer, not the network.

### 5. Model state as an append-only event-log CRDT
**Never mutate a record; never store derived state.** Current state is a pure deterministic
**fold** over the merged log.
- Immutable events, **dedup by `id`** (re-delivery is a no-op → idempotent).
- **Edit = a supersede event** (`kind:"edit"`, `target`=id, new fields), applied **LWW by
  createdAt** in the fold — never an in-place change.
- **Delete = a tombstone** (`kind:"delete"`, `target`) — order-independent (commutes with a
  late original), can't be un-seen.
- The fold is **order-independent** (an edit/delete may arrive before its target).
- *Why:* offline multi-writer merge, idempotent redelivery, and convergence fall out for
  free. A mutable field is a lost-update waiting to happen.

### 6. Zero-trust: seal with the household key before anything leaves the device
One 32-byte household secret (shared out-of-band via a pairing QR + word fingerprint) is the
whole key. Everything derives from it (HKDF schedule; deterministic id-derived nonce).
- Payloads are **sealed** (AEAD, AAD = topic) before they touch the wire or any server.
- Large media is **content-addressed by `sha256(sealed)`** — a store only ever holds
  ciphertext, and identical content dedups. **Hash the sealed bytes, not plaintext**
  (plaintext hashing leaks content-equality and breaks zero-trust).
- Never surface the raw secret/topic (secret-adjacent); show only the fingerprint / a QR.

### 7. Put infrastructure behind swappable seams
Anything that is "which backend/transport/store" is an interface the app codes against, so
infra swaps without touching app logic:
- **Transport** behind `loam_core` (server node today, BLE bearer tomorrow).
- **Sync** behind loam-sync.
- **Blob storage** behind a `BlobBackend` (`put(cid)/get(cid)`): embedded LAN hub today →
  **Logos Storage / Codex `storage_module`** tomorrow, same contract, mobile+UI unchanged.
- *Why:* it lets us ship a pragmatic thing now (an embedded hub, a LAN server) and upgrade
  to the decentralized thing later as a localized change, not a rewrite.

### 8. Cross-module the Logos way
A Basecamp module declares its dependencies in `metadata.json` + a flake input, and calls
them via `modules().<dep>.method()` + `.on("event", cb)` (async completion via typed
events). Prefer a **module dependency** (e.g. `loam_core`, `storage_module`) over embedding
a subsystem when a real module exists. A module may **autostart a service** in its backend
(e.g. an embedded blob hub) — fine for "runs with the module", but an always-on need argues
for a headless node.

### 9. Publish deliberately (it silently ships nothing otherwise)
- **Basecamp:** publish the **portable** (`linux-amd64`) `.lgx`, never `-dev` ("NOT
  AVAILABLE"). One stable-named `.lgx` per module (overwrite), regenerate the signed index,
  verify from the **served** URL (sha + version), not `dist/`.
- **F-Droid:** each app needs `metadata/<applicationId>.yml` or `fdroid update` **drops the
  APK silently**. Bump versionCode and leave `CurrentVersionCode` **unset** (any pin — including
  the old maxint placeholder — strands the update: "app is there, no update offered"). In-place
  update needs the **same signing key**. The repo's `repo_url` is **plain http** (+ fingerprint)
  unless the host has a publicly trusted cert — a self-signed https address baked into the index
  breaks adding the repo on new phones. Optional hardware (NFC, camera) is `required="false"`.
  Depth: `logos-fdroid`.
- **The nix trap:** a flake only sees **git-tracked** files — `git add` new sources/icons
  **before** building or they're invisible (and the old manifest ships).
- Reuse `logos-publish-artifacts` (+ the app's own publish scripts); don't hand-roll.

### 10. Never block, never render peer text as markup, never fail silently
- **No blocking IPC in a view, ever.** Every cross-module call from QML goes through
  `logos.callModuleAsync` (one helper), polls are single-flight, action buttons guard
  double-fire. *Why:* a synchronous `callModule` freezes the whole view for up to 20 s
  whenever the core is busy — "the app hangs" with nothing in the logs.
- **`textFormat: Text.PlainText` on every text item showing other people's data.** *Why:* Qt
  renders HTML-looking strings and fetches remote `<img>` the moment they're shown — one crafted
  title leaks every reader's IP. Gate with `qml-plaintext.py --check`.
- Mechanics: `logos-basecamp-module` (§ no blocking calls, § plain text).

- **Every outcome is visible.** Each action reports success or a human-readable failure;
  cores return `{ok, error}` and never throw across IPC; a write the fold would drop is
  refused up front, not stored and silently discarded; long operations end in done or
  failed, never an endless spinner. *Why:* the transport already fails silently — the app
  is the only place a user can learn something went wrong. Mechanics:
  `logos-basecamp-module` (§ error handling), `logos-distributed-debugging`.

### 11. Run the same versions everywhere a node runs
A hub, a desktop and a phone that sync together must run compatible delivery/transport builds.
- *Why:* a hub one delivery version behind meshed fine but couldn't reassemble newer clients'
  segmented catch-up — a joined room stayed empty with no error.
- *How:* upgrade the hubs with the clients; when sync is "connected but empty", compare versions
  first.
- **Know which repo can upgrade you.** Basecamp installs the highest version of a package across
  every repo the user added. A fork that sits below upstream's version number gets "upgraded" to
  upstream the day a user adds the official catalog — every app stopped syncing that way once.
  Prefer renaming a fork (or pinning dependency ranges); versioning it above upstream works only until the
  apps move to upstream, then the fork outranks it the other way. Publish platform-migration test builds
  to a separate repo.
- **Version-scope anything the platform changed.** Delivery config (flat on the 0.1.x fork, layered
  on v0.3.0), the `messageReceived` signature, Storage's call arguments: code against the installed
  version and write down which one each rule applies to — don't delete the old guidance while users
  still run it. When running a fork of an upstream module, keep a written diff against upstream
  (what we patch, why, what upstream changed since) so moving back is a plan, not archaeology.

### 12. Ship every platform Basecamp ships, when it's cheap
Desktop modules go out as **one package per module with a variant per platform**: Linux x86_64,
Linux ARM64 and macOS Apple Silicon, built on CI runners and merged
(`logos-multiplatform-modules`). Flake inputs must therefore be fetchable (GitHub refs, not
local paths). Say plainly which platforms are untested on real hardware.

### 13. Keep docs, ADRs, and this charter current
- **ADRs** (MADR style: Status/Date/Context/Decision/Rejected/Consequences, a `0000` index)
  for every load-bearing decision — like Loam/Shrooms. Write the ADR when the decision is
  made, not later.
- Keep the wire-contract doc current with every envelope/kind change; use a `docs/spikes/`
  dir for prototypes.
- When a rule here changes or a new pattern proves out, update this skill so the next app
  inherits it.

### 14. Design for a message budget (RLN)
Logos Messaging rate-limits publishers: a membership allows **100 messages per 10-minute epoch,
per node** — shared by every app on that node, and every segment and retransmit counts. On
delivery v0.3.0 a node without a membership receives but cannot send.
- *Why:* a sync design that replays the log message-by-message on every join or answers every
  request with a broadcast works on an empty network and stalls under RLN.
- *How:* count messages per user action in the design; catch up from a **snapshot** in Storage
  and reconcile only the tail; batch small events; answer catch-up requests per peer, throttled;
  keep blobs out of messages. The transport owns membership and pacing, the sync library owns
  snapshots and batching, the app owns payload size. Mechanics: `logos-rln-budget`.

### 15. Verify before claiming; push when confirmed
Reproduce via the user's actual path with the version-matched tool + a known-good comparison
before saying "fixed" or "works". Push to the remote as soon as a change is confirmed
working — don't leave confirmed commits local.

## Which sibling skill for which layer
- **Kickoff / scoping / "which layer is missing":** `logos-multiwriter-app-blueprint`.
- **The CRDT/fold/HLC/envelope/hub mechanics:** `logos-multiwriter-sync`.
- **SDS Reliable Channels (channelCreate/Send, the silent-failure gates):** `logos-reliable-channels`.
- **Desktop module (mkLogosQmlModule, `.rep`, headless hub, `.lgx`):** `logos-basecamp-module`.
- **Moving an app to Basecamp 0.3.x / builder 0.3.1 / delivery v0.3.0:** `logos-basecamp-0.3-port`.
- **Headless nodes and two-node test rigs on the 0.3 runtime (`logosctl`):** `logos-headless-logosctl`.
- **Mobile embed (JNI, config plugin, F-Droid, "phone receives nothing"):** `logos-mobile-app`.
- **Publishing artifacts to Basecamp/F-Droid repos:** `logos-publish-artifacts`; the F-Droid
  repo itself (keys, metadata, http repo_url, verifying the served index): `logos-fdroid`.
- **Attachments, media, log snapshots (Logos Storage):** `logos-storage`.
- **RLN message budget:** `logos-rln-budget`.
- **macOS / Linux ARM64 packages (CI build + merge):** `logos-multiplatform-modules`.
- **"syncs nothing / receives partially" debugging:** `logos-distributed-debugging`.
- **Adopt the shared node / bump loam-transport:** `loam-integrate-app`, `loam-update-app`.
- **Hardware/per-person identity:** `loam-keycard`.

## When to bend a rule
These optimize for household multi-writer, offline-first, privacy-preserving apps. A rule
bends when the app genuinely isn't that (a single-device tool, a public read-only feed) —
but say so explicitly and record why (an ADR), because the default is the rule.
