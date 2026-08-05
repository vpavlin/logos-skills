---
name: logos-mobile-app
description: Use when embedding a Logos Delivery (Waku) node inside a React Native / Expo Android app so a phone can sync directly on the wire with desktop peers or a headless hub — multi-writer state, cross-user sharing (budgets, calendars, notes, trackers, Q&A). Covers the hand-written JNI bridge, the config plugin that survives `expo prebuild`, the mobile side of SDS Reliable Channels (the channel mechanics themselves live in logos-reliable-channels), cross-thread event delivery, and self-hosted F-Droid release. Reach for it on symptoms like "phone receives nothing", "undefined is not a function", release SIGSEGV, or "node stays offline".
---

A Logos app that syncs multi-writer state between devices needs an embedded Delivery (Waku) node **on the phone too** — not a thin client talking to a server. There is no public x86_64 build and no Expo-autolinkable package, so you bridge the prebuilt `liblogosdelivery.so` yourself through hand-written JNI and keep it alive across `expo prebuild`. This playbook is the set of non-obvious things that each, on their own, silently produce "syncs nothing." [^1]

The channel/SDS layer itself — the reliable-channel API surface, the encryption provider, the receive-chain gates, and the payload convention — is owned by the sibling skill **logos-reliable-channels**. This skill covers only the *mobile* wiring of it (§6, §7) and cross-references the rest rather than restating it.

## Architecture in one breath

Prebuilt `.so` (arm64 only) + a hand-written JNI shim (`logos_messaging_ffi.c`) + a Kotlin `ReactContextBaseJavaModule` bridge → a React Native `NativeModule` named `LogosMessaging`. The node runs in-process; received messages come back as a single `DeviceEventManagerModule` JS event. Because Expo CNG regenerates `android/` from scratch, none of this can live in `android/` — it lives outside and is re-copied by a config plugin on every prebuild. [^2][^3]

The JNI wraps the **stable high-level API** (`logosdelivery_create_node/start_node/stop_node/destroy/subscribe/send/get_node_info/channel_create/channel_send/channel_close/set_event_callback`) and a few **kernel** symbols directly (`waku_version/connect/listen_addresses/relay_publish/relay_subscribe/store_query`). Gotcha: raw `waku_stop`/`waku_destroy` are **not exported** by the current build — call `logosdelivery_stop_node`/`logosdelivery_destroy` (same ABI). [^1]

## 1. Config plugin — survive `expo prebuild` (or the build silently drops your code)

`expo prebuild` wipes `android/`. Anything hand-added there is gone next build → JS throws `undefined is not a function` when it calls a bridge method. Keep native files **outside** `android/` and re-apply with a `withDangerousMod` plugin. The plugin must: [^2][^3]

- Copy `native/<lib>/arm64-v8a/*.so` → `app/src/main/jniLibs/arm64-v8a/` (the four libs below), and `native/<lib>/android/java/**.kt` → `app/src/main/java/**`.
- Register the RN package by hand (`withMainApplication` → `add(YourPackage())`) — a hand-written JNI module is **not autolinkable**.
- Add any Java deps the bridge uses (e.g. `com.google.code.gson:gson:2.10.1` for config serialization) via `withAppBuildGradle`.
- Set `expo.useLegacyPackaging=true` (`withGradleProperties`). The `.so` is opened with `System.loadLibrary`, so it must be an **uncompressed real file** on disk, not zipped inside the APK.

The four `.so`, in load order (see §3): `libc++_shared.so`, `librln.so`, `liblogosdelivery.so`, `lib<yourjni>.so`. `Android.mk`: declare `liblogosdelivery.so` as `PREBUILT_SHARED_LIBRARY` and build your JNI `.c` as `BUILD_SHARED_LIBRARY` with `LOCAL_LDLIBS := -llog`, `LOCAL_SHARED_LIBRARIES := logosdelivery`. [^4]

## 2. ABI reality: arm64 only

There is no public x86_64 `liblogosdelivery.so`, so the x86_64 emulator has no node. **Test sync on a real arm64 phone — never the emulator.** [^5] Guard it: load the native libs **lazily** (on first `setup()`), not in a class-init/`static` block, so merely registering the module on a device that lacks the `.so` does not crash the app at startup — the `UnsatisfiedLinkError` then surfaces only if sync is actually invoked, and your `setup()` can `promise.reject` gracefully. [^1]

## 3. Native lib load order is load-bearing

```
System.loadLibrary("c++_shared")   // FIRST: liblogosdelivery references __gxx_personality_v0
System.loadLibrary("rln")          // then RLN
System.loadLibrary("logosdelivery")// then the node
System.loadLibrary("<yourjni>")    // then your JNI shim
```
Wrong order → `UnsatisfiedLinkError` on a missing C++ personality symbol. Do it once behind a `@Synchronized`, `@Volatile`-guarded flag. [^1]

## 4. The cross-thread event callback — the release-only SIGSEGV

The node invokes your event callback from **its own worker threads (FFI, watchdog, libp2p — ≥3)**, none attached to the JVM. Rules: [^1]

- In `JNI_OnLoad`, cache a **global ref** to the callback class and its static method id (`execEventCallback(J Ljava/lang/String;)V`). Per-call `FindClass` is unsafe off the main thread.
- In the callback, `GetEnv`; if `JNI_EDETACHED`, `AttachCurrentThread` **once** and arm a `pthread_key` destructor to `DetachCurrentThread` on thread exit. Never attach/detach per callback.
- **Attach unconditionally — never inside `assert()`.** A release/`NDEBUG` build strips the `assert` body, leaving `env` NULL → SIGSEGV on the first JNI deref. Classic "works in debug, crashes in release."

The callback just marshals `(wakuPtr, msg)` into a JS event (e.g. emit `"logosMessage"` via `RCTDeviceEventEmitter`). All receives — live relay **and** SDS channel — arrive on this one event stream. [^1]

Related null-handling: the node-creation callback fires on success too (a benign `"on_response-ok"`), so treat it as failure **only** when the result's `error` flag is set — otherwise you discard the real node ctx. And reject node creation when the returned ptr is 0/nil (config rejected) instead of proceeding to a segfault. [^1]

## 5. Node config that actually connects: RELAY, not light client

Use a minimal **relay** config; do **not** add light-client fields. [^1]
```js
{ mode: "Core", preset: "<fleet-preset>", relay: true, entryNodes: [ ...bootstrapMultiaddrs ] }
```
Adding `filter`/`lightpush`/`store` + pinned service nodes made `waku_new` **reject the config** → node reports offline. A relay node dials all bootstrap peers, joins the gossip mesh, and receives by mesh membership — no service node has to grant a filter lease (the fleet served "filter 0" unreliably). No `clusterId`/`shard` pinning — the preset + auto-sharding handle it. After `start`, wait ~10s for the mesh to form before the first publish. [^1]

## 6. Subscribe by CONTENT topic, not raw pubsub — wire the right JNI symbol

The JNI exposes **both** `logosdelivery_subscribe` (content topic — auto-shards to the real pubsub topic `/waku/2/rs/<cluster>/<shard>`) and the low-level `waku_relay_subscribe` (a **raw pubsub** topic). Wire the content-topic call; hand the low-level one a content topic and it silently subscribes to a shard that does not exist → the node receives **nothing**. That is the #1 "traffic flows but phone sees zero" bug at the mobile boundary. Content-topic leases expire on the fleet — renew on an idempotent timer. (*Why* content-topic subscription is the rule, and that it is also the channel layer's receive-chain gate, is in **logos-reliable-channels**.) [^1]

## 7. SDS Reliable Channels — the mobile-specific wiring

To interoperate with desktop/hub peers running channels, the phone speaks the SDS reliable-channel API, not raw subscribe/send (raw can't decode the channel framing). **The channel API surface** (`channelCreate`/`channelSend`/`onChannelMessageReceived`, `channelId == contentTopic ==` the derived topic, `senderId ==` a stable per-install device id that is also your HLC tiebreak), **the no-op encryption provider requirement, the double-base64 payload convention, and the receive-chain gates are all in logos-reliable-channels — do not restate them.** [^1][^6] What is genuinely *this* skill's concern is how those cross the JS↔JNI boundary, plus two Android-only failures:

1. **JS must call `subscribeContentTopic(topic)` AND `channelCreate(topic, topic, deviceId)`** — both, in the join path — because `channelCreate` does not itself subscribe the content topic (see logos-reliable-channels for the `isContentSubscribed` receive-chain gate). Symptom of doing only `channelCreate`: relay receives traffic but the channel counter reads `ours: 0` — the channel layer is never fed. This is pure JS wiring in your `delivery` lib; the JNI is not involved. [^6]
2. **The double-base64 convention lives in JS around the bridge.** On send, double-encode (`fromByteArray(utf8Bytes(sealedB64))`); on receive, also try the double-decoded candidate. The JNI passes payload bytes opaquely — so the encode/decode discipline (and *why* it exists) is entirely the sibling skill's; mobile only has to apply it symmetrically on both sides of the bridge or it is unreadable to the hub. [^6]
3. **Android OnlineMonitor DNS deadlock (mobile-only — this skill's own fix).** The node's `DnsResolver` (raw UDP to 1.1.1.1) fails on Android's sandboxed network stack → the connectivity check returns false → node reports offline → won't dial bootstrap → never gets peers → offline forever. Patch `online_monitor.nim`: when `numConnectedPeers == 0`, **assume online** (desktop is unaffected — DNS resolves there), so the node dials the fleet and forms the mesh; once connected, real peer count keeps it online. Bake the patch into the arm64 `.so` you build from source. (Same theme as §9: peer count is not trustworthy ground truth for "offline.") [^7]
4. **Channel `@ReactMethod`s must live in the plugin-copied `.kt` template** (§1), not the generated `android/` copy — else `expo prebuild` drops them and JS throws `undefined is not a function`. This is the §1 prebuild trap applied to the channel methods specifically. [^3]

## 8. Store (history) pull — the catch-up path

The asymmetric mobile mesh doesn't guarantee live delivery of everything, so pull history from the fleet **store** nodes. Bridge the kernel `waku_store_query(ctx, jsonQuery, peerAddr, timeoutMs)` — the desktop delivery module does **not** expose it, but the phone only needs to *read*. `jsonQuery` is an nwaku `StoreQueryRequest`: `{requestId, contentTopics:[...], includeData:true, paginationForward:true, paginationLimit:100, paginationCursor?}`; `peerAddr` is a fleet store multiaddr. Page through `paginationCursor`, try each bootstrap peer until one answers, decrypt each returned base64 payload exactly like a live receive (idempotent, so re-pulling is safe). This works **only if the fleet retains traffic on your shard** — surface the result (msgs → events) in the UI as the make-or-break signal, and keep core-level rebroadcast as a safety net until you've seen `store: msg>0` on-device. [^1][^8]

## 9. Diagnosing connected-vs-isolated

`getNodeInfo("Metrics")` returns Prometheus text. Read the `/waku/2/rs/<cluster>/<shard>` lines to confirm the phone landed on the **same shard** as the desktop/hub — a shard mismatch is silent (both look "up," neither receives the other), so shard match + actual message receipt are your real ground truth.

**Caveat: the `libp2p_peers` (transport peers) gauge UNDER-REPORTS — it has been observed reading 0 while sync was flowing.** Treat a non-zero count as a positive signal of connectivity, but **never conclude "offline" from a 0.** (This is exactly why the §7.3 online-monitor patch assumes-online at 0 peers.) When in doubt, trust an actual received message, not the gauge. [^1]

## 10. Release signing + self-hosted F-Droid

Expo's template signs **both** debug and release with the shared, well-known Android debug key — anyone could sign an APK your phone accepts as an update, and the signing cert is the app's permanent identity (can't rotate post-publish). Add a `withAppBuildGradle` plugin that injects a `release` `signingConfig` reading credentials from `~/.gradle/gradle.properties` (**outside** the repo), falling back to the debug key when absent so fresh clones/CI still build. [^9]

Ship: `expo prebuild --platform android` (re-applies the plugins — not optional), assert `app.json`'s `versionCode` matches the generated `build.gradle`, `gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a`, then `fdroid update` on the **served** repo. Gotcha: publishing to the wrong repo dir (one with empty `metadata/`) silently produces an **empty index** — publish to the repo whose `metadata/<pkg>.yml` and fingerprint the phone already trusts. Version comes from `app.json` (`expo.version` + `android.versionCode`), not `build.gradle`. [^10][^11]

## Pre-flight: silent-failure table

| Symptom | Root cause | Fix |
|---|---|---|
| `undefined is not a function` on a bridge call | `expo prebuild` regenerated `android/`, dropped hand-added code | Move `.so`+`.kt` outside `android/`, re-copy via config plugin (§1) |
| App crashes at startup on emulator | native libs loaded in class-init on an ABI with no `.so` | Load lazily on `setup()`; reject gracefully (§2) |
| Works in debug, SIGSEGV in release | JVM attach was inside `assert()`, stripped by NDEBUG | Attach unconditionally, once per thread (§4) |
| Node "offline" forever on Android | DnsResolver UDP blocked → assumed offline | Patch `online_monitor.nim` assume-online at 0 peers (§7.3) |
| `waku_new` rejects config / node offline | light-client fields present | Minimal relay config only (§5) |
| Relay gets traffic, phone sees nothing | subscribed a content topic as a pubsub topic → non-existent shard | Wire the content-topic/auto-shard JNI call (§6) |
| `chan > 0` but `ours: 0` | channelCreate didn't subscribe; or single- vs double-base64 mismatch | JS: pair `subscribeContentTopic`+`channelCreate`, double-encode/decode (§7.1–7.2); mechanics in **logos-reliable-channels** |
| Metrics shows 0 peers but sync works | `libp2p_peers` gauge under-reports | Trust message receipt, not the gauge; never read 0 as "offline" (§9) |
| Both nodes "up," neither receives | different pubsub shard | Compare `/waku/2/rs/x/y` from Metrics (§9) |
| Phone accepts a stranger's "update" | signed with shared debug key | Real release signing plugin (§10) |
| F-Droid index empty after publish | published to a repo with empty `metadata/` | Publish to the trusted served repo (§11) |

## Where else this applies

Nothing here is budget-specific. The pattern applies to any Logos app that puts an embedded Delivery/Waku node in a React Native/Expo Android build for multi-writer, cross-user sync: a **shared calendar** (each participant a writer, invites via a pairing secret → derived content topic), a **field/activity tracker** syncing between a worker's phone and an office dashboard, or a **collaborative notes / Q&A** app where several phones and a headless hub converge on one topic. In every case the load-bearing mechanics are identical: hand-written JNI because there's no autolinkable package and no x86_64 `.so`; a config plugin so `expo prebuild` doesn't wipe the integration; the cross-thread JVM-attach discipline (and the release-only SIGSEGV); a relay (not light-client) node config; content-topic auto-shard subscription; the *mobile* wiring of SDS reliable channels — JS-side subscribe-before-`channelCreate` and the double-base64 discipline across the JNI boundary, with the channel mechanics themselves deferred to **logos-reliable-channels**; the Android DNS-monitor patch; store-pull for catch-up; treating the `libp2p_peers` gauge as a positive-only signal (it under-reports); and real release signing before self-hosting the APK. Swap the topic-derivation scheme and the payload schema; keep the transport playbook.

## Sources & evidence

All paths are in `github.com/vpavlin/kym` unless noted. Memory-file names are provenance, not truth — every claim was re-verified against the code cited.

[^1]: `mobile/native/logosdelivery/jni/logos_messaging_ffi.c` — the JNI shim. Confirms: high-level `logosdelivery_*` wrappers + kernel `waku_*` symbols; raw `waku_stop`/`waku_destroy` not exported (use `logosdelivery_stop_node`/`destroy`); `wk_callback` attaches per-thread with a `pthread_key` detach and the "attach-inside-assert → release SIGSEGV" note; `on_response`'s benign `"on_response-ok"` and the ptr==0 guard in `to_jni_ptr`; the content-topic vs pubsub-topic silent-drop comment on `wakuSubscribeContentTopic`/`wakuRelaySubscribe`; `waku_store_query` StoreQueryRequest shape. Kotlin side: `mobile/native/logosdelivery/android/java/com/receiverandroid/LogosMessagingModule.kt` — RN module name `"LogosMessaging"`, lazy `ensureLibsLoaded()` load order (`c++_shared`→`rln`→`logosdelivery`→jni) and the emulator-crash rationale, `getNodeInfo("Metrics")`. Node config `{mode:"Core",preset:"logos.dev",relay:true,entryNodes}` and the "no light-client fields / auto-shard" rationale, plus renew timer, store-pull, and the `getPeerCount`/`libp2p_peers` under-report (peer gauge read 0 while sync flowed): `mobile/src/lib/delivery.ts` (`ensureNode`, `joinRoute`, `publishSealed`, `getPeerCount`, `storeSync`). Provenance: memory `kym-delivery-ffi-surface`, `kym-android-liblogosdelivery-build`, `kym-senderid-and-shard` (metrics under-report).
[^2]: `mobile/plugins/withLogosDelivery.js` — `withDangerousMod` copies `native/logosdelivery/arm64-v8a/*.so` → `jniLibs/arm64-v8a` and `android/java/**.kt` → `app/src/main/java`; the four-`.so` list; manual package registration `com.receiverandroid.LogosMessagingPackage`; gson `2.10.1`; `expo.useLegacyPackaging=true` with the "System.loadLibrary needs an uncompressed file" comment; header comment "Expo CNG regenerates android/ from scratch." Provenance: `kym-android-liblogosdelivery-build`.
[^3]: `docs/decisions.md` §23 item 4 — the Kotlin `@ReactMethod` bridge (including the channel methods) must live in the source template copied by the plugin, else prebuild drops it → `undefined is not a function`.
[^4]: `mobile/native/logosdelivery/jni/Android.mk` — `liblogosdelivery` as `PREBUILT_SHARED_LIBRARY`; JNI built with `LOCAL_LDLIBS := -llog`, `LOCAL_SHARED_LIBRARIES := logosdelivery`.
[^5]: `mobile/plugins/withLogosDelivery.js` header ("arm64-only, no public x86_64 build") + `LogosMessagingModule.kt` `ensureLibsLoaded` comment naming the x86_64 emulator. Provenance: memory `perun-native-delivery`, `kym-testing-status` (test on real arm64 phone, not emulator).
[^6]: `docs/decisions.md` §23 "Mobile Reliable Channels" + `mobile/src/lib/delivery.ts` — for the *mobile wiring only*: `joinRoute` calls both `subscribeContentTopic` and `channelCreate` (the `ours: 0` symptom when the subscribe is skipped); `publishSealed` double-encodes `fromByteArray(utf8Bytes(sealedB64))` and `payloadCandidates` double-decodes across the JNI boundary; `USE_CHANNELS`, `ours`/`chan` counters. `senderId`/deviceId: `mobile/src/lib/device.ts` (`"dev-"+uuid`, SecureStore). The channel API surface, encryption provider, receive-chain gates, and *why* the double-base64 convention exists are documented in the **logos-reliable-channels** skill, not re-derived here. Provenance: memory `kym-mobile-channels-working`, `kym-mobile-channels-receive-rootcause`, `kym-senderid-and-shard`.
[^7]: `github.com/vpavlin/kym-hub` — `sds-build/logos-delivery-patched/logos_delivery/waku/node/health_monitor/online_monitor.nim`: `updateOnlineState` sets `online = true` when `numConnectedPeers == 0` with the "PATCH (mobile/Android): DnsResolver raw UDP … deadlocks offline" comment; `checkInternetConnectivity` uses `DnsResolver`. Also `docs/decisions.md` §23 item 3. Provenance: `kym-android-liblogosdelivery-build`.
[^8]: `mobile/src/lib/delivery.ts` `storeSync`/`getStoreInfo` — StoreQueryRequest paging, per-peer fallback, `storeInfo` "store: N msg → M ev" surfaced in the Sync card; "works only if the fleet retains our shard" caveat. Provenance: memory `kym-store-pull` (UNVERIFIED until on-device `store: msg>0`).
[^9]: `mobile/plugins/withReleaseSigning.js` — injects `release` signingConfig from `~/.gradle/gradle.properties` (`KYM_STORE_FILE` etc.), debug-key fallback; header explains the shared-debug-key / non-rotatable-cert risk.
[^10]: `scripts/build-apk.sh` — `expo prebuild --platform android`, `app.json` versionCode-vs-build.gradle assertion, `gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a`, copy to `dist/lan/kym-arm64.apk`; version from `app.json`. `mobile/app.json` — `android.package co.logos.kym`, `versionCode`, plugin order.
[^11]: `scripts/fdroid-publish.sh` — served repo `~/fdroid` (via `~/vpavlin-home/fdroid` symlink) carries the trusted `metadata/<pkg>.yml` + fingerprint; the "stale second repo with EMPTY metadata → empty index" warning; `fdroid update --pretty`. Provenance: memory `kym-lan-repo-publishing`.
