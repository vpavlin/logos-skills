---
name: logos-fdroid
description: "Run a self-hosted F-Droid repository for Logos Android apps and keep updates flowing: create the repo, give every app its metadata (or the index comes out empty), never pin CurrentVersionCode, advertise a plain-http repo_url (an https self-signed one breaks adding the repo on new phones), share the repo by fingerprint link + QR, keep the app signing key separate from the index key, declare optional hardware as required=false, keep enough old versions, split public and private repos, and verify the SERVED index. Use when setting up a repo, publishing an APK, or debugging 'the app is there but no update is offered', 'SSL handshake error when adding the repo', 'app not compatible with this device', '404 downloading the APK', or 'failed to verify'. Publishing mechanics (publish.sh) live in logos-publish-artifacts; this skill is the F-Droid side in depth."
---

# Self-hosted F-Droid for Logos apps

Our Android apps don't go to Google Play or the main F-Droid repo; they're served from small
self-hosted F-Droid repos (a household LAN box, a public GitHub Pages site). A repo is a directory of
APKs plus an index the client downloads and verifies. Almost every failure is silent: the publish
"succeeds" and the phone never sees the update. These are the rules that stop that.[^why]

## Two keys, two jobs

- **The index key** signs the repo's index. It is the repo's identity: a phone trusts a repo by the
  SHA-256 fingerprint of this key. One key per repo; `fdroid update` uses it automatically.
- **The app signing key** signs the APK. It is the app's identity *forever*: Android refuses an
  update signed by a different key, and the only way past is uninstalling (losing the app's data and
  keys). Sign every release of an app with the same release key, from outside the repo (gradle
  properties in `~/.gradle`), never the Expo debug key (`logos-mobile-app` § 10).

They are independent. The same signed APK can be published into several repos, each re-signing
only its own index. Check an APK's signer before publishing:
`apksigner verify --print-certs app.apk | grep 'Signer #1 certificate DN'`.[^keys]

## Set up a repo

```sh
mkdir ~/fdroid-myrepo && cd ~/fdroid-myrepo && fdroid init      # creates config.yml + the index keystore
```

In `config.yml` set `repo_url`, `repo_name`, `repo_description`, `repo_keyalias`. Serve the
directory with any static file server; the URL path must end in `/repo`.

**`repo_url` must be plain `http://` unless the host has a publicly trusted certificate.** F-Droid
doesn't need TLS — authenticity comes from the signed index plus the fingerprint. And the client
uses the address **baked into the signed index**, not the one you typed: an `https://` repo_url with
a self-signed certificate makes adding the repo fail on every new phone with an SSL handshake error
(old phones work only because someone installed the certificate once). Use a stable hostname, not a
LAN IP that changes when the box moves networks. Changing `repo_url` and re-running `fdroid update`
keeps the same index key, so existing installs stay valid; remove and re-add the repo on phones that
cached the old address. (A Basecamp repo is the opposite: it *must* be https. Serve both from one
box on two ports if needed.)[^http]

## Share it: link, fingerprint, QR

```sh
keytool -printcert -jarfile repo/index-v1.jar | grep SHA256      # the repo fingerprint (strip the colons)
echo "http://<host>:<port>/<path>/repo?fingerprint=<64-hex>"     # the add-repo link
qrencode -o repo-qr.png "http://<host>:<port>/<path>/repo?fingerprint=<64-hex>"   # or any QR generator
```

Always share the URL **with** `?fingerprint=` (the full 64 hex characters): it pins the index key so
nobody on the path can substitute a repo. A QR of that link is the easiest way onto a phone (F-Droid's
"Add repository" scans it); typed or pasted long URLs get corrupted. Two repos have two fingerprints
— they are not interchangeable.[^share]

## Every app needs metadata

`fdroid update` **silently drops an APK that has no `metadata/<applicationId>.yml`** — the index
comes out without the app. Minimal file: `Name`, `Summary`, `Description`, `Categories`,
`AuthorName` (add `WebSite`/`SourceCode` for the storefront). YAML-quote names and summaries that
contain `:` or `#`.[^meta]

## Never pin `CurrentVersionCode`

F-Droid offers only the index's *suggested* version as an update. With no `CurrentVersionCode` in
the metadata, `fdroid update` suggests the highest real APK — correct. **Any pin strands updates:**
a stale low value suggests an old build (the new APK sits in the index, never offered), and a
placeholder like `2147483647` matches no APK, so nothing is offered at all. Remove the line on every
publish (the bundled `publish.sh` does), and check the served index's `suggestedVersionCode`
equals your newest versionCode. Bump `android.versionCode` in `app.json` for every release.[^cvc]

## Optional hardware must be `required="false"`

F-Droid hides an app on a device that lacks a feature the APK *requires* ("not compatible"). A
config plugin that adds NFC for a hardware key (`android.hardware.nfc.hce`) or a camera for QR scans
must declare it `android:required="false"`, in the plugin itself so `expo prebuild` keeps it. Check
with `aapt2 dump badging app.apk | grep uses-feature` and confirm the served index lists no
`features` for the app.[^feat]

## Keep enough versions

Clients cache the index. If you delete an APK the moment a newer one lands, a phone with an index a
few hours old asks for a file that's gone (404) until the user refreshes. For a repo other people
use, keep several versions per app (versioned file names plus `archive_older: N` in `config.yml`,
N ≈ 3–5). The bundled `publish.sh` instead overwrites one stable file per app
(`<applicationId>.apk`), which suits a private repo where you refresh the phone yourself; a stale
client there sees a hash mismatch instead of a 404 — same fix, refresh.[^prune]

## Signature schemes

A `minSdkVersion` of 24 or higher makes the Android build drop the v1 (JAR) signature, and some
F-Droid clients then report "failed to verify". Force `enableV1Signing true` in the release
signing config and verify with `apksigner verify --min-sdk-version 19 -v app.apk` (without the flag
apksigner misreports v1 as absent).[^v1]

## Public and private repos

- **Private (household / LAN / mesh):** http + fingerprint, its own index key, test builds welcome.
  Remember which directory is served at which URL — a box may host several repos with different
  keys, and publishing into one the phone hasn't added "works" and changes nothing.
- **Public (e.g. a GitHub Pages site):** a real certificate, so https is fine; its own index key;
  only releases you'd hand to strangers. Pages and raw-file CDNs cache for minutes — verify after
  the cache expires.
- One app in two repos = the **same signed APK** dropped into both, each repo's own `fdroid update`.
  Never re-sign the APK with a repo's key.[^keys]

## Publish flow

1. Build the release APK (`logos-mobile-app`; `loam-update-app` for Loam apps). Verify its signer
   and the v1/v2 signatures.
2. Publish with `logos-publish-artifacts/publish.sh --apk … --apk-package <id>` and
   **`FDROID_HOME=<the repo the phone reads>`** — the default is one specific repo and is easy to get
   wrong. Keep `index-*.json.bak` files out of `repo/` (fdroid tries to scan them).
3. Verify the **served** index (below), then pull-to-refresh on the phone.

## Verify what's actually served

```sh
curl -s http://<host>/<path>/repo/index-v1.json | python3 -c '
import json,sys; d=json.load(sys.stdin); print("address:", d["repo"]["address"])
for a in d["apps"]:
    if a["packageName"]=="<applicationId>": print("suggested:", a.get("suggestedVersionCode"))
for p in d["packages"].get("<applicationId>", []): print(p["versionCode"], p["apkName"], p.get("features"))'
```

Check: `address` is the http URL you share; `suggestedVersionCode` is the newest build; `features`
is empty. Modern clients read `entry.json` → `index-v2.json`: confirm both were rewritten with
`index-v1.json` (`fdroid update` has been seen leaving v2 stale, which no client refresh can fix —
`publish.sh` checks this).[^v2]

## Symptom → cause

| Symptom | Cause | Fix |
|---|---|---|
| App missing from the repo | no `metadata/<id>.yml` | add it, `fdroid update` |
| App there, no update offered | `CurrentVersionCode` pinned (stale or max) / index-v2 stale / versionCode not bumped | remove the pin; regenerate; bump |
| "SSL handshake error" adding the repo on a new phone | `repo_url` is https with a self-signed cert | http `repo_url`, regenerate, re-add |
| "Not compatible" on some devices only | a required `uses-feature` the device lacks | `required="false"` |
| 404 downloading an APK | client index older than your pruning | refresh; keep more versions |
| "Failed to verify" | no v1 signature (minSdk ≥ 24) | `enableV1Signing true` |
| Update refuses to install | APK signed with a different key than the installed app | sign with the app's original key |
| Publish succeeded, phone sees nothing | published to a repo the phone hasn't added | publish to the repo whose fingerprint the phone trusts |

## Sources & evidence

[^why]: Ecosystem repos: a LAN/mesh repo per app family and the public `apps.vpavlin.xyz` F-Droid repo (GitHub Pages). Publisher: `logos-publish-artifacts/publish.sh`.
[^keys]: `logos-publish-artifacts` § "The signing rule that decides whether an update can install at all"; memory `loam-fdroid-repo-target` (two repos on one box, different index keys, different fingerprints).
[^http]: Memory `fdroid-repo-http-not-https` (2026-08-15: SSL handshake error on a new phone; root cause = the signed index's canonical address was https with a self-signed cert; fixed by an http `repo_url`; re-signing kept the fingerprint) and `crib-mesh-hostname` (LAN IP churn → use a stable mesh hostname).
[^share]: Fingerprint command from memory `fdroid-repo-http-not-https`. Link corruption of long pasted URLs: memory `rln-readiness-plan` (a ~720-char invite link broke on paste; QR fixed it) — the same applies to repo links.
[^meta]: `publish.sh` (auto-creates a minimal metadata file; YAML-quotes Name/Summary, commit `a0345c8`); memory `kym-lan-repo-publishing` (empty index after publishing to a repo with empty `metadata/`).
[^cvc]: Memory `fdroid-currentversioncode-trap` (stale pin = no update; the `2147483647` placeholder = no update at all, confirmed on a phone 2026-09-11 for five apps; removing the pin fixed all). `publish.sh` strips the line on every publish.
[^feat]: Memory `android-uses-feature-incompatible` (scala "not compatible" on a tablet: `android.hardware.nfc.hce required="true"` from the keycard config plugin; fixed in scala 0.9.106).
[^prune]: Memory `apps-storefront-fdroid-prune-404` (public repo publisher keeping 2 versions → 404 for stale clients). `archive_older` is the standard fdroidserver `config.yml` option.
[^v1]: `loam-keycard` § silent traps (minSdk 24 drops v1 → F-Droid "failed to verify"; `--min-sdk-version 19` for an honest apksigner report).
[^v2]: `publish.sh` index-v1/v2/entry staleness check; served-index check from memory `fdroid-currentversioncode-trap`.
