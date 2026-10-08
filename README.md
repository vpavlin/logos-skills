# logos-skills

Portable [Claude Code](https://claude.com/claude-code) **skills** for building **multi-writer, offline-convergent Logos/Waku apps** — the reusable spine behind a peer-to-peer, end-to-end-encrypted app with multi-device sync and cross-user sharing.

They are **generic playbooks** (any domain: shared calendars, activity trackers, collaborative notes, Q&A boards, ledgers — anything where two writers must converge without losing a change), distilled from a real end-to-end engineering effort and **verified against source**. The origin project appears only as footnoted evidence.

## The skills

| Skill | Covers |
|---|---|
| **logos-app-charter** | **The house rules** — the non-negotiable principles for apps in this ecosystem (desktop + Android at parity, build on Loam, local-first, event-log CRDT, zero-trust sealing, non-blocking plain-text views, matching versions, every Basecamp platform) and *why*, plus which skill owns each layer. |
| **logos-multiwriter-app-blueprint** | **Start here.** The zero-to-app recipe (`HANDBOOK.md`), decisions-up-front, build order, which skill per layer. |
| **logos-multiwriter-sync** | The data-model spine: event log + fold, HLC ordering, the three write-shapes (commutative delta / per-actor register / LWW), union merge, roles-on-merge, crypto + wire. |
| **logos-reliable-channels** | The SDS Reliable Channels transport: the receive-chain gates and the silent-failure fixes. |
| **logos-basecamp-module** | The desktop half: a universal `core` module + a thin QML `view`, `.lgx` build/publish, the builder-glue quirks, the always-on headless hub. Views call modules **asynchronously only** and render peer text as **plain text** (bundled `qml-plaintext.py`). |
| **logos-basecamp-0.3-port** | Move an app from Basecamp 0.2.x to **0.3.x / builder 0.3.1**, delivery fork → upstream v0.3.0, storage 2.x → 3.0: why the stack moves together, the phone-first wire shim (SegmentMessage), the per-module checklist (LIDL, 256² icons, onContextReady defer, default args), RLN-off-by-config, a separate test repo. |
| **logos-headless-logosctl** | Headless modules on the 0.3 runtime with **`logosctl`** (replaces logoscore/logos-hub): sessions, install/load/call/watch with typed args, logs, the host-owned Storage node, always-on hubs, two-node test rigs. |
| **logos-multiplatform-modules** | Ship modules for **macOS Apple Silicon and Linux ARM64** without the hardware: build each platform on GitHub runners from pinned refs (bundled workflow), merge into one package per module with existing platforms proven byte-identical (bundled `lgx-merge-platforms.sh`), republish the same version. |
| **logos-mobile-app** | The phone half: React Native + `liblogosdelivery` JNI, building the arm64 lib, the `expo prebuild` template trap, Core vs Edge, Hermes traps, release-build OOM, and what delivery v0.39 changes on phones. |
| **logos-distributed-debugging** | The methodology: triage the cheap causes first (versions, repo, a known-good hub as oracle), walk the layered chain, instrument-and-measure, same-event-two-listeners, verify-via-the-real-path, on-device timing instrumentation for phone-only bugs, and fixed-length stalls = a blocking call hitting the IPC timeout. Error-handling contract: `logos-basecamp-module`. |
| **logos-publish-artifacts** | Ship built artifacts to self-hosted repos: `.lgx` → a Basecamp package repo, APK → an F-Droid repo. Encodes the silent traps (PORTABLE-not-`-dev`, required F-Droid metadata, `CurrentVersionCode` pinning, and the **index-v1-vs-index-v2/entry.json staleness** that strands releases). Bundled `publish.sh` does both halves + verifies the indexes regenerated together; `publish-public-basecamp.py` updates a public catalog's two surfaces additively, including same-version republishes. |
| **logos-fdroid** | The self-hosted **F-Droid** repo in depth: two keys, metadata or an empty index, never pin `CurrentVersionCode`, **http `repo_url`** + fingerprint link/QR, `uses-feature required=false`, keeping old versions, public vs private repos, verifying the served index. |
| **logos-storage** | **Logos Storage** for attachments, media and log snapshots: CIDs + the two-id rule, the 2.x → 3.0 API, the Basecamp-0.3 host-owned node, NAT and the reachable-hub cache, address traps, mobile fetch-only. |
| **logos-rln-budget** | Designing under **RLN**: 100 messages / 10 min per node shared by all apps, what counts, who owns pacing vs snapshots vs payload size, membership vs RLN-off-by-config on delivery v0.3.0, open questions. |
| **logos-app-review** | **Reviewing someone else's app.** Code review at the trust boundaries, sync-correctness checks (fold order-independence, signature parity, roles in the fold), metadata-privacy checks, a Basecamp "check these first" list, one probe per finding, a real Basecamp GUI test (one and two instances, isolated Xvfb, no `pkill -f`), a phone test, a fix-verification round, and a publish-with-consent step. Ships `report-template.html` and `offline-run.sh` (run an app with loopback only). |
| **loam-keycard** | **Hardware & multiple identities.** A Status Keycard (NFC) as a per-person signer — on-card tap-per-sign, the choppu RN stack, the three sig adapters + the silent traps (`res.data.cbFuncResponse` nesting, reader-wedge, `buffer` bundle break); multiple authoring identities bound per container; one card = one identity across phone (NFC) + desktop (PC/SC) via `domainToSignPath`; `signaturesRequired`; custody/delegation; desktop signing. |
| **loam-integrate-app** | Integrate a React-Native app as a **client of the device-wide Loam shared delivery node** (many apps → one Waku/Logos node): the `preferServiceBackend` ordering, service binding, the approval prompt, and the "shared enabled but runs its own node" gotchas. |
| **loam-update-app** | Move a Loam mobile app onto a newer `loam-transport` SDK: bump the submodule, rebuild the release APK, publish — encoding the build traps (`expo prebuild --clean` wiping `local.properties`, the shim entry-file import, submodule realign). |

## Install

Claude Code loads skills from `~/.claude/skills/`. Clone and copy (or symlink) the skill directories:

```sh
git clone https://github.com/vpavlin/logos-skills.git
cp -r logos-skills/logos-* logos-skills/loam-* ~/.claude/skills/   # or: ln -s per dir
# updating later: git pull, then copy again (local edits to a skill are overwritten)
```

They then load automatically in every project. Read `logos-multiwriter-app-blueprint/HANDBOOK.md` before cutting the first line.

**Presenting this?** [`PRESENTING.md`](PRESENTING.md) is a twelve-point "how we build apps on Logos" outline, each point linked to the skills that hold the detail.

## Provenance & validation

Each skill was drafted from a real session (transcript + code + engineering notes), then **adversarially verified against the actual source**, deduped to a single canonical owner per topic, and put through a demanding style/correctness critic. The set was then **validated by rebuilding a *different* app (a Q&A board) from the skills alone** — the convergence property test passed **7/7** (200 trials × 4 devices, shuffled arrival orders + duplicate redelivery → identical folded state).

## License

Dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option — matching the Logos/Basecamp stack.
