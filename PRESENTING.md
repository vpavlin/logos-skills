# How we build apps on Logos

A talk outline in twelve points. The spine is the [charter](logos-app-charter/SKILL.md); each point
links the skill that holds the detail, so the deck and the onboarding kit are the same material.
The apps behind it: a shared calendar, a Q&A board, a household budget, a contacts book, a run
tracker, and Loam — the shared transport they all ride on.

## 1. The problem

- Several people and devices edit shared state — a calendar, a budget, a Q&A room — often offline,
  with no server we want to run or trust.
- The network fails silently: a dropped message looks exactly like "nothing happened".
- "Last write wins" quietly loses someone's change; a central server reintroduces the thing
  Logos exists to remove.
- → [charter](logos-app-charter/SKILL.md), [blueprint](logos-multiwriter-app-blueprint/SKILL.md)

## 2. Local-first principles

- The device holds the data first and always; the network is an enhancement, never a precondition.
- Authoring never waits for the wire: append locally, update the UI, send in the background — and
  let reconciliation guarantee delivery.
- Every app works offline and converges later with no lost writes and no coordinator.
- → [charter rules 3–4](logos-app-charter/SKILL.md), [multiwriter-sync](logos-multiwriter-sync/SKILL.md)

## 3. The event-log data model

- Never mutate a record, never store derived state: immutable events, state = a pure fold over
  the merged log, merge = union by id.
- Edits supersede (LWW by hybrid logical clock), deletes are tombstones, counters are per-actor
  registers or commutative deltas.
- Convergence is a test we run: hundreds of shuffled arrival orders with duplicates → identical
  state.
- → [multiwriter-sync](logos-multiwriter-sync/SKILL.md)

## 4. Zero-trust crypto and Keycard

- One shared secret per room (QR + word fingerprint); topic, keys and nonces derive from it, so
  the wire and every store see only ciphertext.
- Byte-identical sealing and signing across TypeScript and C++, guarded by golden vectors —
  including the "undefined vs null" canonicalisation trap.
- A person, not a device, can sign: a Status Keycard over NFC on the phone and PC/SC on the
  desktop gives one identity on both.
- → [multiwriter-sync](logos-multiwriter-sync/SKILL.md), [loam-keycard](loam-keycard/SKILL.md)

## 5. The transport: Delivery, SDS, RBSR — and Loam

- Logos Delivery (Waku) carries sealed bytes; SDS Reliable Channels add ordering, gap detection and
  retransmit; app-level range-based reconciliation (RBSR) handles cold start.
- Loam runs **one** delivery node per device for every app (an AIDL service on Android, a
  `loam_core` module on desktop), with a BLE mesh bearer for no-network moments.
- Five silent failure modes live here (subscribe vs channelCreate, the encryption provider, the
  wire marker, base64 depth, echoes) — memorise them.
- → [reliable-channels](logos-reliable-channels/SKILL.md), [loam-integrate-app](loam-integrate-app/SKILL.md), [loam-update-app](loam-update-app/SKILL.md)

## 6. Two front ends, one engine (plus a hub)

- Desktop: a Qt-free core module owns everything; a thin pure-QML view renders its JSON, calls it
  asynchronously only, and shows peer text as plain text.
- Android: React Native with the delivery library over JNI (or the shared Loam node).
- The same core runs headless as an always-on hub — availability, not authority.
- → [basecamp-module](logos-basecamp-module/SKILL.md), [mobile-app](logos-mobile-app/SKILL.md), [headless-logosctl](logos-headless-logosctl/SKILL.md)

## 7. Swappable infrastructure

- Transport, sync and blob storage sit behind seams, so an embedded LAN hub can become Logos
  Storage, or Waku can gain a BLE bearer, without touching app logic.
- Logos Storage for attachments and log snapshots: content ids, a local handle first, a reachable
  hub that caches for NAT-ed users.
- → [charter rule 7](logos-app-charter/SKILL.md), [storage](logos-storage/SKILL.md)

## 8. Shipping

- Desktop: portable `.lgx` packages in self-hosted and public Basecamp repos; Linux, Linux ARM64
  and macOS built on CI runners and merged into one package per module.
- Android: self-hosted F-Droid — metadata or nothing, never pin `CurrentVersionCode`, http
  `repo_url` plus fingerprint, the app key is forever.
- Verify from the served index, not from `dist/`.
- → [publish-artifacts](logos-publish-artifacts/SKILL.md), [fdroid](logos-fdroid/SKILL.md), [multiplatform-modules](logos-multiplatform-modules/SKILL.md)

## 9. Debugging distributed silence

- Triage first: what's installed, did something upgrade it, does a known-good hub see traffic?
- One counter per pipeline stage and no bare `return`s; two listeners on one event to find the
  failing layer.
- Can't reproduce it? Instrument the real path on the phone and have the user paste one line.
- A fixed-length stall is a timeout: find the blocking call.
- → [distributed-debugging](logos-distributed-debugging/SKILL.md)

## 10. Living on a moving platform: the 0.3 port

- Basecamp 0.3 changed the runtime, builder, packaging, headless tool and the bundled delivery and
  storage modules at once — the whole desktop stack had to move together.
- Phones first: a wire shim so old and new delivery libraries understand each other before any
  desktop upgrades.
- A user's Basecamp silently "upgraded" our fork to upstream overnight — version-scope everything
  and keep test builds in a separate repo.
- → [basecamp-0.3-port](logos-basecamp-0.3-port/SKILL.md), [basecamp-module § 0.3.x](logos-basecamp-module/SKILL.md)

## 11. What's next

- RLN: 100 messages per 10 minutes per node, shared by every app — snapshots, batching and
  pull-not-push are the levers.
- Snapshots in Storage as the standard catch-up path; the phone delivery library on v0.39.
- Desktop BLE, macOS testing on real hardware, Windows once the stack is on builder 0.3.
- → [rln-budget](logos-rln-budget/SKILL.md), [storage](logos-storage/SKILL.md), [multiplatform-modules](logos-multiplatform-modules/SKILL.md)

## 12. The skills repo as the onboarding kit

- Every rule here was paid for with a real bug; each skill footnotes the evidence.
- A new app or a new agent starts from the charter and the blueprint and loads the layer skill it
  needs.
- The set was validated by rebuilding a different app from the skills alone (convergence test 7/7).
- → [README](README.md), [charter](logos-app-charter/SKILL.md)
