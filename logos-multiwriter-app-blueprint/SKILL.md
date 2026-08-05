---
name: logos-multiwriter-app-blueprint
description: "Use when standing up a NEW multi-writer or cross-user-shared Logos/Waku app from zero — several devices or people edit shared state offline and must converge without losing writes — and you want the end-to-end recipe that ties the pieces together: a Basecamp core+view module, a React Native mobile app, and sync over SDS Reliable Channels. This is the INDEX/BLUEPRINT: it names the decisions to make up front, the order to build in, and which sibling skill (logos-multiwriter-sync, logos-reliable-channels, logos-basecamp-module, logos-mobile-app, logos-distributed-debugging) to load for each layer. Reach for it at project kickoff, when scoping the work, or when a half-built app \"syncs but syncs nothing\" and you need to see which layer is missing. Fits shared calendars, activity/habit trackers, collaborative notes, Q&A boards, household ledgers — anything where last-write-wins would silently drop a change."
---

# Logos multi-writer app — blueprint / index

Stand up a NEW multi-writer, offline-convergent Logos app: a **Basecamp core+view module** on the desktop plus an **RN/Expo mobile app**, all folding the same event log and syncing over **SDS Reliable Channels**. This skill is the map; each layer has a dedicated sibling skill. Depth lives in **HANDBOOK.md** — read it before you cut the first line.

## The one idea everything hangs off
**Never store derived state, never mutate a record.** Every change is an immutable event; current state is a pure deterministic fold over the merged log; merge is union-by-`id`. Get that right and offline-merge, idempotent redelivery, and convergence are free. Everything below is plumbing to move those events between replicas without losing any.

## Sibling skills — what each covers, when to load

| Load this | For | Load it when |
|---|---|---|
| **logos-multiwriter-sync** | The data model: event log + fold, HLC ordering, commutative deltas, union merge, roles/admission. The *what to send*. | First. Before any transport. Designing the event schema, delta ops, tenant model. |
| **logos-reliable-channels** | The SDS Reliable Channel transport: `channelCreate / channelSend / onChannelMessageReceived`, segmentation, ordering, gap-fill. The *how it travels*. | Wiring the wire. When a channel "syncs nothing", peers can't decode each other, or choosing SDS vs libchat vs raw relay. |
| **logos-basecamp-module** | The desktop half: a `core` (universal) module + a `ui_qml` view, the `callModule`/`onModuleEvent` bridge, `.lgx` packaging. | Building the desktop app and the always-on headless hub. |
| **logos-mobile-app** | The phone half: JNI bridge to the prebuilt `liblogosdelivery.so`, the config plugin that survives `expo prebuild`, cross-thread event delivery, F-Droid release. | Adding the phone. Symptoms: "phone receives nothing", "undefined is not a function", release SIGSEGV, node offline. |
| **logos-distributed-debugging** | Per-stage counters across the receive/reconcile pipeline; telling relay-down from wrong-key from channel-not-firing apart. | The moment anything "syncs nothing" or partially. Keep it open the whole build. |

## Build order (each step is testable before the next)
1. **Contract + engine + fold** (multiwriter-sync) — pure, in-process, no network. Convergence property test is the gate.
2. **Crypto + wire envelope** (multiwriter-sync) — seal/open, topic derivation.
3. **Reliable-channel transport** (reliable-channels) — two desktop cores converge over the wire.
4. **Basecamp core+view** (basecamp-module) — render the fold; wire edits through `callModule`.
5. **Headless hub** — same core, run standalone for always-on availability.
6. **Mobile** (mobile-app) — embed the node, join the same channel, converge with desktop + hub.
7. **Instrument throughout** (distributed-debugging) — counters from step 3 onward.
8. **Finalize & ship** (basecamp-module + mobile-app) — co-release core/view/mobile; build the `.lgx`s + APK; publish to the repo the client actually reads; tag a release with the artifacts; install from the *published* path on a clean client. Converging ≠ shipped — see HANDBOOK §Step 8.

## Decisions to make up front (don't defer these)
- **Event schema** — `{ v, id:UUIDv4, type, hlc:{wall,ctr,dev}, dev, payload }`. `id` is the idempotency key. Version field from day one.
- **Delta ops** — which mutations must be **commutative** (money moves, counters → net-zero two-legged deltas that sum) vs which can be **LWW-by-HLC** (renames, flags). Edits = superseding events; deletes = tombstones. Never an overwrite.
- **Tenant / group model** — one shared dataset = one 32-byte secret = one derived topic = one channel. Membership/roles (if any) as events folded with enforcement-on-merge; ACLs are attribution, not enforcement, in an append-only p2p log.
- **Transport** — SDS Reliable Channels (mature, ordered, gap-filling). Not raw relay, not libchat, unless you have a specific reason.
- **Crypto** — pre-shared secret → HKDF room key → HMAC content topic → AEAD (ChaCha20-Poly1305) with the topic as AAD. Sharing = handing over the secret (QR/code).
- **Cold-start history** — the channel does **not** backfill across restarts. Decide now: rebroadcast-on-join, store-query pull, or RBSR reconciliation.
- **Identity / senderId** — a stable per-device id, distinct from the tenant secret; it is the HLC tie-break and the channel `senderId`.

## The failure mode this whole stack exists to prevent
Sync that **works but syncs nothing** — every drop in the pipeline is a silent `return`. "No peer on my topic", "arrives but won't decrypt", "channel layer never fires", "reassembly errored" all render identically as *not up to date*. The counters in logos-distributed-debugging are non-optional; instrument from the first two-node test.

See **HANDBOOK.md** for the full zero-to-app recipe, per-layer plug-in points, and the cross-cutting pitfalls (payload double-encoding, view/core version skew, the `subscribeContentTopic` gate, arm64-only `.so`).
