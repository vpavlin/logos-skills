---
name: logos-distributed-debugging
description: "Use when a Logos Delivery / Waku multi-writer sync app \"syncs nothing\", receives partially, or hangs, and a single silent `return` hides which stage failed. A method playbook: triage the cheap explanations first (versions, which repo/build is installed, a known-good peer as oracle), then seven moves to localize a failure across the layered receive/reconcile chain (instrument each boundary, listen twice on one event, fingerprint before blaming the network, correlate both directions, read metrics without trusting zero, verify on the real path), plus on-device timing instrumentation for bugs that only reproduce on a phone and the fixed-duration-stall = timeout rule for hangs (blocking IPC, a module calling another from inside its own event callback). Transport facts (channel API, subscribe-by-content-topic, the silent-failure gates, self-echo, RBSR) live in the sibling skill logos-reliable-channels; this skill is how you find where the pipeline stops."
---

Sync over Logos Delivery (Waku relay + SDS Reliable Channels) fails **silently**: every drop is a bare `return`, so "no peer on my topic", "traffic arrives but decrypts to nothing", "the channel layer never fires", and "reassembly errored" all present identically — a UI that just says *not up to date*. This is a **method** playbook. It does not re-explain the transport; the channel API, subscribe-by-content-topic rule, the silent-failure gates, self-echo semantics, payload-encoding convention, and RBSR mechanics all live in **logos-reliable-channels**. This skill is how you *localize* a failure across the chain and prove where it stops, for any multi-writer Logos app (shared calendars, notes, trackers, budgets, Q&A).

**The chain** is always: transport callback → payload extracted → decrypted/authenticated with the room key → envelope parsed → deduped/folded — plus a **reconcile** half that decides *behind vs up-to-date*. A failure sits at exactly one boundary. The seven moves below find which — after Move 0 has ruled out the boring causes.

---

## Move 0 — Triage: rule out the cheap explanations first

Most "it's broken" reports in this ecosystem were not logic bugs. Spend five minutes on these before opening a debugger:

- **What is actually installed?** The version on the device/host, not the one you built. View and core are separate packages (a newer view calling a method an older core lacks returns the host's `{"error":"Invalid response"}`); a phone may still run last week's APK. Ask for, or read, the running version.
- **Did something upgrade it behind your back?** With several package repos added, Basecamp installs the **highest version across all of them** — a delivery fork at 0.1.4 was replaced by the official 0.3.x overnight and every app stopped syncing ("worked yesterday"). First question for "delivery connects, nothing syncs" on a desktop: which `delivery_module` version is installed, from which repo?[^t7]
- **Did the deploy copy what you built?** Nix build outputs all have mtime 1970, so `rsync -a` skips a changed file whose size didn't change — a hub kept an old `manifest.json` and plugin after two "successful" deploys. Deploy with `rsync --checksum` (or `cp`) and compare `md5sum` on both ends.[^t8]
- **Do the peers run compatible builds?** A hub one delivery version behind the clients meshed fine and still never converged (it couldn't reassemble their segmented catch-up). Compare delivery/transport versions on every node in the conversation.[^t1]
- **Did the publish reach the place the client reads?** Wrong repo, stale index, a CDN still serving the old index, a URL host the TLS cert doesn't cover. "Update not offered" is usually here (see `logos-publish-artifacts`).
- **Is the network down, or is this client off it?** One client's "no mesh peer" is one witness. Ask a known-good, always-on node (a headless hub): if *it* is relaying live traffic on the shard, the fleet is up and the failing client is the problem — usually its config (no `entryNodes`, wrong preset/cluster).[^t2]
- **Is it reproducible on the user's path?** GUI repo install ≠ CLI local install ≠ your harness. Reproduce the exact path, with the tool version matching their runtime, against a known-good reference (an official package that works) — or say plainly that you couldn't.[^t3]


- **Is the platform refusing, not your code?** A few log lines mean "not a sync bug": `auth token not recognized` (Logos 0.3 runtime rejecting a module's calls made from `onContextReady`, or a module built on the old builder), `Failed to create Delivery context` (config shape the installed delivery rejects — flat vs layered), a v0.3.0 node that **receives but every send fails** (RLN on, no membership — `logos-rln-budget`), `libQt6…: cannot open shared object file` (a library the host doesn't ship).[^t9]
- **Is your check itself lying?** A zero from a tool is only evidence if the tool could have found it: `strings | grep` misses UTF-16 strings in a Hermes bundle, a peer gauge reads 0 while sync flows (Move 6). Confirm the tool's output shape before rebuilding on the strength of a zero.

## Move 1 — Walk the layered chain

Write the stages down before touching a debugger. You cannot fix "sync is broken"; you can fix "stage 3 of 5". Every move below hangs off this list. Never reason about "sync" as one thing — reason about a specific boundary.

## Move 2 — Instrument and measure (one counter per stage)

Never collapse the receive path into a single boolean. Bump a distinct counter at **each boundary** and surface all of them together (debug JSON blob / status card / stderr). **The counters *are* the diagnosis.** Generic stages[^1][^2]:

- `raw` — the native transport callback fired **at all**. Climbs ⇒ relay/mesh is delivering *something*.
- `seen` — a message with a non-null payload was extracted.
- `opened` — that payload **decrypted+authenticated** with one of your room keys (AEAD: only the right key/topic wins).
- `new` / `dup` / `dropped` — folded a new event / already had that id / had no key for the topic or a bad envelope.

**Silent-failure rule:** replace every `return;` in the receive handler with `counter++; log(reason); return;` — no bare returns. And **wrap the whole receive callback in try/catch**: one malformed field from a peer, thrown through a delivery callback, otherwise takes the entire module down and freezes the UI with no error (every subsequent call hangs)[^2].

### Symptom → stage → cause → fix

| Counter state | Stage that failed | Cause | Fix |
|---|---|---|---|
| `raw`=0 | transport never fired | no peer on your shard / mesh not formed / not subscribed | check peer+mesh+shard (Move 6); subscribe the **content topic**, not a raw pubsub topic (see logos-reliable-channels) |
| `raw`>0, `seen`=0 | payload not extracted | FFI event shape differs from what you parse (nested / delivered as byte-array not string) | parse defensively; accept string **and** `number[]` **and** wrapper-object shapes[^5] |
| `seen`>0, `opened`=0 | decrypt failed for every candidate | wrong key/topic, or payload-encoding mismatch | verify AAD=topic + key; build **all** plausible sealed-byte candidates and let AEAD pick (encoding convention: see logos-reliable-channels) |
| framed>0, unwrapped=0 | transport ok, channel never unwraps | content topic not subscribed | see Move 3 + logos-reliable-channels |
| unwrapped>0, `opened`=0 | channel ok, decrypt fails | payload-encoding convention mismatch between peers | see Move 3 + logos-reliable-channels |
| errored>0 | SDS reassembly/decrypt rejected | gap / retransmit / missing encryption provider | read last-error text; provider is load-bearing (see logos-reliable-channels) |
| `new`=0, `dup`>0 | receiving fine, all duplicates | you already hold those events — convergence, not a bug | none |

## Move 3 — Same event, two listeners

To localize *which layer* drops a message, tally the **event type** at the ingress, not just a count. Attach both listeners to the same boundary and keep a by-type breakdown[^1]:

- **framed** — the raw transport event (still SDS-framed, won't open).
- **unwrapped** — the channel-unwrapped event (openable).
- **errored** — the error event (keep the last-error text).

The gap between the two listeners *is* the failing layer: `framed>0, unwrapped=0` ⇒ traffic reaches transport but the channel never unwraps it (subscription/channel gate); `unwrapped>0, opened=0` ⇒ the channel unwraps but decrypt fails (key/encoding). Both root causes and fixes live in logos-reliable-channels; your job here is only to say *which* one.

## Move 4 — Fingerprint before blaming the network

Counts say *how many*; a forensic sample says *what*. On the **first** event that failed to open — you rarely get a debugger on-device — capture one human-readable line[^1]: sender id, payload shape+length, how many decode candidates you tried, last decrypt error:

```
sid=<senderId8> pl=arr123 cand=2 openErr[<throw text>]
```

If `sid` equals **your own device**, you are seeing only **your own echo** — the peer's traffic isn't reaching you (or you're grepping the echo, not the ingress). That single field distinguishes "not connected" from "connected, wrong key" from "looking at the wrong stream" before you go blame the mesh. Self-echo / senderId semantics: see logos-reliable-channels.

## Move 5 — Correlate both directions

Live receive proves messages *flow*; it does **not** prove you are *caught up*. Derive a sync **phase** (`offline → fetching → asking → uptodate`) from range-based set reconciliation, not raw counts[^2] (RBSR mechanics: see logos-reliable-channels).

The method point is the **asymmetry**: a behind node only learns its own deficit if the **ahead** node replies with a summary. So a stuck "checking…" indicator is almost always fixed on the **serving** side — re-summarize whenever **either** side is behind (throttled, e.g. ≥2 s), so the behind peer receives something to diff against[^2]. **Instrument both directions**, or you will misread a serving-side gap as a receive failure and debug the wrong node. (Throttle the *display* separately; never throttle the reconcile itself.)

## Move 6 — Read peer/mesh health, but never trust zero

"Am I connected?" is answered by the node's metrics — with a hard caveat. Delivery exposes no direct peer count; pull the node-info Prometheus text and parse two gauges plus your shard[^4]:

- `libp2p_peers` — transport peers held.
- `libp2p_gossipsub_peers_per_topic_mesh` — peers in your gossip mesh (a nonzero peer count with an empty mesh still reaches no one).
- your **shard** (`/waku/2/rs/<cluster>/<shard>`, via regex) — compare against the shard your peers use; a mismatch means same protocol, different mesh, nothing crosses[^1].

> **CAVEAT — the peer/mesh gauge under-reports.** Sync has been observed working end-to-end while the peer gauge read **0**[^6]. Never treat a zero as ground truth for "offline". A **nonzero** reading confirms connectivity; a **zero** reading confirms *nothing* — cross-check with the receive counters (Move 2) before declaring a node disconnected. (This supersedes any older "mesh==0 ⇒ offline" heuristic.)

Fetch metrics **async** and parse in the callback: a synchronous node-info/`send` on the event-loop thread blocks until delivery replies — up to the IPC timeout — freezing every button (the "core module stuck" class of bug). The off-thread delivery-IPC deadlock is detailed in logos-reliable-channels[^4].

## Move 7 — Verify via the real path; re-read ambiguous signals

Prove a fix on the **real device/host through the actual path** — not an emulator, not a mock backend. A change that "works" only in the harness has verified nothing about relay, mesh, or on-device FFI shapes.

Then **re-read the signals that lie**:

- the peer gauge under-reports (Move 6) — 0 ≠ offline.
- self-echo looks like receive (Move 4) — `sid`=self is not "sync works".
- all-duplicates looks like failure but is convergence (`dup>0, new=0` is *healthy*).
- traffic arriving ≠ caught up (Move 5).

## Move 8 — Can't reproduce it locally? Instrument the real path on the device

When a phone bug ("takes 10 s", "stuck", "slow") doesn't reproduce on the dev box, **stop guessing**. Each plausible fix you ship blind costs a release and a user round-trip, and may improve something real while missing the cause.

1. Wrap each step of the suspect path in `Date.now()` deltas, finely enough that **one number** will point at the culprit (`write=[sign/seal/send] refresh=[load/fold/render]`).
2. Surface the result **on the device**: a toast, *and* copy it to the clipboard — a toast is too fast to write down, and the user should be able to paste the line straight back. A "copy diagnostics" button on a debug panel does the same for counters.
3. Ship a throwaway instrumentation build; the user reproduces once and pastes.
4. Fix the step the numbers name; strip the instrumentation; ship the clean fix.

Read the shape of the numbers: a **constant** duration (always ~10 s, ~20 s) points at a timeout; a duration that **grows with data** points at compute or IO (re-folding a log, scanning storage).[^t4]

## Move 9 — A fixed-length stall is a timeout: find the blocking call

A UI that freezes for a near-constant time (often **~20 s**, the cross-module IPC timeout), buttons that "stop working" and then recover, a hub heartbeat with ~20 s gaps — that's a **synchronous call waiting for a reply**, not a crash. No try/catch fixes it, because nothing throws. The known sources:

- **A view's blocking `logos.callModule`** while the core is busy → use `callModuleAsync` only (`logos-basecamp-module`).
- **A core's synchronous call into another module on the event-loop thread** (`send`, `getNodeInfo`): a lightpush attempt with no lightpush peers can stall for seconds → use the `…Async` variants and handle the result in the callback.[^t5]
- **A module calling another module from inside that module's own event callback.** Each call runs in the target, but the reply can't arrive while the callback is still on the stack, so every call waits out the full timeout — and events emitted meanwhile are **lost**, which can wedge a state machine. In the handler, only record state; defer the calls with `QTimer::singleShot(0, <QObject on the loop thread>, fn)` (a bare `singleShot(0, fn)` may never fire off-loop).[^t6]

Log timestamps around each call show the gap directly (`destroy :48 → init :09 → start :29` = three 20 s waits).

---

## Grep-able log markers (build them in from day one)

Prefix every probe with a fixed, unique **per-subsystem** tag (lifecycle / receive / send) so a noisy device log is greppable[^2]. On the receive path, emit at minimum: `open failed (<n> bytes) seen=… openFail=…`, `bad envelope: …`, `type=… seen=… opened=…`, the reconcile summary (`peer_items=… peer_needs=… sent=… we_lack=…`), `undecodable EVENT`, and `handler exception (message dropped, module stays alive)`. Adapt the prefix per app; the point is a stable, unique tag per subsystem.

## Payload-representation probe (send side)

Which in-process payload shape `send()`/`channelSend()` accepts (byte **array** vs plain **string** vs wrapper **object**) differs by sdk build. Don't hardcode it: **probe array→string once, cache the representation that didn't throw**, fall back if a cached one starts failing; symmetrically accept all three shapes on receive[^5]. (The wire-level encoding *convention between peers* is a transport fact — see logos-reliable-channels.)

## Checklist for a new app

1. Name the chain stages: `raw → seen → opened → new/dup/dropped`; expose them all.
2. Every drop = `counter++; log(tag+reason); return;` — no bare returns.
3. Wrap the whole receive callback in try/catch; one bad peer message must not kill the module.
4. Two listeners on the ingress (framed / unwrapped / errored) to localize the failing layer.
5. First-failure forensic sample: senderId, payload shape, candidate count, decrypt error.
6. Correlate both directions; fix a stuck indicator on the *serving* side.
7. Peer **and** mesh **and** shard from metrics — fetch async, and never read 0 as offline.
8. Verify on the real device/host through the real path, not a mock.

## Where else this applies

The chain is identical for any multi-writer Logos Delivery app; only the payload semantics change, and every move transfers unchanged:

- **Shared calendar / activity tracker:** same `raw→seen→opened→new/dup/dropped` counters (Move 2); an event that decrypts but whose type is unknown is a `dropped`, not a silent no-op.
- **Collaborative notes / a shared doc:** the reconcile phase (Move 5) drives the "syncing… / up to date" indicator, and the serving-side re-summarize is what unsticks a peer frozen on "checking".
- **P2P Q&A / forum over Waku:** the peer-vs-mesh-vs-shard read (Move 6) distinguishes "connected but posting into the void" (empty mesh / wrong shard) from a real key/encoding bug — while remembering that a 0 peer reading is not proof of offline.

The transferable moves, in order: walk the layered chain; one counter per stage with no bare returns and a try/catch around the whole receive callback; two listeners on one ingress to localize the failing layer; fingerprint the first failure (senderId self-echo vs peer, payload shape, decode candidates, decrypt error) before blaming the network; correlate both directions and fix a stuck indicator on the serving side; read peer/mesh/shard from metrics but never trust a zero and never block the event-loop thread; verify on the real device through the real path and re-read the signals that lie. All transport specifics (channel API, subscribe-by-content-topic, encoding convention, self-echo, RBSR, the off-thread deadlock) belong to **logos-reliable-channels**.

## Sources & evidence

All evidence is from the KYM project (a local-first p2p budget on Logos), used here purely as a worked example of the technique — never as the subject. Provenance memory files: `verify-before-claiming-fixed`, `kym-mobile-channels-receive-rootcause`, `kym-sync-root-cause-bytes-payload`, `kym-stuck-buttons-async-delivery`, `kym-sync-indicator`, `kym-senderid-and-shard`, `kym-mobile-channels-working`, `kym-headless-hub`, `kym-hub-runner`.

[^1]: `mobile/src/lib/delivery.ts` — the JS diagnostic harness. Per-stage counters `rxRaw/rxSeen/rxOpened/txSent/rxSample` exposed by `getRx()` (generalized in prose to `raw/seen/opened`); by-event-type tallies `dChan`(channel_message_received)/`dMsg`(raw message_received)/`dErr`/`dErrText` (generalized to unwrapped/framed/errored, Move 3); first-failure forensic string `dInfo` = `sid=<8> pl=<shape> cand=<n> openErr[<text>]` (Move 4); rolling sample `chan:${dChan} msg:${dMsg} err:${dErr} | ${dInfo}`; `getPeerCount()` parsing `libp2p_peers`/gossipsub-mesh and extracting the `/waku/2/rs/<c>/<s>` shard via regex (Move 6). github.com/vpavlin/kym/blob/main/mobile/src/lib/delivery.ts — proves the per-stage counter shape, the two-listener event-type breakdown, the forensic sample format, and the shard-compare probe.

[^2]: `kym_core/src/kym_core_impl.cpp` — the C++ core probes. `KYMRX/KYMTX/KYMDBG` `fprintf(stderr,…)` markers (generalized to per-subsystem tags); counters `m_rxSeen/m_rxOpened/m_rxOpenFail/m_rxNew/m_rxDup/m_rxDropped/m_txTotal` (generalized to seen/opened/new/dup/dropped); the whole `ingestRaw` handler wrapped in try/catch (`KYMRX handler exception … module stays alive`); the derived sync `phase` (`offline/fetching/asking/uptodate`) in the state JSON; the RBSR reconcile giving `d.aNeeds`/`d.bNeeds`, `b.missing = d.aNeeds.size()`, and the "re-summarize when either side behind, throttled ≥2000 ms" serving-side fix (`KYMRX SUMMARY peer_items=… peer_needs=… sent=… we_lack=…`). github.com/vpavlin/kym/blob/main/kym_core/src/kym_core_impl.cpp — proves the counter set, log markers, the try/catch survival rule, the phase derivation, and the serving-side re-summarize fix (Moves 2 and 5).

[^3]: `kym_core/docs/decisions.md` #22 and #23 — the SDS Reliable-Channels transport facts that this skill deliberately does **not** re-explain (they are owned by the sibling skill `logos-reliable-channels`): the channel API surface (`channelCreate`/`channelSend`/`onChannelMessageReceived`, `channelId==contentTopic==derived topic`, `senderId==deviceId`), the load-bearing no-op encryption provider, and the four independent silent-failure fixes (channelCreate does not subscribe → also `subscribeContentTopic`; the double-base64 payload convention; the no-op provider requirement; the Android/online-monitor deadlock). github.com/vpavlin/kym/blob/main/docs/decisions.md — the source for the `framed>0, unwrapped=0` (`ours:0`/`chan:6`) localization in Move 3 and the encoding-mismatch row of the Move 2 table.

[^4]: `kym_core/src/kym_core_impl.cpp` `refreshPeerCount()` + `parseAndApplyMetrics()`. Uses `getNodeInfoAsync("Metrics")` — explicitly async because a synchronous `getNodeInfo`/`send` blocks the event-loop thread up to the ~20 s IPC timeout (the "core module stuck" bug, memory `kym-stuck-buttons-async-delivery`; the off-thread deadlock itself is documented in `logos-reliable-channels`); parses `libp2p_peers` and `libp2p_gossipsub_peers_per_topic_mesh`. github.com/vpavlin/kym/blob/main/kym_core/src/kym_core_impl.cpp — proves the mesh-vs-peers distinction, the shard extraction, and the async requirement in Move 6.

[^5]: `kym_core/src/kym_core_impl.cpp` `bytesPayload()`, `deliverySend()`, and the `onMessageReceived`/`onChannelMessageReceived` `toWire` lambdas. Send probes array (repr 1, `bytesPayload`) → string (repr 2) and caches the winner (`m_sendRepr`), `KYM_SEND_ARRAY` forces array; receive accepts string, `number[]`, and `{_bytes: <base64>}` object shapes — the `{_bytes}` wrapper was the single root cause behind a class of "second peer received nothing" failures (memory `kym-sync-root-cause-bytes-payload`). github.com/vpavlin/kym/blob/main/kym_core/src/kym_core_impl.cpp — proves the probe-and-cache send rule and the accept-all-shapes receive rule (Move 2 table row 2, and the send-side probe section).

[^6]: The peer/mesh gauge under-report is an observed-behavior caveat, not a code assertion: mobile↔hub SDS channel sync was proven working end-to-end (memory `kym-mobile-channels-working`) and the headless hub ingested live edits (memory `kym-headless-hub` / `kym-hub-runner`) in runs where the parsed `libp2p_peers` gauge read 0. Hence Move 6 treats a zero reading as inconclusive and cross-checks the receive counters ([^1]/[^2]) rather than the older render-time `peers==0 ⇒ "no peers"` string in `refreshPeerCount` ([^4]).

[^t1]: Scala VPS hub, 2026-09-30: delivery_module 0.1.3 on the hub vs 0.1.4 on the clients; a calendar added to the hub stayed empty until the hub was upgraded. Memory `scala-vps-hub`.
[^t2]: 2026-08-07 qaku outage: phone `shard -, mesh 0` and a Basecamp "no mesh peer" on all shards while the always-on kym hub was relaying shards 0/2/4/7 — the fleet was up; `logos.dev` had moved to cluster 3 and new nodes dialled it with the cluster-2 preset. Fix: `preset: "logos.test"`. Memory `logos-fleet-health-oracle`.
[^t3]: KYM Basecamp variant issue, 2026-07-19: "fixed" was claimed three times from proxies (a dev store, `lgpm`) before reproducing the user's repo-install path with `lgpd` and diffing against an official package. Memory `verify-before-claiming-fixed`.
[^t4]: Scala mobile 14 s edit lag, 2026-09: two blind fixes missed; one instrumented build printed `refresh=13459 [cals=4161 evts=6726 alias=2572]` → a fold cache took it to ~1 s. Memory `on-device-timing-instrumentation`; copy-to-clipboard debug panels: `LoamDebug` (loam-transport).
[^t5]: kym_core 0.5.8: synchronous `send`/`getNodeInfo` from the snapshot poll froze every button; the hub heartbeat stalled ~21 s → async variants, max gap 4 s. Memory `kym-stuck-buttons-async-delivery`.
[^t6]: Scala core 0.9.32, storage restart inside `onStorageStop`: destroy/init/start each waited 20 s and the `storageStart` event was lost; deferring via `QTimer::singleShot(0, m_resyncTimer, …)` made it ~20 ms. Memory `module-calls-inside-event-callbacks`.
[^t7]: 2026-10-02: Basecamp (with the official catalog added) upgraded `delivery_module` 0.1.4 (fork) → 0.3.x (upstream); the `messageReceived` signature change plus RLN meant no message reached the transport. Workaround: reinstall 0.1.4; durable fix: version the fork above upstream. Memory `delivery-upstream-vs-fork`.
[^t8]: Scala VPS hub, 2026-09-30. Memory `nix-rsync-mtime-trap`.
[^t9]: `loam-basecamp` `port/0.3` `docs/port-0.3/analysis-basecamp-builder.md` (auth-token rejection), memory `scala-gui-delivery-flat-config` (layered config rejected by the 0.1.x fork), `analysis-delivery.md` § 2 (RLN, sends fail), memory `ble-mesh-qtbluetooth-bundle`; Hermes UTF-16: memory `hermes-utf16-string-grep`.
