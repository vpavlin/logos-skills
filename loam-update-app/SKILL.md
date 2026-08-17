---
name: loam-update-app
description: >-
  Update a Loam mobile app (kym, qaku, perun, scala, or a new consumer) to the latest
  loam-transport SDK — bump the git submodule, optionally wire the shared-node status UI,
  rebuild the release APK, and publish to F-Droid. Encodes the build gotchas that bite every
  time (expo prebuild --clean wipes local.properties; the shim's entry-file import must stay
  logos-transport; submodule dir-rename gitdir realign). Reach for it whenever an app needs
  to move onto a newer loam-transport commit, or a batch of apps must all land on the same lib.
---

# Update a Loam app to the latest loam-transport

Every Loam mobile app (kym/qaku/perun/scala/…) vendors the shared transport as a **git
submodule** at `mobile/src/lib/loam-transport-pkg` (repo `vpavlin/loam-transport`) behind a
thin re-export shim `mobile/src/lib/loam-transport.ts`. "Update to latest" = move the
submodule pin forward, rebuild, publish. Bumping the pin also pulls whatever the SDK gained:
the identity-first BLE mesh (dormant unless the app arms a radio), the mesh package rename
(`xyz.vpavlin.loam.mesh`), and the shared-node UI (`SharedNodeBanner` / `SharedNodeStatus`).

## Procedure

1. **Bump the submodule** to the target ref (default `origin/main`):
   ```sh
   cd <app>/mobile/src/lib/loam-transport-pkg && git fetch -q origin && git checkout -q <ref>
   cd <app> && git add mobile/src/lib/loam-transport-pkg
   ```
2. **(Optional) wire the shared-node status UI** — see below. Do this the first time an app
   adopts it; skip on a plain lib bump.
3. **Bump the app version** in `mobile/app.json` (`version` + `android.versionCode`).
4. **Build** with `update.sh` (next to this file) — it handles the gotchas:
   ```sh
   bash <skill-dir>/update.sh <app> [ref]        # bumps submodule + prebuild + gradle
   ```
   or manually: `expo prebuild --platform android --clean` → **restore `local.properties`** →
   `gradlew assembleRelease` with `ANDROID_HOME` set.
5. **Merge to master** if on a feature branch, **push**, and **publish** the APK with
   `logos-publish-artifacts` (`publish.sh --apk … --apk-package xyz.vpavlin.<app>`).
6. **Verify** the APK's `package`/`versionName` with `aapt2 dump badging`, and that the
   submodule resolves (`git submodule status` shows the new commit, no leading `-`).

## Gotchas (each has cost us a failed build)

- **`expo prebuild --clean` WIPES `android/local.properties`** → gradle dies with *"SDK
  location not found."* Always recreate it before gradle: `echo "sdk.dir=$ANDROID_HOME" >
  android/local.properties`, and pass `ANDROID_HOME=~/Android/Sdk` to gradle. `update.sh` does this.
- **The shim's entry-file import must stay `logos-transport`.** The lib's entry file is
  `src/logos-transport.ts` (deliberately NOT renamed — renaming it is a lockstep break across
  all apps). The shim reads `export * from "./loam-transport-pkg/src/logos-transport"`. A broad
  `sed s/logos-transport/loam-transport/` wrongly rewrites `src/logos-transport` → a missing
  `src/loam-transport` → *"Unable to resolve module."* Rename the **path**, keep the entry name.
- **Renaming the submodule DIR** (`logos-transport-pkg` → `loam-transport-pkg`) leaves the
  internal gitdir stale (`git submodule status` shows a leading `-`). Realign:
  ```sh
  mv .git/modules/<old-path> .git/modules/<new-path>
  echo "gitdir: ../../../../.git/modules/<new-path>" > <new-path>/.git
  git config -f .git/modules/<new-path>/config core.worktree "../../../../../../<new-path>"
  git config --rename-section "submodule.<old-path>" "submodule.<new-path>"
  git submodule sync
  ```
  A fresh recursive clone rebuilds this correctly from `.gitmodules` regardless — this only
  fixes the *local* checkout.
- **Bumping pulls the mesh package rename** (`co.logos.mesh` → `xyz.vpavlin.loam.mesh`). It's
  self-consistent after a fresh `prebuild` (the `withLoamMesh` plugin registers the new name);
  don't hand-edit `android/`.
- **Keep genuine Logos Delivery references** — the `co.logos.delivery` AIDL/IPC contract and
  the `com.receiverandroid` JNI (pinned by the prebuilt `.so`) are upstream and stay.

## Wiring the shared-node status UI (first adoption)

The SDK owns the "shared node isn't running / not approved" shout so no app hand-rolls it.
Mount **once** near the top of the app's main screen:
```tsx
import { SharedNodeStatus } from "./src/lib/loam-transport-pkg/src/SharedNodeStatus";
// in the main (not loading/modal) view, just under the header:
<SharedNodeStatus appName="Scala" showSync />   // showSync adds a peers/mesh line
```
`SharedNodeStatus` = `SharedNodeBanner` (the tappable node-down/not-approved banner) + a
peer-info refresh poll (keeps peer/mesh counts live) + an optional compact sync line. Apps
that hand-rolled their own banner (e.g. qaku's `ldBanner`) should delete it and use this.

## Where it applies

All `vpavlin/loam-transport` consumers: kym, qaku(-logos), perun, scala (mobile side), and any
new Loam app. scala also has a **C++ desktop** side (vendored `logos_sync`, `logos_transport.hpp`)
that this skill does NOT cover — that rename needs a nix/desktop build to verify.
