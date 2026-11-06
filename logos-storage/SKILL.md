---
name: logos-storage
description: "Use Logos Storage (the Codex-derived storage_module / libstorage) for large or shared blobs in a local-first Logos app — attachments, media, log snapshots — without putting it on the capture path. Covers the model (content-addressed CIDs, DHT provider discovery, direct dials), the two-id rule (sealed-hash handle + CID sidecar), the storage_module API and its 2.x → 3.0 changes, the Basecamp 0.3 host-owned node, why NAT-ed users can't fetch from each other and the reachable-hub cache that fixes it, address/listen traps (extip, IPv6, mesh ULA), event-less completion polling, and the mobile reality (no Android module; a fetch-only client). Also covers relays (auto-discovered, capped), the download-swarm bug (manifest arrives, data never does; fixed in storage_module 3.0.3 / logos-storage-nim #1540), calls that block storage_module's queue, and storage_gateway (the same API, served by a node on a server through its REST API). Use when adding attachments or snapshots, or when 'upload works but nobody can fetch it' / 'stuck at fetching' / 'only the manifest came'."
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

*API differences as of 2026-10-04 (storage_module 2.1.3 → 3.0.0):*

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
- **storage_module serves one call at a time, and `downloadToUrl`/`fetch` look the manifest up before
  they return.** A CID nobody answers for therefore holds every later call (an upload, a
  `manifests()` listing) until that lookup gives up: the app sees "did not answer (timeout)" on
  unrelated calls. Ask for the manifest first with `downloadManifest` (returns at once; done in the
  `storageDownloadManifestDone` event) and call `downloadToUrl` only once it is there. And don't put
  an error over a listing that worked before because one refresh timed out: keep it and look again.[^queue]
- **A dataset is listed (`manifests()`, `exists()`) as soon as its manifest arrives**, before (and
  without) its data. Track what you are still fetching and show it as "arriving" / "only the
  description came", never as a normal file.[^queue]

## Basecamp 0.3: the host owns the node

As of 2026-10-04, Basecamp 0.3 and `logosctl` bundle storage_module 3.0 and initialise it from
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

**A hub on Basecamp 0.3 pulls home users' files with `fetch()`, but `exists()` lies about it**
(stock Storage 3.0, 2026-10-09).[^hub09] A home node can't be dialled, yet the hub's background
`storage_module.fetch(cid, false, true)` does get its blocks while it's online (through relay
circuits; ~2 MB in 2-6 min). Two traps:
- **`exists(cid)` is true as soon as the manifest is in**, not the data. A hub that stops there
  holds manifests only, and downloads from it stall ("transfer stalled (no holder answering)").
  `space` showing one block, or a local-only `downloadToUrl` writing 0 bytes, gives it away.
  Check completeness by writing the file from local data (`downloadToUrl(cid, path, local=true, ...)`)
  and verifying its hash. Until that works, fetch again; publishers are usually online right
  after publishing, so pull the newest files first.
- **On the hub, `downloadToUrl` from a NAT-ed publisher fails to start** ("Failed to start
  download"); use `fetch()` there. Clients downloading from the reachable hub use `downloadToUrl`
  as usual (300 KB in 4 s).

So a file reaches the hub only while its publisher is online. Neither a runtime `connect()` to the
hub nor the hub in the publisher's `bootstrap-node` list was needed.

**Reproduce NAT on one machine:** run the "home" node in a rootless podman container (pasta
networking: it can dial out, nothing can dial in). It reaches the host at `169.254.1.2`
(`host.containers.internal`), so a host-side hub announces `extip:169.254.1.2`. The host's own
LAN address points back into the container there. Run logosctl's extracted AppImage inside
(`--security-opt label=disable` for the bind mount on SELinux hosts).

## Relays: auto-discovered, but capped (don't move files through them)

As of libstorage v0.5.3 (2026-10-15, read from the source):[^relay]
- **Clients find relays on their own.** When AutoNAT says a node isn't reachable (and no port mapping
  works), its `AutoRelayService` reserves slots on up to `nat-max-relays` (default 2) relays. A relay
  is any **already connected** peer that supports the circuit-relay v2 hop protocol. Nothing is
  pre-configured, and there is no "use this relay" setting: to favour one, make it a bootstrap node
  (or `connect()` to it) so it is among the connected peers.
- **A relay is a node run with `--relay-server`** (needs `--nat=extip:<public IP>`). It uses
  nim-libp2p's `Relay.new()` defaults, which are **not configurable from the CLI: 120 s and 128 KiB
  per circuit**. Enough for a manifest, not for a file: this is why two NAT-ed machines often swap
  manifests but no data. A relay on another host (say Akash, with unlimited traffic) changes nothing
  unless the node is patched to raise those limits.
- What works instead is a **reachable holder**: a node with a public address that holds the data
  (both NAT-ed machines dial it directly: one uploads, the other downloads). See the hub below, or
  storage_gateway, which uses such a node through its REST API.
- Relayed links do try to upgrade to a direct one (DCUtR hole punching), which sometimes works.

## The download swarm bug: "the manifest came, the data never did"

Stock libstorage v0.5.x (every storage_module 3.0.0-3.0.2, and the v0.5.3 node) fills a download's
swarm with up to 16 of the peers it is **connected** to, records a holder's "have" only if that
holder is already in the swarm, and never drops silent members. A reachable holder that answers from
outside the first 16 is ignored for good: `trace` on `blockexcengine` shows "Received block presence"
then "No peer with range" in a loop. It hits at random (about half of fresh nodes on logos.test), even
with the holder directly connected. **Fix: logos-storage-nim PR #1540** (open, as of 2026-10-15),
carried as **storage_module 3.0.3** (`vpavlin/logos-storage-module` branch `pr1540-3.0.3`; Basecamp
0.3.1 loads an installed 3.0.3 over its bundled 3.0.0, logosctl doesn't). Stock 0/5 vs 5/5 delivered
in the same test.[^swarm] Download with `advertise=false` and `setAdvertise(cid, true)` only once the
data is whole, so a failed download leaves no dead provider in the DHT.

## A node on a server through its REST API: storage_gateway

The standalone `logos-storage` node (release binaries, or `make` in logos-storage-nim) has a REST API
(`--api-bindaddr`, `--api-port`; hidden from `--help` since 2026-09 but present). `storage_gateway`
(`vpavlin/logos-storage-gateway`) is a Basecamp module with **storage_module's exact API** (methods,
arguments, events) served by such a node: `init({"url": "http://[fd…]:8480"})`, then the usual calls,
with files read from and written to this computer. An app can then offer both with one code path
(`withStorage(n, [&](auto& sm){ … })`), e.g. Dufs and Reelcut.[^gateway] Notes:
- **The REST API has no auth:** bind it to a VPN/mesh address only; make the p2p port public.
- The node checks `Content-Type` against Nim's `std/mimetypes` and answers 422 for unknown ones
  (`text/markdown`): upload again without a type.
- Mix (`isPrivate`) isn't offered over REST.
- A NAT-ed node fetching from the server: `connect(serverPeer, [its /ip4/…/tcp/…])` first helps, and the
  server itself needs the #1540 fix to fetch FROM a NAT-ed node (with it: ~2 min for a small file).

## More traps (as of 2026-11)

- **A node advertises at most ~500 CIDs.** The vendored nim-libp2p Kademlia caps both provided keys
  and provider records at 500 by default, and libstorage builds its DHT with the defaults. A node
  holding more than that announces only the ~500 it re-provided most recently. Fetching a manifest asks
  only the DHT, never peers you are already connected to, so the rest fail with "Failed to fetch
  manifest … Key doesn't exist" even while connected to the holder. If one node holds thousands of
  files (a backup target, an app repo host), raise the capacities and fall back to connected peers
  for the manifest (both carried on a fork as of 2026-11-05, no upstream PR yet), or spread the data.
- **`--block-ttl 0` deletes, it doesn't mean "forever".** Expiry is `now + ttl`, and the maintainer
  removes expired blocks: with ttl 0 an upload is gone within seconds. The default is **30 days**, so
  a server node started without the flag silently drops everything a month after upload. Use a huge
  value (e.g. `--block-ttl=3153600000`, 100 years). Restarting with a new TTL changes only new blocks;
  to extend existing ones, re-upload the same bytes with the same block size, filename and
  content type (they are part of the manifest), and check the returned CID is unchanged.
- **Slow downloads from peers you `connect()`ed to.** Peers added with `connect()` join the download
  swarm without ever being asked what they hold, so each batch waits for a per-batch presence answer
  and falls back to a 2 s retry: about one batch every 2 s (64 KiB/s with 64 KiB blocks). The same
  holder found through the DHT gives MB/s. Fix: logos-storage-nim#1575 (open as of 2026-11-01); only
  the *downloading* node needs it. Without it, prefer DHT discovery over an explicit `connect()` for
  big files.
- The REST API ignores HTTP `Range` (it answers 200 with the whole body).

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

There is no Android `storage_module`, and libstorage (the library) has no HTTP API (the standalone
node does: see storage_gateway above). A **fetch-only** Android client
is feasible: libstorage cross-compiled to arm64 with the NDK, behind a JNI/RN bridge, downloading by
CID from reachable providers (in practice, the hub). Uploading from phones stays out of scope; phones
author events, desktops or hubs hold blobs. (Upstream tracking issue: logos-storage-nim#1221.)[^mobile]

If the phone already runs a **shared node** that bundles libstorage (Loam, see `loam-integrate-app`),
fetch through it instead of putting libstorage into every app: one node, one cache, one set of
connections, and apps stay small. Pass the holder's peer id *and* addresses with the request (a signed
peer record in the invite is the easy way), since the node has to dial it.

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
[^hub09]: Swamp (`vpavlin/swamp3d` `swamp_core_impl.cpp` `hubSweep`/`hubPullStep`, 2026-10-09). The VPS hub with Storage debug logs: manifests only (`space` 1 block, local write 0 bytes) after trusting `exists()`; a file the hub uploaded itself downloaded on a fresh NAT-ed node in 4 s. NAT rig: a publisher container's 1.57 MB STL reached the hub in ~6 min; with the publisher stopped, a second container downloaded it byte-identical in 177 s. Real network: a home publisher behind NAT, VPS hub, fresh node downloaded with the publisher offline in 164 s.
[^queue]: Dufs (`vpavlin/dufs-basecamp` PR #3, dufs_core 0.2.5-0.2.6, 2026-10-15): an "Add by CID" for an unanswered CID blocked an upload until the lookup ended; downloads now go `downloadManifest` first (`fetchFile` → `manifestDone` → `beginStorageDownload`); a failed `manifests()` refresh keeps the last good listing. Manifest-only datasets: the VPS node listed `test-privacy.jpg` while `GET /data/<cid>` returned 0 bytes; Dufs marks them (`StNode.incomplete`, persisted).
[^relay]: logos-storage-nim v0.5.3 `storage/storage.nim` (`Relay.new()` when `--relay-server`, `AutoRelayService.new(maxNumRelays = natMaxRelays …)`), `storage/conf.nim` (`nat-max-relays` default 2; `--relay-server` requires extip), `storage/nat.nim` (autorelay started when not reachable without port mapping; `holePunchIfRelayed`); nim-libp2p `relay.nim` `DefaultLimitDuration = 120`, `DefaultLimitData = 1 shl 17`; `autorelayservice.nim` picks connected peers whose ProtoBook has `RelayV2HopCodec`.
[^swarm]: atlas/whisperbox's analysis and test (`ActiveDownload.updatePeerAvailability` discards a failed `swarm.addPeer`; `DefaultDeltaMax = 16`), posted on logos-storage-nim#1540 (comment 6187343523, 2026-10-15). Confirmed here: a stock v0.5.3 VPS node ignored a NAT-ed holder it was directly connected to ("Want-have send timed out"); the same node built from d82d0d39 fetched it in ~2 min.
[^gateway]: `vpavlin/logos-storage-gateway` 0.1.1 (module `storage_gateway`, cpp-httplib, worker threads emit the events); VPS node `storage-gateway-node.service` (REST on the mesh only, p2p public, `--autonat-server --relay-server`). Dufs core 0.2.x (two nodes at once: this computer's + one elsewhere; copy between them through this computer) and Reelcut core 0.1.8 (reels + `reels-<job>.json` to Storage) use it.
