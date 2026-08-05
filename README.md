# logos-skills

Portable [Claude Code](https://claude.com/claude-code) **skills** for building **multi-writer, offline-convergent Logos/Waku apps** — the reusable spine behind a peer-to-peer, end-to-end-encrypted app with multi-device sync and cross-user sharing.

They are **generic playbooks** (any domain: shared calendars, activity trackers, collaborative notes, Q&A boards, ledgers — anything where two writers must converge without losing a change), distilled from a real end-to-end engineering effort and **verified against source**. The origin project appears only as footnoted evidence.

## The skills

| Skill | Covers |
|---|---|
| **logos-multiwriter-app-blueprint** | **Start here.** The zero-to-app recipe (`HANDBOOK.md`), decisions-up-front, build order, which skill per layer. |
| **logos-multiwriter-sync** | The data-model spine: event log + fold, HLC ordering, the three write-shapes (commutative delta / per-actor register / LWW), union merge, roles-on-merge, crypto + wire. |
| **logos-reliable-channels** | The SDS Reliable Channels transport: the receive-chain gates and the silent-failure fixes. |
| **logos-basecamp-module** | The desktop half: a universal `core` module + a thin QML `view`, `.lgx` build/publish, the builder-glue quirks, the always-on headless hub. |
| **logos-mobile-app** | The phone half: React Native + `liblogosdelivery` JNI, building the arm64 lib, the `expo prebuild` template trap, F-Droid release. |
| **logos-distributed-debugging** | The methodology: walk the layered chain, instrument-and-measure, same-event-two-listeners, verify-via-the-real-path. |

## Install

Claude Code loads skills from `~/.claude/skills/`. Clone and copy (or symlink) the skill directories:

```sh
git clone https://github.com/vpavlin/logos-skills.git
cp -r logos-skills/logos-* ~/.claude/skills/      # or: ln -s per dir
```

They then load automatically in every project. Read `logos-multiwriter-app-blueprint/HANDBOOK.md` before cutting the first line.

## Provenance & validation

Each skill was drafted from a real session (transcript + code + engineering notes), then **adversarially verified against the actual source**, deduped to a single canonical owner per topic, and put through a demanding style/correctness critic. The set was then **validated by rebuilding a *different* app (a Q&A board) from the skills alone** — the convergence property test passed **7/7** (200 trials × 4 devices, shuffled arrival orders + duplicate redelivery → identical folded state).

## License

Dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE), at your option — matching the Logos/Basecamp stack.
