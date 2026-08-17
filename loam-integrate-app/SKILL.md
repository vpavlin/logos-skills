---
name: loam-integrate-app
description: >-
  Integrate a React-Native app as a client of the Loam shared delivery node — consume
  loam-transport, enable the device-wide shared node (so many apps share ONE Waku/Logos node
  instead of each running their own), get the approval prompt, and show shared-node status.
  Encodes the ordering + binding + approval gotchas that cost a full debugging session
  ("shared enabled but runs own node", "no approval prompt", startup crashes). Use when
  adding Loam sync to a new app, or when a client silently falls back to its own node.
---

# Integrate an app with the Loam shared node

The Loam app (`xyz.vpavlin.loam`) runs ONE device-wide delivery node; client apps (kym, qaku,
perun, scala) bind its AIDL service and route through it instead of each embedding a node. This
recipe is the CLIENT side. For bumping an already-integrated app to a newer lib, see
`loam-update-app`; for publishing, `logos-publish-artifacts`.

## 1. Wire the transport (once)

- Add `vpavlin/loam-transport` as a submodule at `mobile/src/lib/loam-transport-pkg` + a
  re-export shim `mobile/src/lib/loam-transport.ts`:
  `export * from "./loam-transport-pkg/src/logos-transport";` (entry file keeps the
  `logos-transport` name — do NOT rename it).
- In `app.json` plugins, add BOTH:
  `"./src/lib/loam-transport-pkg/plugins/withDeliveryClient.js"` (the shared-node CLIENT: native
  module + Android-11 `<queries><package xyz.vpavlin.loam>` + the AIDL) and, for the BLE mesh,
  `withLoamMesh`. Without withDeliveryClient the client module is absent and
  `ServiceNode.available()` is false → the app can only ever run its own node.

## 2. Enable the shared node — ORDERING IS EVERYTHING

`transport.preferServiceBackend(true, "<appid>")` selects the shared backend. **It MUST run
before the first call that wires the backend.** The backend is wired lazily by `ensure()`, which
is **idempotent** — the FIRST `ensure()` locks the choice. Any of these call `ensure()`:
`start`, `publishSealed`, `join`, `storeSync`, `refreshPeerInfo`, `getCtx`, `registerClient`, …

So:

```ts
// read the user's toggle, THEN start — nothing transport-y before this:
const shared = (await SecureStore.getItemAsync("<app>-shared-node")) === "1";
transport.preferServiceBackend(shared, "<appid>");
await transport.start({ deviceId, topics, onReceive, onStatus });
```

**The classic trap:** a status widget (or a peer-count poll) that calls `refreshPeerInfo()` on
mount fires *before* your `preferServiceBackend`, wiring the embedded node first — then the
shared toggle silently does nothing (`serviceDiag()` shows `prefer` flipping true *after* the
node already wired). loam-transport ≥ the re-wire fix corrects this (`preferServiceBackend`
re-wires pre-start), but still: **call `preferServiceBackend` as early as possible**, before any
peer poll or status widget mounts, and never render a shared-node status widget before start.

## 3. The approval prompt (device owner consent)

On the shared backend, `start()` → `Client.connect()` (binds `xyz.vpavlin.loam`'s
`co.logos.delivery.svc.LogosDeliveryService`) → `Client.register(appId)` → the Loam service's
`DeliveryHub.register` → `toJs` → the Loam app's `service-bridge` → **pending → notification +
the Loam app's REQUESTS list**. For this to fire:

- **The Loam app must be installed AND its RN/JS must be RUNNING.** `BIND_AUTO_CREATE` starts the
  native service but NOT the JS; `toJs` is only set while the Loam app is alive (foreground, or
  its keep-alive foreground-service notification holding the JS). So: **launch Loam once** (out
  of the Android "stopped state") and keep its keep-alive notification up.
- The prompt appears as a notification (needs Loam's notification permission) AND in the Loam
  app's REQUESTS list. Approve once; grants persist per (package + signing cert).

## 4. Show shared-node status (drop-in)

Mount ONCE near the top of the main screen — and **import it** (a missing import compiles fine
but crashes at runtime with "SharedNodeStatus does not exist"):

```tsx
import { SharedNodeStatus } from "./src/lib/loam-transport-pkg/src/SharedNodeStatus";
<SharedNodeStatus appName="MyApp" showSync />   // banner + peer-refresh + optional sync line
// add `debug` to show serviceDiag() live: prefer / available / using / resolve / bind / connect.
```

Don't hand-roll the node-down banner (that's how some apps shouted and others forgot).

## Debugging: `serviceDiag()` reads the exact stop

`transport.serviceDiag()` → `prefer=… available=… using=… | target=… resolves=… bindReturned=…
connected=… appId=… | <fallback error>`:

- `available=false` → withDeliveryClient not in app.json / module not registered.
- `prefer=true available=true using=false` → backend wired before the pref (ordering, §2).
- `resolves=NULL` → `<queries>` missing the target package, or wrong `ComponentName` package
  (must be the Loam app id `xyz.vpavlin.loam`, service class `co.logos.delivery.svc.…`).
- `bindReturned=false` → service not exported / not found. `connected=false` → bound, never
  connected (Loam JS not running, §3).

## Gotchas checklist

1. `preferServiceBackend` before ANY ensure()-triggering call (§2) — the #1 bug.
2. Bind target = the Loam app's **applicationId** (`xyz.vpavlin.loam`), not the service's
   class namespace (`co.logos.delivery.svc`). Both live in `withDeliveryClient` + the client
   Kotlin — update together on any Loam-app-id change.
3. `<queries><package xyz.vpavlin.loam>` is mandatory on Android 11+ or the bind is invisible.
4. Loam app must be launched + keep-alive running for the register to reach its UI (§3).
5. `<SharedNodeStatus>` needs its import (runtime crash otherwise).
6. Build: `expo prebuild --clean` wipes `android/local.properties`; gradle OOMs on the native
   CMake compile — see `loam-update-app` (restore local.properties, `--no-daemon --max-workers=2
   -Xmx2g`).

Sources: this recipe distills the 2026-08-15 debugging of the co.logos→xyz.vpavlin/Loam rename;
see memory `loam-shared-delivery-ble`.
