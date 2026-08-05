# Reliable Channel integration — per-platform checklist & walls

## Two integration shapes

| | Full node (in-process, e.g. desktop core) | Raw FFI / mobile embed |
|---|---|---|
| Content-topic subscription | Often implicit via the node's own subscribe/autoshard path | **You must call `subscribeContentTopic(topic)` explicitly** before `channelCreate` |
| `channelCreate(topic,topic,deviceId)` | required | required |
| Encrypt/Decrypt provider | `ReliableChannelManager.start()` installs no-op | same — but only if `start()` runs; verify it did |
| Store backfill on reconnect | via node `recv_service` | via node `recv_service` (kernel store API) |

The asymmetry in row 1 is the #1 cause of "one side syncs, the other receives nothing." When in doubt, subscribe explicitly on **every** platform — subscribing an already-subscribed topic is harmless.

## Join sequence (idempotent-safe)
```
subscribeContentTopic(topic)      // feed recv_service's subscribed set
channelCreate(topic, topic, id)   // build channel + ingress listener
```
On reconnect / renewal: re-run **only** `subscribeContentTopic`. Do **not** re-`channelCreate` — it would rebuild SDS session state.

## Send sequence
```
sealed = aeadSeal(appBytes, roomKey, aad=topic)   // your crypto, not the channel's
channelSend(topic, { payload: base64(sealed), ephemeral: false })
```
Use `ephemeral:true` only for data already persisted elsewhere (SDS repair rebroadcasts use ephemeral internally).

## Receive sequence
```
on onChannelMessageReceived({channelId, senderId, payload}):
    if senderId == self: skip            // your own echo
    for cand in payloadCandidates(payload):   // try single- AND double-base64
        if opened = aeadOpen(cand, roomKey, aad=topic): apply(opened); break
    // raw message_received copies (SDS-framed) will also arrive; open() rejects them harmlessly
```

## Silent-failure quick table

| Symptom | Cause | Fix |
|---|---|---|
| Channel exists, receives 0 (`ours:0`) | `channelCreate` didn't subscribe the content topic | add `subscribeContentTopic` |
| Sends never appear on wire; no error | no Encrypt provider | ensure `start()` ran / install no-op |
| Receives 0, or garbage after decode | double-base64 mismatch between peers | make receive try both depths; align send |
| Peer's plain `send()` invisible to channel (or vice-versa) | `meta` marker filter | one transport per topic |
| "Verified receipt" but it was your own echo | `senderId == self` | filter self before counting |
| Receives nothing on a hand-pinned shard | subscribed a non-existent shard | subscribe by content topic, let autosharding pick |

## What to build on top
- **App-level set reconciliation (RBSR / fingerprint exchange)** for cold-start and long-offline devices — SDS only heals within a session/disconnect window.
- **Your own AEAD sealing** — the channel is reliability, not confidentiality.
- **Membership** — a topic is open to anyone who knows it; gate the room key.

## Build walls (native embeds)
- A native SDK-glue bridge (e.g. the Kotlin `@ReactMethod` for `channelCreate/Send/Close`) must live in the **source template** that the config plugin copies in — a codegen/prebuild step that regenerates the native dir will silently drop a hand-edited copy, and JS throws "undefined is not a function."
- Android connectivity checks that resolve DNS over raw UDP can deadlock in a sandboxed network stack → node reports offline forever → never dials bootstrap. Assume-online when peer count is 0 if you hit this.
- SDS persistence may need to be forced memory-only in some builds (a nil persistency job can SIGSEGV on channel create). Memory-only SDS state means: don't rely on state surviving a `channelClose`/process restart — re-sync via your app-level reconcile.