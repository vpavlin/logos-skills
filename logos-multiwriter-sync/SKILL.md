---
name: logos-multiwriter-sync
description: "Playbook for multi-writer, offline-convergent sync in a Logos/Waku app: event-sourced CRDT fold, HLC ordering, commutative deltas, an app-level AEAD envelope carried over SDS Reliable Channels, and a hub+RBSR backfill path — with the two hub gotchas that make it connect yet ingest nothing."
---

Playbook for giving a Logos/Waku app **multi-writer sync**: several devices or people edit shared state while offline and must converge to identical state **without any write being lost** — and, when needed, share one dataset across users with roles. Works for a shared calendar, an activity tracker, collaborative notes, a household ledger, a Q&A board — anything where "last write wins" would silently drop someone's change.

The whole design is one idea: **never store derived state, never mutate a record. Every change is an immutable event; state is a pure deterministic fold over the merged event log.** Get that right and offline-merge, idempotent redelivery, and convergence come for free.

---

## The 7 load-bearing decisions

### 1. Event log + fold, not mutable rows
Every change is an append-only event; current state is recomputed by folding the log. Merge of two replicas = **union of events by `id`** — nothing is overwritten, so no concurrent value is ever lost.[^1]

```
Event = { v:1, id:UUIDv4, type, hlc:{wall,ctr,dev}, dev, payload }
```
- `id` (client UUIDv4) is the **idempotency key** — redelivering an event is a no-op (dedup on `id`).[^3]
- Merge is idempotent + commutative + associative ⇒ arrival order is irrelevant.[^1]

```js
// mergeEvents(...logs): union by id, then sort by HLC. Pure. [^1]
const byId = new Map();
for (const log of logs) for (const e of log) if (!byId.has(e.id)) byId.set(e.id, e);
return [...byId.values()].sort((a, b) => compareHlc(a.hlc, b.hlc));
```

**Edit = superseding event. Delete = sticky tombstone.** Never touch the original event. The fold reconstructs each record as `create + field-scoped edits (LWW by HLC) + terminal delete`:[^4][^18]
- Concurrent edits to *different* fields both survive (key-scoped supersede); same-field conflict resolves LWW by HLC — deterministic, not lost silently.
- Make edit **lenient**: a `txn.edit` may legitimately arrive *before* its `create` — don't gate on local existence, just merge; the create landing later still folds correctly.[^18]
- Delete is terminal — the fold never un-sets `deleted`, so a late edit can't resurrect a tombstoned record.[^4]

### 2. Pick the right write-shape: three buckets, not two
Every mutable field falls into one of **three** shapes. Classifying each field up front is the single most important modeling step — get it wrong and you either lose writes (LWW where you needed accumulation) or corrupt counts on redelivery (raw delta where you needed a register).

1. **Commutative delta** — for a true accumulator multiple writers touch concurrently. `delta` op → `acc[key] += amount`; a `move`-style op is two legs summing to zero (`from -= x; to += x`), net-neutral and additive. Never LWW these.[^1][^5]
2. **Per-actor register** — for "counters that are really *who did it*": an upvote, a like, a poll vote, an RSVP, a reaction, a read-receipt. Model as a map **keyed by actor** of an LWW-by-HLC value (`present`/`absent`, or the chosen option); the displayed count/tally is the **aggregate** (`count = distinct actors whose value is present`). This is the reusable *social* pattern, and it beats a raw `+1` delta because it is **idempotent under redelivery** (re-seeing the same actor's vote can't double it) and **toggle-safe** (un-vote is just the actor's register going `absent`). Two different actors voting concurrently both survive; the same actor voting twice is one vote.
3. **LWW-by-HLC** — for genuinely single-valued fields where "latest wins" is correct: a title, a flag, an accepted-answer pointer, a config value. Resolve ties by HLC.

Edits/deletes (rule 1) are a special case of #3 layered over the append-only record.

### 3. HLC + UUID for a deterministic, replica-identical order
Every event carries a Hybrid Logical Clock `{wall, ctr, dev}`. Total order is `wall → ctr → dev`, identical on every replica; UUID dedups.[^2][^3]

```js
compareHlc(a,b): a.wall-b.wall || a.ctr-b.ctr || (a.dev<b.dev?-1:a.dev>b.dev?1:0)  // [^2]
```
- `Clock.send()` stamps a local event; `Clock.receive(remoteHlc)` advances the clock past an ingested event's cause. Call `receive` for every event you ingest, and prime the clock from your whole log on load — else a device authors events that sort *before* causes it already saw.[^2][^11]
- **Wall time is for ordering only — never for money/quantities.**[^2]

### 4. Invariants are surfaced, not enforced at merge
Global correctness properties (a balance that should sum to zero, a count that shouldn't exceed N) **cannot** be enforced from local state — two offline writers can each break them. Compute them as an **oracle after the fold**, assert `diff === 0` in tests across random convergence runs, but **never block, clamp, or drop an event to satisfy them** at runtime. A violating state is a valid, displayed condition a human resolves.[^6]

```js
checkInvariant(state) → { ...sides, diff, ok: diff === 0 }   // test oracle + display check [^6]
```

**Choosing your oracle.** Not every domain has a conservation law to sum to zero — that shape fits ledgers/inventory. When there's no sum, use a **cardinality or referential** oracle instead, so the convergence test still has a hard assertion: e.g. *aggregate count equals the number of distinct actors with a present register* (rules out double-counted votes), *no record references a parent id that isn't in the fold* (referential integrity), or *at most one field is in the "accepted/primary" state*. Pick the invariant that a bad merge would violate; if you can't name one, your convergence test can still assert **state-equality across shuffled orders**, which is the real gate.

### 5. Integers, never float, for any quantity that must be exact
Store money/quantities as scaled integers (e.g. milliunits = value×1000). No float in storage, transport, or arithmetic; convert only at the UI edge. Assert `Number.isSafeInteger` on every value entering the fold.[^7]

### 6. Convergence is a test you actually run
Prove it: generate N devices' random offline edit streams, fold the union in **many shuffled arrival orders + duplicates**, assert identical state every time and the invariant holds. The reference suite runs 200 trials over 3 concurrent devices.[^8]

### 7. Roles enforced on merge (for cross-user sharing)
A dataset is single-owner until an opt-in `group.init` event; after that each event's **author = `hlc.dev`**, and the fold **admits** an event only if the author's role allows it (`member.*` needs an active admin; mutating events need admin/editor; viewers/non-members dropped).[^16] In an append-only p2p log you can't *prevent* a write — admission is a **deterministic filter folded from the same event set**, so it's order-independent and convergence still holds. This is enforcement-on-merge, and it is **attribution, not cryptographic authorization** (phase-1 trusts the author claim inside the shared key; per-member signatures + MLS re-key on removal are a later layer). The real privacy boundary is *which dataset a record lives in* — a private dataset = a key you never share.[^16]

---

## Transport over Logos Delivery

**Local safety lives in a transport-independent node; the transport just moves sealed bytes.**[^11] Keep a `SyncNode`-style object that owns the log and exposes:
- `append(event)` → merge locally, return sealed wire bytes to publish.
- `ingest(sealed)` → open → decode → dedup by `id` (return false if known) → merge → `clock.receive`.[^11]
- `backfill()` → re-seal the missing events for a peer that just joined (see hub, below).

**Wire envelope** — one event per message, JSON (events are ~200 B, far under Waku's 150 KB cap, so binary buys nothing; parity tests validate *semantics* not struct shape):[^10]
```
plaintext = { v:1, type:"EVENT", event:{…} }   // then sealed, then published
```

**Crypto — this skill's wire concern.** One 32-byte pre-shared key per dataset (shared out-of-band via pairing QR); the topic is *derived*, so it leaks nothing:[^9]
```
K       = HKDF-SHA256(secret, salt="…-pair-v1")
topic   = "/app/1/" + hex(HMAC-SHA256(K, "…/topic/v1|"+epoch)[0..15]) + "/proto"
Ke      = HKDF-SHA256(K, info="…/payload/v1")
payload = nonce(12) ‖ ChaCha20-Poly1305(Ke, nonce, plaintext, aad=topic)
```

**Carry those sealed bytes on SDS Reliable Channels, not raw relay** — they give ordering, gap detection, retransmit and causal history *inside* the delivery layer (mature, 30+ releases).[^13] The channel API surface (`channelCreate`/`channelSend`/`onChannelMessageReceived`, `channelId == contentTopic == derived topic`, `senderId == deviceId`), the load-bearing **no-op encryption provider** (SDS refuses a channel with no Encrypt provider, and the app already does its own AEAD above), the receive-chain, and the four silent-failure gates that otherwise make a joined channel decode nothing are the sibling skill's material — **see `logos-reliable-channels`**. This skill owns only what rides *inside* the channel: the AEAD envelope above.

### Backfill: no Store ⇒ you need a hub + set reconciliation
`liblogosdelivery` exposes **no Waku Store query on desktop**, so someone must be online to re-serve history. Two pieces:[^12][^13]

1. **An always-on headless hub** — the same core as a plain peer, run headless (no GUI); it holds the full log and re-serves on demand. It is an *availability* role, not a canonical authority (the log stays fully replicated).

   **Two non-obvious gotchas make a hub connect yet stay silently useless:**[^20]
   - **Receives nothing.** The delivery module emits its "message received" signal straight from the FFI callback thread; a headless host replicates cross-module events over **Qt Remote Objects, which silently drops any provider signal emitted off the object's Qt event-loop thread** (and can wedge the QRO link so later calls also stop). The hub then meshes and *sends* but ingests zero. Fixed host-side in **cpp-sdk `d77c3dd`** (marshal provider events onto the source thread); a hub built against an older SDK receives nothing. Separately, drive the hub's *own* delivery calls from a `QTimer` on the event-loop thread, never a `std::thread` (else `createNode` just hangs).
   - **Meshes with nobody.** A hub started from default node config has **no `entryNodes`** → it is isolated (`No peers for topic`) and never dials the fleet. Give it bootstrap/entry nodes + `Restart=always`.

   See `logos-basecamp-module` for module event/threading wiring and `logos-distributed-debugging` for proving the hub actually meshed and ingested (peer-count gauges lie — trust the delivery host log).

2. **Range-Based Set Reconciliation (RBSR / Negentropy)** so backfill ships only the *missing* events, not the whole log: peers exchange 16-byte fingerprints over sorted id-ranges and recurse only where they disagree.[^12]

```
reconcile(A, B, {threshold=8, buckets=16}) → { aNeeds:[id], bNeeds:[id], rounds, controlBytes }
// order events by (hlc.wall, id); fingerprint = XOR of per-id SHA-256, folded with count, first 16 bytes
```

---

## Silent failure modes (symptom → cause → fix)

| Symptom | Root cause | Fix |
|---|---|---|
| Channel joined, traffic on relay, but nothing decodes (`ours: 0`); or decrypt fails across peers; or the core receives a message but the payload is empty/garbage | Channel receive-chain gates: `channelCreate` builds the listener but doesn't subscribe the content topic; the double-base64 payload convention; the delivery FFI `{"_bytes":…}` wrapping; the load-bearing no-op provider; and a synchronous/off-thread delivery IPC call deadlocking the caller | **See `logos-reliable-channels`** — it is the canonical owner of the channel receive-chain and all four silent-failure gates. This skill stops at the sealed AEAD bytes handed to the channel |
| A module method silently no-ops / never fires | The module glue generator **drops methods with >4 args** and methods with **trailing `//` comments**; a numeric CLI arg typed as `int` into a `QString` slot also silently no-ops | Pass a change-set as **one JSON string**, keep ≤4 params, strip trailing comments; build test JSON in C++ not via CLI `-c`[^19] |

For hub-specific silent failures (receives nothing / meshes with nobody) see the two gotchas under *Backfill* above.

---

## Parity: one reference, mirrored implementations
When the same fold runs in two languages (e.g. a JS reference + a C++ core), define it **once** as the reference and guard the port with **golden-vector fixtures + a cross-language parity test** — validate logic, not just the codec.[^17] The reference here keeps its C++ mirror byte-identical (24/24 parity), down to the reconciliation fingerprint.[^17][^12]

## Do-this checklist for a new app
- [ ] Event = `{v,id(UUIDv4),type,hlc,dev,payload}`; typed constructors document each payload.[^3]
- [ ] `mergeEvents` = union-by-id + HLC sort; fold is pure, no I/O.[^1]
- [ ] Edits supersede (field-scoped, LWW-by-HLC); deletes are sticky tombstones; edit is lenient about ordering.[^4][^18]
- [ ] Concurrent numeric writes = commutative deltas / net-zero moves, **never** LWW.[^5]
- [ ] `Clock.receive` on every ingest; prime from the whole log on load.[^2]
- [ ] Integers only for exact quantities; assert safe-integer at the fold boundary.[^7]
- [ ] `checkInvariant` oracle asserted in a 200-trial shuffled-order convergence test; never enforced at merge.[^6][^8]
- [ ] Transport = app-level AEAD envelope carried on SDS Reliable Channels (channel mechanics, no-op provider, receive gates → `logos-reliable-channels`); hub + RBSR for backfill, minding the two hub gotchas.[^13][^12][^20]
- [ ] Cross-user sharing: roles admitted deterministically on merge; privacy boundary = which key/dataset.[^16]

## Where else this applies

The origin is a household-budget app, but nothing here is finance-specific — the subject is the sync architecture. The same nine-item checklist drops onto any Logos/Waku app with concurrent writers or shared datasets:

- **Shared calendar / activity tracker:** events = `event.create/edit/delete`; a "move to another day" is a superseding edit (LWW-by-HLC per field); an RSVP count is a commutative delta so two people confirming offline both stick; the invariant oracle checks "no double-booked slot" as a surfaced warning, never a merge block.
- **Collaborative notes / docs:** each keystroke-batch or block op is an event; block ordering uses the HLC total order; a delete is a sticky tombstone so a late edit can't resurrect a removed block; RBSR backfills a device that was offline for a week without resending the whole doc.
- **Multiplayer Q&A / forum:** upvotes are commutative deltas; post edits supersede; roles (mod/member/viewer) are admitted deterministically on merge exactly like the group-sharing path; a private board is just a key you don't share.

Only two things are domain-specific and get swapped out: the **payload schemas** (the `type`/`payload` table) and the **invariant** you assert. The event-log + fold + HLC + commutative-delta skeleton, the AEAD-envelope-over-SDS-channel transport, and the hub/RBSR backfill (with its two hub gotchas) are identical across apps. The channel *mechanics* themselves live in the sibling `logos-reliable-channels` skill and are shared verbatim by every Logos app that syncs.

## Sources & evidence

All paths are repo-relative to `github.com/vpavlin/kym` (origin project, used here as evidence only).

[^1]: `packages/engine/src/engine.mjs` `mergeEvents` (union by `id`, HLC sort; idempotent/commutative) + `docs/decisions.md` #1 (two-layer append-only ledger, "shipped, proven 200-trial convergence"). Proves merge = conflict-free union. Provenance: memory `kym-project`.
[^2]: `packages/contract/src/hlc.mjs` — `Clock.send/receive`, `compareHlc` (`wall → ctr → dev`); comment "wall component is for ordering ONLY — never money". Proves the deterministic replica-identical order + the receive-on-ingest requirement.
[^3]: `packages/contract/src/events.mjs` — `makeEvent` returns `{v:1,id,type,hlc,dev,payload}`, `id=randomUUID()`; typed `ev.*` constructors. Proves the event shape + idempotency key.
[^4]: `packages/engine/src/engine.mjs` `reconstructTxns` — create-then-edit patch, `txn.edit` field-level supersede, orphan edit ignored, sticky `deleted` tombstone never un-set. Proves supersede/tombstone reconstruction + resurrection-proof delete.
[^5]: `packages/engine/src/engine.mjs` ASSIGN (`mode:'delta'` `+=`, `'set'` LWW) and MOVE (two legs sum-zero) + `docs/decisions.md` #1. Proves commutative-delta modeling vs LWW.
[^6]: `packages/engine/src/engine.mjs` `checkInvariant` (returns `{…,diff, ok:diff===0}`) + `docs/decisions.md` #3 ("surfaced, not enforced at merge") + `docs/data-model.md` §6. Proves the oracle-not-constraint rule. Provenance: memory `kym-invariant-asset-only-income`.
[^7]: `packages/contract/src/money.mjs` — integer milliunits, `assertMoney` = `Number.isSafeInteger`; `docs/decisions.md` #2. Proves integers-never-float.
[^8]: `packages/engine/test/convergence.test.mjs` — 200 trials, 3 devices, shuffled arrival + 5 redelivered dupes, asserts identical balances/available/RTA and `invariant.ok`. Proves convergence is an executed test.
[^9]: `packages/sync/src/crypto.mjs` — `deriveIdentity` (HKDF `K`/`Ke`), `topicFor` (HMAC-SHA256, first 16 bytes → `/kym/1/<hex>/proto`), `seal`/`open` (ChaCha20-Poly1305, `nonce(12)‖ct`, `aad=topic`); `docs/data-model.md` §8. Proves the PSK/derived-topic/AEAD scheme.
[^10]: `packages/sync/src/wire.mjs` — `encodeEvent`/`decodeEvent`, `{v:1,type:"EVENT",event}`; `docs/decisions.md` #6 (JSON not protobuf, ~200 B vs 150 KB cap). Proves the envelope.
[^11]: `packages/sync/src/node.mjs` `SyncNode` — `append` (merge + seal), `ingest` (open→dedup→merge→`clock.receive`), `backfill` (re-seal whole log). Proves the transport-independent node boundary.
[^12]: `packages/sync/src/reconcile.mjs` — `reconcile(A,B,{threshold=8,buckets=16})→{aNeeds,bNeeds,rounds,controlBytes}`, order by `(wall,id)`, 16-byte XOR-of-SHA256 fingerprint; C++ mirror `kym_core/src/kym_reconcile_std.hpp` (`threshold=8, buckets=16`); `docs/decisions.md` #17; 6 tests in `packages/sync/test/reconcile.test.mjs`. Proves RBSR params + no-Store rationale.
[^13]: `docs/decisions.md` #22 (SDS Reliable Channels: `channelCreate`/`channelSend`/`onChannelMessageReceived`, `channelId==contentTopic==derived topic`, `senderId==deviceId`, no-op encryption provider) + `kym_core/src/kym_core_impl.cpp` (`channelCreateAsync`, `onChannelMessageReceived`). This skill cites the channel material only to hand it off to `logos-reliable-channels`, which owns the full receive-chain + gates. Async-delivery + hub-role provenance: memories `kym-stuck-buttons-async-delivery`, `kym-libchat-vs-reliable-channels`, `kym-hub-runner`.
[^16]: `packages/engine/src/engine.mjs` `admitEvents` (group gating: `member.*`→admin, mutating→admin/editor, viewers dropped; order-independent fold) + `docs/decisions.md` #16 & #21 (attribution ≠ enforcement; privacy = which dataset/key) + `docs/data-model.md` §10. Provenance: memories `kym-multibudget`, `kym-tx-edit-attribution`.
[^17]: `docs/decisions.md` #5 ("TS reference, C++ mirror, golden vectors + parity, 24/24") + `kym_core/src/kym_engine.hpp` (`compareHlc`, `admitEvents`, `checkInvariant` mirrors) + `kym_reconcile_std.hpp`. Proves the parity discipline.
[^18]: `docs/decisions.md` #20 — edit/delete are append-only supersede/tombstone, `editTxn` lenient (no local-existence gate), delete terminal, concurrent edits LWW-by-HLC key-scoped. Provenance: memory `kym-tx-edit-attribution`.
[^19]: `docs/decisions.md` #20 (API note: glue silently no-ops a method with too many args; pass a change-set as one JSON string) + #18 (generated dependency-caller only surfaces action-style names). CLI int/quote mangling + >4-arg drop provenance: memory `logoscore-cli-arg-mangling`, `logos-dev-notes.md`.
[^20]: Headless-hub gotchas. Under a headless host, cross-module events replicate over Qt Remote Objects, which **silently drops a provider signal emitted off the Qt event-loop thread** — the delivery module emits `messageReceived` from its FFI callback thread, so the hub meshes and sends but ingests 0 (and the QRO link can wedge). Root-caused with the 2-module reproducer `github.com/vpavlin/logoscore-event-repro`; fixed host-side in **logos-cpp-sdk `d77c3dd` (PR #68, "marshal provider events onto the source thread")** and confirmed locally (old SDK → 0 events, new SDK → background-thread emit delivered). Default node config has **no `entryNodes`** → hub isolated (`No peers for topic`); needs bootstrap/entry nodes + `Restart=always`/enable-linger. Also: drive the hub's own delivery calls from a `QTimer` on the event-loop thread, not a `std::thread`, or `createNode` hangs; and `getNodeInfo("Metrics")` peer gauges read 0 even when connected — trust the delivery host log. Provenance: memories `kym-headless-hub`, `kym-hub-runner`, `kym-stuck-buttons-async-delivery`.
