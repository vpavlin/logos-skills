---
name: logos-reliable-channels
description: Use when building multi-writer sync or cross-user sharing (shared calendars, activity feeds, collaborative notes, Q&A, budgets) over the Logos/Waku delivery layer and you want ordering, gap-detection, retransmit and causal history instead of raw relay subscribe/send — the SDS Reliable Channel API (channelCreate / channelSend / onChannelMessageReceived). Reach for it when a channel "syncs nothing" / receives zero messages, when peers can't decode each other's payloads, or when deciding SDS channels vs. libchat vs. plain relay.
---

## What a Reliable Channel gives you

The Logos delivery layer ships an **SDS (Scalable Data Sync) Reliable Channel** on top of raw Waku relay. One channel == one content topic. Inside it you get, *for free, inside the delivery layer*: **segmentation** of large payloads, **SDS reliability** (ordering, gap detection, retransmit, causal-history references), optional **encryption**, and dispatch.[^1] This is the mature path (30+ releases) — prefer it over hand-rolling ack/retransmit on plain `subscribe`/`send`.[^2]

Outgoing pipeline: `Segment → SDS → Encrypt → Dispatch`. Incoming: `Decrypt → SDS → Reassemble → emit event`.[^1]

**It does NOT give you:** cross-cold-start history sync, membership/ACLs, or content encryption. See "What SDS does not cover" below. Layer those yourself.

## API surface (get the names right)

SDK/app-facing (three calls + three events):

- `channelCreate(channelId, contentTopic, senderId)` — build the channel + its listener.
- `channelSend(channelId, {payload, ephemeral})` — payload is base64 at the FFI boundary.
- `onChannelMessageReceived` → `{channelId, senderId, payload}` (payload base64-encoded by the FFI event serializer).[^3]
- also `onChannelMessageSent` / `onChannelMessageError` (channel-level, one per app-send once all its segments finalize).[^3]

FFI C exports (if you bind the lib directly): `logosdelivery_channel_create(ctx, cb, ud, channelId, contentTopic, senderId)`, `logosdelivery_channel_send(ctx, cb, ud, channelId, messageJson)` where `messageJson = {"payload": <base64>, "ephemeral": <bool>}`, `logosdelivery_channel_close`, `logosdelivery_channel_exists`.[^4]

Nim internals worth knowing: `ReliableChannelManager.createReliableChannel(channelId, contentTopic, senderId)`.[^5] A common convention is `channelId == contentTopic == a derived per-room topic`, `senderId == this device's id` — so every device in a shared room joins the *same* channel and `senderId` disambiguates authors.[^6]

## The five silent failure modes

Each one produces "channel exists, receives nothing" or "peer can't decode" with **no error** — memorize them.

### 1. `channelCreate` does NOT subscribe the content topic
`createReliableChannel` only constructs the channel object and its `MessageReceivedEvent` listener — it never subscribes.[^5] But the node's `recv_service` only emits `MessageReceivedEvent` for content topics in its *subscribed* set (`isContentSubscribed`), and the channel's ingress listener rides on that same event.[^7] So if you drive the lib directly you must call **both**: `subscribeContentTopic(topic)` **and** `channelCreate(topic, topic, deviceId)`. Miss the subscribe and relay receives the traffic but the channel layer is never fed → `ours: 0`.[^8] (A full node that already subscribes all autoshards on its own path gets this implicitly; a raw FFI/mobile embed does not.)

### 2. No encryption provider → nothing sends or delivers
Every channel egress calls the `Encrypt` request broker and every ingress calls `Decrypt`.[^1] With **no provider registered, both fail silently** — sends never reach the wire and `onChannelMessageReceived` never fires. `ReliableChannelManager.start()` auto-installs a **pass-through** via `setNoopEncryption()`; it is load-bearing, not decoration.[^9] If you do your own app-level sealing (see #5), keep the no-op provider. `setProvider` refuses to overwrite, so installing real encryption *before* `start()` is respected.[^9]

### 3. The `meta` spec marker — foreign/other-channel traffic is dropped
Every channel-framed `WakuMessage` carries `meta = "RELIABLE-CHANNEL-API/1"` bytes. On ingress the listener drops any message whose `meta` != that marker, then drops any whose `contentTopic` != this channel's — *before* any decode.[^10] Consequences: (a) a peer sending plain `send()` (no marker) onto the same topic is invisible to channel receivers, and vice-versa — pick one transport per topic and stick to it; (b) the trailing `/1` is the wire version, bumped only on breaking changes; mismatched versions silently ignore each other.[^10]

### 4. Double-base64 payload convention (SDK-plumbing trap)
The FFI base64-decodes `channelSend`'s `payload` once, and the receive event base64-*encodes* `payload` once.[^3][^4] If your SDK glue *also* base64s (e.g. hands the transport the base64 *text* as a byte array which the glue then base64s again), you get a **double-encoded** wire payload. This is easy to introduce by accident and then it's load-bearing across every peer — the receiver must peel two layers, and a peer that peels one sees base64 text instead of your bytes (symptom: `chan:6, ours:0`).[^11] Fix by making receive try both single- and double-decoded candidates and making send match whatever the fleet already does.[^12] Audit your exact byte path end-to-end before shipping.

### 5. Self-echo hides "did the peer actually receive it?"
Your own relayed message comes back to you; `onChannelMessageReceived.senderId` on that copy is *your own* id. When verifying that a peer received traffic, filter on `senderId != self` — grepping for your own sender id (or the hub's own echo) proves nothing.[^13]

## Wire / shard notes
Content topics auto-shard to a pubsub topic `/waku/2/rs/<cluster>/<shard>` (shard derived from the content topic; generation-zero defaults to 8 shards on cluster 0).[^14] Subscribe by **content topic** and let auto-sharding pick the pubsub shard — pinning a shard by hand risks subscribing a non-existent shard and receiving nothing.[^8] To confirm the fleet actually meshes your shard, check peer/mesh counts for the derived pubsub topic.[^13]

## What SDS does NOT cover — layer these yourself
- **Cold-start / pre-join history.** SDS reliability + the node's `recv_service` store-backfill (`checkStore`, `StoreQueryRequest`, fired on reconnect) recover messages missed *during a session or a disconnect*.[^15] They do **not** hand a brand-new device the full history from before it joined. Put an **app-level set-reconciliation** (fingerprint-exchange / RBSR) on top for that; SDS backstops it.[^16]
- **Encryption.** The channel is transport reliability, not confidentiality. Do your own sealing (e.g. AEAD with a shared room key, AAD=topic) and run the channel with the no-op provider.[^6]
- **Membership / who's allowed in.** A channel is open to anyone who knows the topic. Access control (who has the room key / pairing code) is an app concern.[^6]

## New-app checklist
1. Derive one content topic per shared room; use it as both `channelId` and `contentTopic`. `senderId` = stable device id.[^6]
2. On join: `subscribeContentTopic(topic)` **then** `channelCreate(topic, topic, deviceId)` — both, in that order.[^8]
3. Ensure an Encrypt/Decrypt provider exists (no-op if you seal yourself). Don't remove it.[^9]
4. Seal your payload yourself; hand the sealed bytes to `channelSend`. Nail down the base64 depth and keep it identical across every platform.[^12]
5. On receive, decode → (open/authenticate) → dedup → apply. Ignore your own `senderId` echoes; tolerate SDS-framed raw `message_received` copies that your open() harmlessly rejects.[^12]
6. Renew only the *subscription* on reconnect, never re-`channelCreate` (that would rebuild SDS state).[^8]
7. Add app-level reconcile for cold-start backfill.[^16]

See `reference/reliable-channels-integration-checklist.md` for the per-platform (full-node vs raw-FFI/mobile embed) split and build-wall notes.

## Where else this applies

The technique is transport-generic: any Logos/Waku app that needs multiple writers to converge on shared state, or one user to share a stream with others, over a content topic. Concrete non-origin examples: a **shared calendar** (each calendar = one channel; events sealed with a shared key; SDS handles ordering/gaps of edits; app-level reconcile heals a device that was offline for a week) — a **collaborative notes / outliner** app (per-document channel, CRDT ops as channel payloads) — a **community Q&A or activity feed** (per-room channel; `senderId` attributes posts; the no-op-provider trap, the subscribe-before-channelCreate gate, and the self-echo verification pitfall all apply identically). The five silent failure modes and the "what SDS does not cover" list are independent of what the payload means.

## Sources & evidence

All Nim paths are in the Logos delivery source tree (`logos_delivery/…`, `library/…`); the Reliable Channel LIP spec is at https://lip.logos.co/messaging/raw/reliable-channel-api.html. Consumer-side paths link into github.com/vpavlin/kym.

[^1]: `logos_delivery/channels/reliable_channel.nim` — the pipeline doc-comment (`Segment→SDS→Encrypt→Dispatch`), `send`/`onMessageReceived`, and the `Encrypt.request`/`Decrypt.request` broker calls. Proves the layer's guarantees and that encryption is a broker dispatch. Provenance: memory `kym-sds-desktop-proven`.
[^2]: `kym/docs/decisions.md` §22 (github.com/vpavlin/kym/blob/main/docs/decisions.md) — "mature — 30+ releases"; chose SDS Reliable Channels over raw relay and over libchat. Provenance: memory `kym-libchat-vs-reliable-channels`.
[^3]: `library/logos_delivery_api/node_api.nim` `logosdelivery_start_node` — installs `ChannelMessageReceivedEvent`→`onChannelMessageReceived` (fields `channelId`, `senderId`, `payload` base64-encoded), `ChannelMessageSentEvent`→`onChannelMessageSent`, `ChannelMessageErrorEvent`→`onChannelMessageError`. Proves event names + payload encoding. Provenance: memory `kym-sds-integration-status`.
[^4]: `library/channels_api/channel_api.nim` — the four FFI exports and that `channel_send` takes `{"payload": <base64>, "ephemeral": <bool>}` and base64-*decodes* payload once. Provenance: memory `kym-delivery-ffi-surface`.
[^5]: `logos_delivery/channels/api/channel_lifecycle.nim` `createReliableChannel` — constructs the channel, no subscribe call anywhere. Confirms failure-mode #1's root cause.
[^6]: `kym/docs/sync.md` §"transports plug in under this core" + `kym/docs/decisions.md` §22 — `channelId==contentTopic==derived topic`, `senderId==deviceId`, app-level ChaCha20-Poly1305 sealing (AAD=topic) with the channel on the no-op provider; membership = who holds the key. github.com/vpavlin/kym/blob/main/docs/sync.md. Provenance: memory `kym-senderid-and-shard`.
[^7]: `logos_delivery/messaging/delivery_service/recv_service/recv_service.nim` `processIncomingMessage` gates on `waku.isContentSubscribed(pubsubTopic, contentTopic)` before emitting `MessageReceivedEvent`; `logos_delivery/waku/node/subscription_manager.nim` `isContentSubscribed`. Plus `reliable_channel.nim`'s ingress listener is a `MessageReceivedEvent.listen`. Proves the channel listener rides the subscribed-only event.
[^8]: `kym/mobile/src/lib/delivery.ts` `joinRoute` (calls `subscribeContentTopic` then `channelCreate`) and the subscribe-by-content-topic / auto-shard comments; `kym/docs/decisions.md` §23 item 1. github.com/vpavlin/kym/blob/main/mobile/src/lib/delivery.ts. Provenance: memory `kym-mobile-channels-receive-rootcause` (`ours:0` = channelCreate not subscribing).
[^9]: `logos_delivery/channels/reliable_channel_manager.nim` `start()` (calls `setNoopEncryption()`, comment: no provider ⇒ send never reaches wire and received event never fires; `setProvider` refuses overwrite) and `logos_delivery/channels/encryption/noop_encryption.nim` `setNoopEncryption`.
[^10]: `logos_delivery/channels/reliable_channel.nim` — `const LipWireReliableChannelVersion = "RELIABLE-CHANNEL-API/1"`, written into `MessageEnvelope.meta`; ingress listener drops on `string.fromBytes(evt.message.meta) != LipWireReliableChannelVersion` then on `contentTopic` mismatch. Proves the marker value, that non-marked traffic is dropped, and the `/N` version semantics.
[^11]: `kym/docs/decisions.md` §23 item 2 — the double-base64 convention, its `chan:6, ours:0` symptom and symmetric failure. Provenance: memory `kym-mobile-channels-working` ("final bug was double-base64 payload mismatch").
[^12]: `kym/mobile/src/lib/delivery.ts` `publishSealed` / `payloadCandidates` (receive tries single- and double-decoded candidates; send double-encodes to match) and `kym/kym_core/src/kym_core_impl.cpp` `bytesPayload` / `deliverySend` (hands base64 text as a byte array; `channelSendAsync` under `KYM_USE_CHANNELS`). github.com/vpavlin/kym/blob/main/kym_core/src/kym_core_impl.cpp.
[^13]: `kym/kym_core/src/kym_core_impl.cpp` `onChannelMessageReceived` handler + `ingestRaw` (drops foreign/other-topic messages) and `kym/mobile/src/lib/delivery.ts` `getPeerCount` (peers/mesh/shard). Provenance: memory `kym-senderid-and-shard` — decode payload for the app's senderId, not the hub's own echo.
[^14]: Logos delivery `AGENTS.md` — content-topic auto-sharding, pubsub topic `/waku/2/rs/<cluster>/<shard>`, gen-zero default 8 shards on cluster 0.
[^15]: `logos_delivery/messaging/delivery_service/recv_service/recv_service.nim` `checkStore` / `getMissingMsgsFromStore` (`StoreQueryRequest`, fired via `onConnectionStatusChange` backfill on reconnect). Provenance: memory `kym-store-pull`.
[^16]: `kym/docs/sync.md` §status table + §v2 RBSR — SDS gives ordering/gap/retransmit; app-level range-based set reconciliation (`packages/sync/src/reconcile.mjs`) backstops for cold-start/full-history. github.com/vpavlin/kym/blob/main/docs/sync.md.
