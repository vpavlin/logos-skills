---
name: logos-rln-budget
description: "Design and run a Logos/Waku app under RLN (Rate-Limiting Nullifier) spam protection: every message a node publishes needs a proof from an on-chain membership, and a membership allows a fixed number of messages per epoch shared by every app on that node. Use when scoping sync traffic (how many messages does a join, an edit, a catch-up cost?), when a node on delivery v0.3.0 'receives but every send fails', when deciding RLN-on vs RLN-off-by-config for development, or when sizing an always-on hub. Covers the budget numbers, what counts (segments, retransmits), who owns what (transport vs sync library vs app), the levers (snapshots, batching, pull-not-push, pacing), the v0.3.0 membership modules and RLN-off configuration, and the open questions."
---

# Living within an RLN message budget

Logos Messaging uses RLN to rate-limit publishers without identities: a node registers a
**membership** (on-chain), and each message it publishes carries a zero-knowledge proof that it is
within its allowance for the current epoch. Traffic over the allowance is dropped and the relaying
peer is penalised. For a multi-writer app this turns "send freely, the network will cope" into a
**budget** the design has to respect.[^facts]

Status as of 2026-10: shipped but dormant in delivery library v0.38.1 (our 0.1.x fork); **on for the
`logos.test` preset in delivery_module v0.3.0** (library v0.39), with proof *validation* currently off
on the fleet. Check both before trusting any statement below — this is moving.[^state]

## The numbers

- **100 messages per 10-minute epoch per membership** (600 s epoch, hard cap `MAX_MESSAGE_LIMIT=100`).
  It's a quota, not a smooth rate: a burst of 100 then silence is fine.
- **Per node, not per app.** Every app riding one shared node (the Loam model: one node per phone,
  one per desktop) shares one budget.
- **Every wire message counts**: each segment of a large payload, and each SDS retransmission (up to
  ~5 per message). A 1-event edit can cost several.
- A hub serving many households on one membership is the sharpest risk: its catch-up answers are
  the biggest senders.[^facts]

## What happens without a membership (v0.3.0)

On `logos.test` a node with no RLN modules / no membership: bring-up of RLN fails (non-fatal), the
node **starts and receives** (validation is off), and **every send fails**. With a membership and a
spent quota, sends are parked and fail after `maxParkedAgeSec` (1800 s). Surface this in the app:
an RLN state in the status line, not a silently queued message.[^v03]

Membership on v0.3.0 needs two extra modules (`liblogos_rln_module` + `liblogos_lez_rln_module`, from
the official RLN catalog), a funded testnet wallet (one payer key can fund many nodes), and a minute
or three to activate. A membership can't be shared between nodes — copying the keystore leaks the
secret. In v0.39 the **sending node generates the proof**, so an Edge phone that publishes needs its
own membership (earlier designs assumed a service node would prove on the phone's behalf — not the
case on this version as read from source).[^v03]

## Developing with RLN off

Until memberships are routine, run development nodes with RLN disabled **by configuration**, behind a
flag that restores the preset later:[^v03]

- Either set the host process env `LOGOS_DELIVERY_RLN_PRESETS` to a file containing
  `{"logos.test":{"enabled":false}}`,
- or use `preset:""` and spell out the fleet parameters yourself (cluster 2, 8 shards, max message
  size, discovery, `reliabilityEnabled`, the entry nodes).

Both work only while the fleet doesn't validate proofs. Don't ship a release that depends on it
without saying so.

## Who owns what

| Layer | Owns |
|---|---|
| **Transport** (one shared node per device) | RLN config and membership; an outbound pacer that never exceeds the quota; retransmit tuning; a backpressure signal to apps |
| **Sync library** (event log + RBSR) | **Snapshots** (catch-up from Storage instead of replaying the log over messaging); batching several small events into one message; pull-not-push catch-up (answer a request, don't broadcast the log) |
| **App** | blobs in Storage, never in messages; a maximum payload size; showing "waiting to send" in the sync indicator |

The sync-library work is no-regret — it makes sync faster and cheaper with or without RLN — so do it
first.[^plan]

## Design rules for a new app

1. **Count messages per user action** — create, edit, join, catch-up — and multiply by segments and
   retransmits. Put the number in the design doc.
2. **Catch-up from a snapshot, then reconcile the tail** (`logos-storage` § snapshots). If each event
   travels as its own message, a join that replays 2 000 events needs 20 epochs (over 3 hours).
3. **Batch** small events into one envelope when several are authored together.
4. **Answer, don't broadcast.** Throttle catch-up answers per peer (e.g. one fingerprint reply per
   peer per 10 s) — store replays and live traffic both arrive as "messages" and can trigger
   answer storms.
5. **Keep payloads under the segment size** so one event = one message. New-library channel
   messages also carry a ~19 KB bloom filter; that's bandwidth, not count, but it matters on phones.
6. **Never block authoring on the send** (charter rule 3). A queued send is fine; a frozen UI is not.

## Open questions (ask the Logos team; record the answers here)

1. When does fleet validation turn on, and is it soft first?
2. Which chain do memberships live on long-term?
3. Will a service node ever prove on behalf of light (Edge) clients?
4. Is there a higher tier or multiple memberships for hubs?
5. Will the membership credential be settable through the messaging API (keystore path/password),
   or only via the RLN modules?

## Sources & evidence

[^facts]: Memory `rln-readiness-plan` (source-read of liblogosdelivery 0.38.1 `e91aaaa`: `rlnEpochSizeSec=600`, `MAX_MESSAGE_LIMIT=100`, per-membership budget, drop + gossipsub penalty, no slashing on the messaging fleet; SDS retransmits ≤ 5×, each metered). Design records: loam-transport ADR 0018 (RLN readiness), loam-sync ADR 0020 (snapshots).
[^state]: `loam-basecamp` `port/0.3` `docs/port-0.3/analysis-delivery.md` § 2 (module preset table `rln_presets.cpp:50-64`: on for `logos.test`, validation off, 600 s epoch) and `docs/delivery-upstream-vs-fork.md`.
[^v03]: `analysis-delivery.md` § 2: no RLN modules → bring-up Failed, non-fatal; no membership → receive works (`relay.nim:202-206`), every send fails (`publish.nim:63-74`); `maxParkedAgeSec` 1800; membership modules `liblogos_rln_module` 0.10.0 + `liblogos_lez_rln_module` 4.2.1, funding ≥ 2e8 testnet units, one payer key for many nodes, keystore not shareable; proofs generated by the sending client (no RLN-as-a-service via lightpush) — source-read, runtime UNVERIFIED; RLN-off options (a) env presets file (`rln_presets.cpp:125-186`) and (b) `preset:""` + explicit params. The ported transport ships option (b) behind an `rln` config key (memory `port-0-3`). The earlier RLN-as-a-service assumption: memory `rln-readiness-plan`.
[^plan]: Memory `rln-readiness-plan` ("Loam does RLN, apps do traffic-shaping"; workstream A — snapshots, batching, retransmit tuning, payload guard — no-regret). Catch-up answer throttling: memory `loam-shared-node-storesync` (qaku/scala throttle fp/whole-log answers). Bloom-filter size: `TESTING.md` § Known limitations.
