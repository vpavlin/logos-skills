---
name: logos-storage
description: "Use Logos Storage (the Codex-derived storage_module / libstorage) for large or shared blobs in a local-first Logos app — attachments, media, log snapshots — without putting it on the capture path. Covers the model (content-addressed CIDs, DHT provider discovery, direct dials), the two-id rule (sealed-hash handle + CID sidecar), the storage_module API and its 2.x → 3.0 changes, the Basecamp 0.3 host-owned node, why NAT-ed users can't fetch from each other and the reachable-hub cache that fixes it, address/listen traps (extip, IPv6, mesh ULA), event-less completion polling, and the mobile reality (no Android module; a fetch-only client). Use when adding attachments or snapshots, or when 'upload works but nobody can fetch it' / 'stuck at fetching'."
---

# Logos Storage for local-first apps

Logos Storage (the `storage_module` Basecamp module, wrapping the Nim `libstorage`; formerly Codex)
stores blobs by content and lets any node that knows the content id find a holder and fetch it. In
our apps it is a **replication target**, never the source of truth: a file is sealed and kept on the
device first; Storage makes it fetchable by the other participants.[^model]

## The model (get this right first)

- **Upload returns a CID, minted after the upload.** It is not `sha256` of anything you hold, and it
  includes the **file name** in the manifest — byte-identical content uploaded under a different name
  gets a different CID.
- **Fetch = DHT discovery, then a direct dial.** The holder advertises the CID to a Kademlia DHT; the
  fetcher looks up providers and dials one directly to pull the manifest and blocks. There is no relay
  fleet as with Delivery: **a provider nobody can dial is a provider nobody can fetch from.**
- **A node only advertises if it believes it is reachable** (AutoNAT says reachable, an explicit
  `nat=extip:<addr>`, or a relay circuit address). A NAT-ed laptop with no extip uploads fine and
  advertises nothing.[^model]

## The two-id rule

Keep capture local-first and content-addressed on *your* terms:

- `blobId = sha256(sealed bytes)` — the local handle, known at capture, used in the event payload
  immediately. Seal with the room key before anything leaves the device (charter rule 6); hash the
  **sealed** bytes, never the plaintext.
- `storageCid` — a sidecar filled in when the upload completes (an `attachment` update event, or a
  field the fold merges). Readers fetch by CID and verify against `blobId` after download.

Seal deterministically (nonce derived from the content) if you want the same file to produce the same
sealed bytes and so dedup across uploads.[^model]

## API (storage_module) — version-scoped

`init(cfgJson)` → `start()` → event `storageStart`. Upload `uploadUrl(path, chunk[, advertise])` returns
a session id; `storageUploadDone{sessionId, cid}` arrives later. Download
`downloadToUrl(cid, path, local, chunk[, isPrivate, advertise])` → `storageDownloadDone{sessionId}`
(no path in the event — map session → path yourself). Also `exists(cid)`, `fetch(cid…)` (prefetch into
the local store), `downloadManifest`, `manifests()`, `remove`, `connect(peerId, addrs)`, `spr()`
(this node's signed peer record, shareable as a bootstrap address), `debug()`.[^api]

| | 2.1.x | 3.0 (Basecamp 0.3) |
|---|---|---|
| upload/download/fetch args | as above without the bracketed flags | + `advertise` / `isPrivate` booleans (pass `advertise=true`, `isPrivate=false` for shared content) |
| `fetch` / `downloadToUrl` | ~3 s synchronous manifest wait | **up to ~30 s** synchronous — longer than the default IPC timeout |
| `destroy()` | any time | fails while starting/stopping; stop first or check `isRunning()` |
| config | your `init()` wins | `init()` persists to `~/.logos_storage/config.json`; on 0.3 hosts the **host** inits first and yours is refused |
| removed | — | `togglePrivateQueries`, `migrateConfig` |

Rules that follow:
- **Never call `fetch`/`downloadToUrl` synchronously on the module's thread**, and never in a loop.
  Use the async variant with an explicit timeout (`…AsyncResult(…, cb, 60000)` on builder 0.3.1) and
  treat a timeout as "maybe still running", keeping the pending entry.
- **Don't depend on completion events alone.** One Basecamp build dropped Storage's done events; the
  fix that held was polling while a transfer is pending (upload done = `manifests()` lists the staged
  file; download done = file size equals the manifest's `datasetSize`), with events as the fast path
  and a hard timeout that ends in a clear error.[^api]
- Don't call Storage from inside one of its own event callbacks (stop → `storageStop` → restart);
  defer to the loop (`logos-distributed-debugging`, Move 9).

## Basecamp 0.3: the host owns the node

Basecamp 0.3 and `logosctl` bundle storage_module 3.0 and initialise it from
`$HOME/.logos_storage/config.json` (defaults: the public `logos.test` network). Your module's
`init()` returns false. **Adopt the host's node**: on refusal, mark it host-owned, call `start()`
(harmless if running), and never stop, restart or reconfigure it — other modules share it. To set a
public address, AutoNAT/relay server or a bootstrap node, the *host's* config file has to change; how
an app is meant to influence that is an open question for the platform team.[^host]

## NAT: why home users can't fetch from each other, and the hub fix

Two desktops behind different NATs: the uploader never advertises (not reachable), and relayed links
are capped (~128 KB / 2 min per circuit in nim-libp2p's defaults), so a NAT↔NAT transfer over a relay
dies mid-file. What works is a **reachable hub** that:[^nat]

1. runs Storage with its public address as `extip` **and** `autonat-server` + `relay-server` enabled —
   NAT-ed clients that bootstrap from it learn they're private and get a relay circuit address,
   which lets them advertise;
2. **caches every CID it sees** referenced in the rooms it belongs to (fetch on first sight, retry for
   ~30 min while the uploader becomes findable — that takes a few minutes after it starts), over the
   uploader's own outbound connection;
3. is the node everyone else then fetches from.

Clients must list the hub as a **bootstrap node in their config**; connecting at runtime was not
enough. One libp2p bug blocked step 2: Kademlia `getProviders` ignored provider records stored on the
node itself, so the hub — the only DHT server, holding everyone's records — couldn't find content it
had been told about. That needed a patch on the hub's build (still not upstream as of 2026-10-02).

**Still open on Basecamp 0.3 (stock Storage 3.0, 2026-10-09).** A Swamp hub on a VPS with a
public `extip`, `autonat-server` and `relay-server` in its config (libstorage 0.4.5 logs "AutoNAT
server enabled"). Results:
- **Works:** the catalogue; the hub's Storage port is reachable from outside; the AutoNAT server
  answers dial requests.
- **Doesn't work reliably:** caching a NAT-ed publisher's file on the hub. In the offline-publisher
  tests the hub never fetched it, with either a runtime `storage_module.connect(hubPeerId, …)` from
  the publisher (the connection came up) or the hub in the publisher's `bootstrap-node` list. A later
  explicit `fetch` on the hub, with the publisher online, did end with `exists` = true.
- **What the hub's debug log shows:**
  - the NAT-ed publisher announces only loopback and its private LAN address (`/ip4/127.0.0.1/…`,
    `/ip4/192.168.x.x/…`), so the hub can't dial it back (see "No `extip` = no announced address"
    below);
  - `debug` reports `relayRunning: false` despite `relay-server: true`, so the publisher gets no
    relay circuit address either.
- **Not yet ruled out:** the `getProviders` local-records bug above. The Scala hub where hub caching
  was proven runs a patched Storage; the Swamp hub runs stock Storage.

Until it's solved, files are only fetchable while their publisher, or another holder, is online.[^hub09]

## Address traps

- **No `extip` = no announced address.** A node announces exactly its `nat=extip:<addr>`; without it
  the signed peer record carries nothing dialable.
- **Listen on the family you announce.** Announcing an IPv6 address while listening on `0.0.0.0`
  binds IPv4 only — the address is advertised and nothing answers. Listen on `::` (dual-stack).
- **No-extip nodes couldn't dial IPv6** (dial bound to the IPv4 listen address → `EINVAL`, no SYN).
  Fixed upstream in nim-libp2p v2.4.0 but not in the version Storage pinned as of 2026-10; carry the
  patch if you dial IPv6 peers without an extip.
- **Private/ULA addresses are dropped** from the DHT when the network is public (libstorage v0.5.1):
  overlay/mesh addresses (`fd00::/8`) won't propagate via a public bootstrap. Unverified in practice.
- **IPv4-only filters on overlays:** on one overlay mesh, IPv6 carried every port while IPv4 only
  passed SSH. Test with a live listener before concluding "the mesh refuses TCP".[^addr]

## Mobile

There is no Android `storage_module` and libstorage has no HTTP API. A **fetch-only** Android client
is feasible: libstorage cross-compiled to arm64 with the NDK, behind a JNI/RN bridge, downloading by
CID from reachable providers (in practice, the hub). Uploading from phones stays out of scope; phones
author events, desktops or hubs hold blobs. (Upstream tracking issue: logos-storage-nim#1221.)[^mobile]

## Snapshots — the catch-up lever

Storage is also how a new device catches up without replaying a long event log over Delivery: a hub
writes a sealed, deterministic **log snapshot** to Storage and shares a pointer (CID + where to fetch);
a joining device fetches it, verifies signatures, ingests (dedup by id), then reconciles only the tail.
It cuts message count, which matters once RLN rate limits apply (`logos-rln-budget`). Carry the
pointer in a channel control message or a QR — a long pasted link got corrupted and silently dropped
the snapshot.[^snap]

## Sources & evidence

[^model]: Memories `logos-storage-module` (API, CID-after-upload, two ids, desktop-only), `codex-fetch-needs-dht-discovery` (DHT discovery + direct dial, advertiser `reachable()` gate, manifest CID includes the file name). Scala attachments: `scala_impl.cpp` `uploadAttachment` (deterministic seal, `blobId = sha256(sealed)`).
[^api]: `loam-basecamp` `port/0.3` `docs/port-0.3/analysis-storage.md` § 2 (2.1.3 → 3.0.0 API table, 30 s `FETCH_MANIFEST_TIMEOUT_MS`, busy `destroy`, removed calls, config persisted). Event-less completion: memory `storage-nat-hub-cache` (scala 0.9.39 poll path, Basecamp 0.2.3). `AsyncResult` timeouts: scala `port/0.3` `downloadToUrlAsyncResult(…, kStorageCallTimeoutMs)`.
[^host]: Memory `port-0-3` finding 2; `TESTING.md` § Known limitations; scala `port/0.3` `ensureStorage` (`m_storageHostOwned`).
[^nat]: Memory `storage-nat-hub-cache` (three causes confirmed on a live hub 2026-09-30; relay limits; cache-on-see with retries; nim-libp2p patch `0002-kad-getproviders-include-local-records.patch`), re-proven on Basecamp 0.3 with a `logosctl` hub (memory `port-0-3`: bootstrap from the hub required, runtime connect not enough). Upstream status: `analysis-storage.md` § 3.
[^addr]: Memories `codex-fetch-needs-dht-discovery` (announced addrs = extip only; listen-ip bug; overlay IPv4 vs IPv6) and `libstorage-ipv6-dial-bind-bug` (EINVAL dial bind; patch `0001-tcp-dial-bind-same-family.patch`; upstream fix nim-libp2p #2952 in v2.4.0 per `analysis-storage.md` § 3). ULA drop: `analysis-storage.md` § 2c (#1542, UNVERIFIED there).
[^mobile]: Memories `logos-storage-module` (no Android target, no REST; fetch-only feasible), `storage-android-fetch-client` (arm64 cross-compile proven; per-dependency cross recipe).
[^snap]: Memory `rln-readiness-plan` (loam-sync ADR 0020 snapshots, TS + C++ byte-identical serializer, hub writer → phone reader proven end-to-end 2026-09-25; the corrupted-link failure).
[^hub09]: Swamp hub (`hub/vps-hub.sh`, logosctl 0.3.1 AppImage, Storage TCP 8399) on the VPS, 2026-10-09: offline-publisher tests from atlas with a runtime `connect` and with `bootstrap-node` = the hub's SPR; both left the hub at `fetched 0` / `stalled 18` and the fresh node's download at "transfer stalled (no holder answering)". The catalogue part passed: a fresh node got the model 15 s after start with the publisher offline. Later the same day a manual `storage_module fetch` on the hub (publisher online, DEBUG log) ended with `exists` = true; the log showed the publisher's loopback/LAN-only addresses timing out and `relayRunning: false`.
