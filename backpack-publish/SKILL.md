---
name: backpack-publish
description: "Publish Android APKs to a Backpack repo (app repos that live in Logos Storage: a signed index, APKs fetched by content hash, updates announced by a signed pointer), as an agent or a build pipeline. Use after building an APK that should reach phones through Backpack, when asked to 'publish to Backpack', 'add this APK to the repo', 'mirror an F-Droid repo into Backpack', 'make the repo private', or to check a Backpack repo the way a phone would. Covers the two front ends (the Basecamp module backpack_core driven with logosctl, and the backpack CLI), the order of operations (sign, add, publish, verify), the checks that make a publish fail (signer change, same versionCode, an index over 1 MB, an unreachable Storage node), private repos and why their link is a secret, F-Droid import by fingerprint, and who may publish where."
---

# Publishing to a Backpack repo

[Backpack](https://github.com/vpavlin/backpack) is an app store whose repos live in **Logos Storage**.
A repo is an ed25519 key. Publishing uploads the APKs (and icons) to a Storage node, writes an
**index** that lists every app, version, SHA-256 and pinned signing certificate, signs it with the
repo key, uploads it, and sends a small signed **pointer** (the newest index's CID and a `seq`) to the
places phones look: web addresses and a Logos Delivery topic. Phones refuse anything the repo key didn't
sign, any `seq` lower than they have, any APK whose SHA-256 or signer differs from the index.[^model]

So an agent's job is short, but the order matters and most failures are refusals by design.

## Before you start

- **The APK must be signed.** Backpack does not sign APKs; Android installs only signed APKs, and
  Backpack pins each version's signing certificate. Every version of a package must carry the **same**
  signer: a different key is refused at add time (Android would refuse the update anyway). Never sign
  with a key you were not given for that app. If your environment has a release/publisher agent that
  holds the signing keys, hand it the **unsigned** APK and let it sign and publish.[^sign]
- **`versionCode` must be new.** The same package + versionCode with different bytes is a different
  APK under an old number; phones treat the higher versionCode as the update. Bump it in the build.
- **Know which repo you may publish to.** A repo's key lives with whoever runs it
  (`<data dir>/<repo>/key.pem`). Publish only to repos you were told to; never copy or print the key.
- **Public vs private.** A private repo (ADR 0009) encrypts the index, pointer and every file; its
  invite link carries the **read key** (`x=`). Treat that link like a password: never paste it into
  logs, issues, chats or task summaries you don't control.

## Path A: the Basecamp module (backpack_core) with logosctl

`backpack_core` is the same engine as the Basecamp app, headless. Repos live in `$BACKPACK_DIR`
(default `~/.backpack`), the same layout the CLI uses, so both front ends can take turns on a repo.

```sh
# a session for the module (see logos-headless-logosctl for sessions)
logosctl daemon start --detach
logosctl package install -y backpack_core.lgx        # or from a Basecamp repo
logosctl module load backpack_core

logosctl call backpack_core snapshot                 # repos (no secrets) + jobs
logosctl call backpack_core addApk  str:<repo> str:/abs/path/app-release-signed.apk
logosctl call backpack_core publish str:<repo> str:0  # str:1 = publish even if nothing changed
logosctl call backpack_core verify  str:<repo> str:1  # deep: download, decrypt, check every APK
```

- `addApk`, `publish`, `verify` and `importFdroid` are **jobs**: they return `{"ok":true,"job":N}`
  at once. Poll `snapshot` until that job's `state` is `done` or `failed`; its `log` says what happened
  and `result` carries the outcome (`publish`: the new `seq` and index CID; pointer targets each with
  ok/failed).
- **One job per repo at a time**: a second call returns `"This repo is busy with another job"`.
  Add several APKs one after another, then publish once.
- Every method returns `{"ok":false,"error":"…"}` instead of throwing; report the error text.
- Other calls: `createRepo(slug, name, private "0"|"1")`, `repo(slug)`, `link(slug)` (the invite link
  + QR; a private one contains the read key), `makePrivate`, `rotateReadKey`, `configure(slug, json)`,
  `removeVersion(slug, appId, versionCode)`, `importFdroid(slug, url, fingerprint, versions)`.

## Path B: the CLI

```sh
cd backpack/cli && npm install
node backpack.mjs --repo <repo> add path/to/app-release-signed.apk
node backpack.mjs --repo <repo> publish
node backpack.mjs --repo <repo> verify --deep
```
The CLI needs the Android build-tools (`aapt2`, `apksigner`) and, for F-Droid import, a JDK
(`jarsigner`, `keytool`). The module reads APKs and verifies signatures and JARs itself.

## Mirroring an F-Droid repo

`importFdroid(repo, url, fingerprint, versions)` (CLI: `import-fdroid <url> --fingerprint <sha256>
--versions N`) takes apps from an F-Droid **v2** repo only through its signed index: `entry.jar` must be
signed (SHA-256, not SHA-1 only) by the certificate whose SHA-256 you pass; `entry.json` pins
`index-v2.json`; every APK and icon must match its SHA-256 and signer; an `entry.json` older than one
already seen from that repo is refused (rollback). Get the fingerprint from the repo's owner, not from
the repo's own web page alone. Import, then publish.

## After publishing: verify like a phone

Always run a deep verify and report it: every pointer address answers with the new `seq`, the index
opens with the repo key (and read key), and every listed APK downloads, (decrypts,) matches its size,
SHA-256, pinned signer, package and versionCode. A publish that "succeeded" but whose pointer push
failed is visible in the publish result's `pointers` list; phones then see the update only through the
other sources.

## Refusals and what they mean

| Message | Cause | Do |
|---|---|---|
| `signed by X…, earlier versions by Y…; refusing` | the APK's signer differs from the app's previous versions | sign with the app's real key; never "fix" it by removing old versions |
| `… already in the repo` | same package, versionCode and SHA-256 | nothing to do |
| `the APK has no v2/v3 signature` / `was modified after signing` | unsigned, v1-only, or changed after signing | sign (v2+), don't touch the file afterwards |
| `the signed index is over 1 MB` | too many apps/versions/descriptions | trim descriptions or split the repo |
| `Storage node …: HTTP …` / `upload … failed` | the repo's Storage node is unreachable | check the node in the repo settings; phones fetch from it too |
| `This repo is busy with another job` | a job is running on that repo | wait for it (poll `snapshot`) |
| `entry.jar is signed by …, not …` | wrong fingerprint, or not the repo you think | stop; confirm the fingerprint with the owner |

## Where the files are served from

Phones fetch the index and APKs from the Storage node the repo names (and from anyone caching those
CIDs). A node behind a home router can't serve phones elsewhere (logos-storage: NAT and the
reachable-hub cache), so a repo meant for other people needs a node they can reach. The pointer's web
addresses and Delivery announcement are the repo's own settings; publishing writes to whatever the repo
is configured with.

[^model]: Backpack ADRs 0001-0003 (repo = key, security model, signed pointer), 0007 (pointer over
Delivery), 0009 (private repos), 0011 (the Basecamp app), in the Backpack repo's `docs/adr/` (as of
2026-10-27).
[^sign]: Signing inside Backpack (per-app keys created or imported, sign-on-add, backup) is planned
for the Basecamp app; until then the APK arrives signed (as of 2026-10-27).
