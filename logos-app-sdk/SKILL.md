---
name: logos-app-sdk
description: >-
  Turn a Logos/Loam app's mobile engine (the React Native `mobile/src/lib` folder: fold, store,
  sync, crypto, identity) into a reusable SDK repo that other apps mount as a git submodule — with
  history kept, the app's own imports unchanged, and the desktop core module as the SDK's other
  half. Reach for it when a second app wants to read and write the first app's data (Frequencies on
  Scala, a forms app feeding a calendar, contacts linked from another app), instead of copying files.
---

# Make an app's mobile engine an SDK

Two apps that share data must share one copy of the engine. Copied files drift, and in this
ecosystem the mobile fold must stay byte-identical to the desktop C++ fold, so a drifted copy is a
fork of the data format. The fix that worked for Scala (`vpavlin/scala-sdk`, Scala ADR 0025):
move `mobile/src/lib` into its own repo **with its history** and mount it back **at the same path**.

On desktop there is nothing to extract: another Basecamp module already reaches the app's data by
calling its **core module** (declare it in `metadata.json` `dependencies`). The SDK is the phone half
of that contract.

## Before you start

- Is the boundary clean? Everything in `mobile/src/lib` must be engine, not app. UI, notifications,
  widgets, screens move out first (Scala moved `notify.ts` + `widget.ts` to `mobile/src/app/`).
  `grep -l "react-native\"\|expo-notifications\|from \"\.\./" mobile/src/lib/*.ts` finds the leaks:
  a lib file importing from `../` (the app) is the clearest one.
- Hard-coded app identity: anything the lib sends to Loam as *this app* (the app id it registers
  with the shared node, a storage prefix, a signing domain) must become a setter, or every consumer
  appears to Loam as the original app. Scala: `export function setAppId(id)` in `scala-sync.ts`,
  re-exported from the API file; Frequencies calls `setAppId("frequencies")` before any transport call.
- Commit the moves in the app first; the split takes what is committed.

## Recipe

```sh
APP=~/<app>; SDK=~/<app>-sdk
cd $APP
git subtree split -P mobile/src/lib -b sdk-split           # history of just that folder
git clone -q --branch sdk-split $APP $SDK && cd $SDK
git checkout -q -b main && git branch -D sdk-split; git remote remove origin
git tag -l | xargs -r git tag -d                            # the app's tags came along; drop them
git config user.name … ; git config user.email …            # new repos have no identity here
```

In the SDK repo:
1. **Nested submodules.** The split keeps the gitlink (e.g. `loam-transport-pkg`) but not its
   `.gitmodules` entry, which lived at the app's root. Recreate it in the SDK:
   `[submodule "loam-transport-pkg"] path = loam-transport-pkg  url = https://github.com/vpavlin/loam-transport`.
2. **`package.json`** with `"private": true`, `main` = the app-facing API file, and
   **`peerDependencies`** = exactly the versions the app uses (expo-crypto, expo-secure-store,
   AsyncStorage, the noble libs, base64-js, react, react-native; Keycard libs under
   `peerDependenciesMeta` as optional). Peers, not deps: the consuming app owns `node_modules`.
3. **README**: what's in it (file table), how to mount, the peers, the Expo plugin line
   (`./src/lib/loam-transport-pkg/plugins/withDeliveryClient.js`), and the change rule below.
   LICENSE-MIT + LICENSE-APACHE.
4. `gh repo create vpavlin/<app>-sdk --public --source . --push` (public, or consumers' clones fail).

Back in the app, replace the folder with the submodule at the **same path**:
```sh
cd $APP
git rm -r -q --cached mobile/src/lib
git config -f .gitmodules --remove-section submodule.mobile/src/lib/loam-transport-pkg   # old nested entry
git add .gitmodules
mkdir -p ~/sdk-backup && mv mobile/src/lib ~/sdk-backup/worktree \
  && mv .git/modules/mobile/src/lib ~/sdk-backup/gitdir 2>/dev/null   # keep, don't delete
git config --remove-section submodule.mobile/src/lib/loam-transport-pkg 2>/dev/null || true
git submodule add https://github.com/vpavlin/<app>-sdk mobile/src/lib
git submodule update --init --recursive
```
Then `cd mobile && npm test && npx tsc --noEmit` must pass **unchanged**: same path means no import,
test or `app.json` plugin path changes. Build the release APK once to prove Metro resolves it.
Write an ADR in the app ("the mobile engine is a separate package").

## Consuming it from another app

`git submodule add https://github.com/vpavlin/<app>-sdk mobile/src/lib` (or another dir if that app
has its own `src/lib`, e.g. `src/<app>`), install the peers, add the Expo plugin, call the app-id
setter first, then use the API. The consumer stores its own data **inside the host app's data**:
custom fields and `ext` items under its own namespace (Scala ADRs 0005/0021) — never new wire fields
the fold doesn't know. Register with Loam under its own app id; it is a separate app to the user.

## Changing the engine afterwards

- **Two commits, SDK first:** commit + push in the SDK, then bump the submodule in the app and run
  the app's tests there. Parity tests that compile the C++ fold against `engine.ts` stay in the app
  repo (the C++ lives there), so a fold change is only done when the app's `npm test` passes.
- **Every consumer bumps the pointer** to get a fix; nothing updates by itself. List consumers in
  the SDK README.
- **Bumping loam-transport** now happens inside the SDK (it is the SDK's submodule), then the SDK
  pointer in each app (see `loam-update-app`).
- Fresh clones need `--recursive`; CI checkouts need `submodules: recursive`.

## Gotchas

- `git subtree split` on a path that contains a submodule: works, but see step 1 (the `.gitmodules`
  entry is lost) and the app-side cleanup of the old nested entry in both `.gitmodules` and
  `.git/config` — skipping it leaves `git submodule status` complaining about a path that no longer
  exists in the index.
- Moving `.git/modules/mobile/src/lib` aside is required: `git submodule add` refuses to reuse a
  gitdir whose remote is the old one ("a git directory for … is found locally").
- A gitignored generated file (Scala's `fixtures.json`) won't be in the split; regenerate it in the
  app, not the SDK.
- The private-repo trap: if the SDK is private, every consumer clone, CI run and reviewer fails.
