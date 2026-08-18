---
name: loam-keycard
description: Use when adding a hardware identity (Status Keycard, NFC smartcard) and/or MULTIPLE authoring identities to a multi-writer Logos/Waku app — so a person (not a device) signs events, on-card, tap-per-sign, key-never-leaves. Reach for it to enrol a card and sign event digests over NFC (the choppu react-native-keycard + keycard-sdk stack), to bind different identities per container (calendar/room/budget) with a per-container→identity registry, to make the card the SAME identity across phone (NFC) and desktop (PC/SC), to enforce "only signed writes count" (signatures-required fold flag), or to sign on the desktop where there is no NFC (a native keycard module's on-card requestSign, or a card-signed delegation cert). Also covers the silent traps: the res.data.cbFuncResponse nesting, the reader-wedge-on-error, the pad32/low-S/compress-pubkey adapters, and the Metro `buffer` bundle break.
---

## What this gives you

A **hardware, portable, per-person identity** for a multi-writer app: the private key lives on a Status Keycard (a JavaCard NFC smartcard), and every event is signed **on-card** with a tap — the key never leaves the chip. Plus a **multiple-identities** model: several keypairs (a built-in software key, extra named software keys, the card) with **one bound per container** (a calendar, a room, a budget), so work and personal can live on keys that never link.

The load-bearing insight: **a multi-writer event is self-describing** — it already carries `pub` / `sig` / `dev` (author pubkey, signature, author id). So *which key signs* is a pure app-level choice. Adopting a card, or per-container identities, needs **no change to the fold, the wire, the CRDT, or the desktop core** — every peer verifies whatever signed each event.[^self] The one exception is *requiring* signatures (below), which changes the fold on both platforms.

This composes on top of [logos-multiwriter-sync](../logos-multiwriter-sync/SKILL.md) (the event-log + fold) — the card is just a `Signer` behind the same seam.

## The stack (get the names right)

The official `react-native-status-keycard` is **archived read-only**. The maintained, Expo-compatible successor is the **choppu** stack: `react-native-keycard` (NFC + secure channel, nitro-modules) + `keycard-sdk` (the applet command set).[^stack] It needs **RN New Architecture** (`newArchEnabled`) — nitro-modules requires it, which forces `minSdkVersion 24`.

Peer deps that actually ship: `react-native-keycard`, `keycard-sdk`, `react-native-nitro-modules`, `react-native-mmkv`, `react-native-get-random-values` (import it FIRST), `buffer`, `@noble/curves`, `@noble/hashes`.

The applet: secp256k1 ECDSA on-card, **signs an arbitrary 32-byte hash** (perfect for an event digest), BIP32/BIP39, PIN blocks after 3 (PUK after 5), max 5 paired clients (secure-channel V1; V2 uses none), and — on a recent branch — BIP340 Schnorr. The private key never leaves the card **except** the EIP-1581 subtree, which is exportable by design.[^applet]

## The card driver — signing a digest, and the three adapters

Drive the NFC lifecycle as a promise: `startNFC` → wait for `onKeycardConnected` → open a secure channel + verify PIN + `signWithPath(digest32, path, false)` → **always `stopNFC` on every exit path**. Concretely: `new NFCCardChannel()`, `new KeycardManager(new PairingStorage())` (an **instance**, not the class — passing the class is the classic "undefined is not a function"[^instance]), then `kManager.runOnSecureChannel(channel, LOADED, {pin, pairingPassword, cardPublicKeys:[AUTH_CERT], skipVerificationUID:[]}, cb)` where the callback runs `cmdSet.signWithPath(...)` → BER-TLV → `new RecoverableSignature({hash, tlvData})` → `{r, s, publicKey}`.[^flow]

The card's raw output needs **three adapters** before @noble / OpenSSL / a desktop verifier will accept it:[^adapters]
1. **pad32** — `r` and `s` come minimal-length big-endian; left-pad each to 32 → a 64-byte compact `r‖s`.
2. **low-S** — the applet may return high-S; normalize to `n − s` when `s > n/2` (canonical / OpenSSL-compatible).
3. **compress pubkey** — it returns uncompressed 65-byte `0x04‖X‖Y`; compress to 33-byte (`0x02/0x03` by Y-parity ‖ X). Do it **by hand** so a @noble API rename can't break it.

The card signs the **raw 32-byte digest** — no re-hash. Feed it exactly the bytes your fold verifies (e.g. `sha256(canonicalEvent)`).

The `pairingPassword` is turned into a 32-byte secret via **PBKDF2-HMAC-SHA256, 50 000 iters, salt `"Keycard Pairing Password Salt"`** (use `@noble/hashes/pbkdf2`, Hermes-safe). `AUTH_CERT` is the Keycard CA public key (secure-channel V2) — the same value keycard-cli / Status use to verify a genuine card.[^flow]

## The silent traps (memorize)

- **Success is nested and unflagged.** `runOnSecureChannel` returns the callback's value under **`res.data.cbFuncResponse`** (NOT `res.data`), and there is **no `res.status==="ok"`** — treat a present `r`/`s`/`pub` as success. The RN/nitro bridge may hand byte arrays across as `{0:.., 1:..}` objects → coerce with `Uint8Array.from(Object.values(x))`.[^nesting]
- **The reader wedges on any error.** After a failed/aborted tap the reader shows "ready" but is not armed (and you'll see "CardIO Error: Error sending command" on the next sign). **Always `stopNFC()` on every exit path** and expose an **abort** (`stopNFC` + reject) so the user is never trapped under a "hold your card" overlay.[^wedge]
- **Metro bundle breaks on `buffer`.** `react-native-keycard` imports `buffer`; the native side compiles fine but the JS bundle fails at `expo export:embed` unless `buffer` is installed. Reproduce release bundling before shipping.[^buffer]
- **NFC antenna position matters** on real hardware — "card doesn't register" is often physical, not code.[^wedge]
- **minSdk 24 drops the v1 APK signature** → F-Droid "failed to verify". Force `enableV1Signing true`, and verify with `apksigner verify --min-sdk-version 19` (without it, apksigner *mis-reports* v1:false). See [logos-mobile-app](../logos-mobile-app/SKILL.md).

## Custody — one mechanism, a security↔convenience slider

Every mode is the same **delegation-cert** primitive:[^custody]
- **tap-per-sign** — the card signs every event (cert `maxSigs ≡ 1`). Most secure; fine when edits are infrequent.
- **delegated `{ttl, maxSigs, scope}`** — the card signs, on-card, a cert `{delegatePub, notAfter, maxSigs, scope, idPub}` authorizing an **ephemeral device key**; the device then signs events off-card with the delegate key + attaches the cert; a verifier checks delegate-sig → cert → card identity, within expiry/count/scope. Re-tap on expiry. Root never leaves; bounded blast radius; revocable (don't renew).
- **exported** — EIP-1581 export, off-card forever (least secure escape hatch).

**Correctness pin:** check cert expiry against the **event's HLC wall clock**, never the local wall clock — otherwise two devices fold the same log to different states and the CRDT diverges. `maxSigs`/`scope` are **fold-enforced** (the app owns the fold); the signature/expiry check is the only thing the verify layer does.

## Multiple identities + per-container binding

Model three kinds — **device** (the built-in software key, always present), **soft** (extra named software keys, to compartmentalise without a card), **keycard** (the enrolled card) — in a small registry, with **one identity bound per container** and a **default** (itself keycard-if-enrolled-else-device, so pre-feature containers behave as before).[^registry] Authoring routes through `authorEvent(containerId, ev)`: resolve the container's identity (its binding, else default) and sign with it — soft/device locally, keycard via the tap flow (throwing on cancel so nothing is authored unsigned).

Make this **generic by injection**: a `SoftKeySeam` (the app's own event signing + key derivation) and an optional `KeycardSeam` (the card session). The registry/binding/default storage is 100% app-agnostic; only the signing is app-specific. Keep the storage prefix stable to preserve existing data.[^registry]

**No silent drops.** If the resolved identity can't write to that container (wrong owner / closed / not a member), refuse **up front** with a clear message that mirrors the fold's own `canAdd`/`canEditExisting` — never store-then-fold-away. And when the UI decides whether a field is editable, check the **container's bound identity**, not one global "me" — else a card-owned container reads as read-only.[^nosilent]

### Requiring signatures (the payoff)
A per-container **`signaturesRequired`** flag (LWW, like any other meta field) makes the fold **DROP any unsigned/unverified event** — "only my card can write here". This is the one change that touches the fold, so it must be mirrored on **both** platforms (mobile fold + desktop core) before any container turns it on, or a stale peer keeps unsigned writes and the two diverge.[^sigreq]

## One card = one identity across phone and desktop

A card key is derived at a **BIP32 path**. If the phone and the desktop derive at *different* paths, the same physical card is *two* identities and a card-owned container isn't writable from both. Align them. A common desktop keycard module derives the path from a **domain string**:[^alignment]
```
idx = SHA256("logos-" + domain) → first 16 bytes as four big-endian uint32, each & 0x7FFFFFFF
sign path (non-exportable):  m/43'/60'/1582'/idx0'/idx1'/idx2'/idx3'
auth/export path (EIP-1581): m/43'/60'/1581'/idx0'/idx1'/idx2'/idx3'
```
Make the mobile signer sign at that **same** `domainToSignPath(appDomain)` (`signWithPath` takes any path). Then the card yields the same key — hence the same address — over NFC and over PC/SC. (Changing an app's signing path changes the derived address → force a clean re-enrol; old card-owned containers are orphaned.)

## Desktop (no NFC): two paths

1. **A native keycard module + a USB PC/SC reader.** The card speaks the identical ISO-7816 APDUs over USB; a native C++/Qt driver (a keycard-go replacement) drives it, and a desktop module exposes on-card signing to other modules — e.g. `requestSign({domain, payloadHash, caller, scheme:"ecdsa"})` → poll `checkSignStatus` → signature, **key never leaves the card**.[^desktop] With a reader this needs **no delegation cert and no cert-aware verify** — the event is directly signed and verifies like any signed event. Poller caveat: wrong-PIN and PIN-lockout stay `pending` (no `failed` status) — add your own timeout. There's usually a sibling `requestAuth`/`deriveKey` that returns an **EIP-1581-exportable** key for encryption/vaults — don't use that for signing identity.
2. **No reader → delegation cert.** The phone (which has the card) issues a card-signed cert for the desktop's delegate key over your sync transport; the desktop signs with the delegate key + attaches the cert. This needs the fold's verify to be **cert-aware** on the desktop (verify `idSig` over the canonical cert by `idPub`, expiry vs `hlc.wall`, `maxSigs`/`scope`).[^custody]

## UI: implicit unlock

Enrol once (PIN + pairing) → the card's address is the identity (persist address/pubkey/pairing; **never persist the PIN**). Then **implicit unlock**: when a locked sign is attempted, prompt the PIN via a modal; that first tap **verifies the PIN and signs** in one go; cache the PIN in memory for the session; a wrong PIN clears the cache to re-prompt. Ship three small components — a PIN gate, a "hold your card" tap overlay (with Cancel → abort), and an enrol sheet (PIN prominent, pairing under "Advanced") — driven by the session's `setPinProvider` / `onState` / `enroll` / `abort`.[^ui]

## Package shape (so the next app is cheap)

Split into app-agnostic layers you can vendor or `npm i`:[^pkg]
- **driver** — `signDigestOnCard(digest, {pin, pairing, path})` + the three adapters + a probe.
- **session** — `createSession({storagePrefix, signingDomain})`: enrol, implicit-unlock, tap-per-sign, PIN cache, an `idle|tap` state bus; returns RAW sig material.
- **paths** — `domainToSignPath` / `domainToKeyPath` (the alignment derivation).
- **identity registry** — `createIdentityRegistry({storagePrefix, soft, keycard})` (device/soft/keycard + per-container binding + default + `authorEvent`).
- **ui** — themeable PIN gate / tap overlay / enrol modal, driven by an injected controller + theme.

A new app then: vendor the files, inject its own `SoftKeySeam` (its event signing) + a keycard session seam + a theme, and pick `signingDomain`.

## Checklist

1. RN New Architecture on; add the choppu deps + `buffer` + `react-native-get-random-values` (imported first); NFC permission via a config plugin; a dev build (not Expo Go).
2. Wrap the NFC flow as a promise; `stopNFC` on **every** exit; provide abort. Apply pad32 / low-S / compress-pubkey; sign the RAW digest.
3. Read the result from `res.data.cbFuncResponse`; coerce `{0:..}` → `Uint8Array`; treat present r/s/pub as success.
4. Enrol once; implicit-unlock PIN modal; cache PIN per session; never persist the PIN.
5. Multiple identities via a registry + per-container binding + `authorEvent`; refuse un-authorable writes up front; check the container's bound identity for edit-ability.
6. Pick `signingDomain` and sign at `domainToSignPath(domain)` so phone + desktop share one identity.
7. For `signaturesRequired`, mirror the fold's drop-unsigned on **both** platforms before enabling it anywhere.
8. Desktop: with a reader → an on-card `requestSign` module (poller needs a timeout); no reader → a delegation cert + cert-aware verify.

---

[^self]: A self-describing event (`pub`/`sig`/`dev`) means the fold resolves the author and verifies whatever signed it — so identity choice is orthogonal to sync. See [logos-multiwriter-sync](../logos-multiwriter-sync/SKILL.md) for the event/fold contract. Provenance: `github.com/vpavlin/loam-keycard` README; scala `docs/adr/0009`.
[^stack]: The archived official lib vs. the choppu successor (`react-native-keycard` 1.0.4 + `keycard-sdk` 3.1.9, nitro-modules/mmkv, new-arch); proven end-to-end on real arm64 hardware. Provenance: memory `keycard-loam-identity` (Phase-2 gate cleared).
[^applet]: `keycard-tech/status-keycard` applet: secp256k1 on-card, signs a 32-byte hash, BIP32/39, PIN/PUK limits, 5-pairing cap (V1), EIP-1581 exportable subtree; BIP340 Schnorr on a recent branch (`SIGN` P2=3). Provenance: `keycard-loam-identity`; Alisher `keycard-basecamp/KEYCARD_SIGNING_MODES.md`.
[^instance]: `KeycardManager` needs a `PairingStorage` **instance** (`new PairingStorage()`); the default export is `{Core, NFCCardChannel, PairingStorage}`. Passing the class → "undefined is not a function" after the tap. Provenance: `keycard-loam-identity` (scala 0.9.16 fix).
[^flow]: `signDigestOnCard` in `loam-keycard/src/keycard.ts`: `runOnSecureChannel(chan, LOADED, {pin, pairingPassword: pbkdf2(pw,"Keycard Pairing Password Salt",50000,32), cardPublicKeys:[AUTH_CERT], skipVerificationUID:[]}, cb→cmdSet.signWithPath(digest,path,false)→new RecoverableSignature({hash,tlvData}))`; AUTH_CERT = the secure-channel-V2 CA key. Provenance: `github.com/vpavlin/loam-keycard`.
[^adapters]: The three adapters (pad32 → 64B compact; low-S `n−s`; uncompressed 65B → compressed 33B by Y-parity) + raw-digest signing. Verified: the card sig verifies under @noble after the adapters. Provenance: `loam-keycard/src/keycard.ts`; memory `keycard-loam-identity` (probe PASS).
[^nesting]: Result nests under `res.data.cbFuncResponse`, no `status` flag; RN bridge hands `{0:..}` objects → coerce to `Uint8Array`. Provenance: `keycard-loam-identity` (scala 0.9.16: the "error" was actually success).
[^wedge]: Reader wedges after any error ("ready" but not armed; "CardIO Error" on next sign) → `stopNFC` on every path + an abort; antenna position matters. Provenance: `keycard-loam-identity` (KeycardExample lessons).
[^buffer]: `react-native-keycard` imports `buffer` → Metro `createBundleReleaseJsAndAssets` fails without it (native compiles fine). Reproduce with `expo export:embed`. Provenance: `keycard-loam-identity` (scala 0.9.14 build).
[^custody]: The custody slider is one delegation-cert mechanism (tap-per-sign = maxSigs 1; delegated = ttl/count; exported = EIP-1581); cert expiry checked vs the event's HLC wall, not local time; maxSigs/scope fold-enforced. The cert layer lives in the shared sync/identity lib (`canonicalCert`/`verifyCert`/`issueCert`/cert-aware `verifyEvent`). Provenance: memory `keycard-loam-identity`; `logos-sync/src/signing.ts`.
[^registry]: `createIdentityRegistry({storagePrefix, soft, keycard})` — device/soft/keycard registry, per-container binding, default (keycard-if-enrolled-else-device), `authorEvent` routing; generic by injecting a `SoftKeySeam` + optional `KeycardSeam`; storage prefix preserves data. Provenance: `github.com/vpavlin/loam-keycard/src/identity.ts`; scala `src/lib/identities.ts`.
[^nosilent]: `assertAuthorable` mirrors the fold's `canAdd`/`canEditExisting` and refuses up front; UI edit-ability must resolve the container's BOUND identity, not one global address. Provenance: memory `keycard-loam-identity` (scala 0.9.25 + the per-identity edit-lock fix).
[^sigreq]: `signaturesRequired` (LWW meta) → fold drops unsigned/unverified events; mirrored in the mobile fold and the desktop core for parity; don't enable before both ship. Provenance: memory `keycard-loam-identity`; scala `engine.ts` + `scala_engine.hpp`.
[^alignment]: `domainToIndices` = `SHA256("logos-"+domain)` → four big-endian uint32 (first 16 bytes) `& 0x7FFFFFFF`; sign subtree `m/43'/60'/1582'`, export subtree `1581'`. Align the mobile `signWithPath` to `domainToSignPath(domain)` so NFC and PC/SC produce one identity. Provenance: `xAlisher/keycard-basecamp` `keycard-core/src/plugin.cpp`; `github.com/vpavlin/loam-keycard/src/paths.ts`.
[^desktop]: A native C++/Qt Keycard driver (`xAlisher/keycard-qt`, PC/SC desktop + Qt-NFC mobile) + a Basecamp `keycard` module exposing `requestSign({domain,payloadHash,caller,scheme})`/`checkSignStatus` (on-card, non-exportable path) and `requestAuth`/`deriveKey` (EIP-1581 export). Poller: wrong-PIN/lockout stay `pending` (no `failed`). Provenance: `xAlisher/keycard-basecamp` `KEYCARD_API.md` / `INTEGRATION_GUIDE.md`; scala `docs/adr/0010`.
[^ui]: Implicit unlock (prompt-on-locked-sign, one tap verifies PIN + signs, PIN cached per session, never persisted) + PIN gate / tap overlay / enrol modal driven by the session. Provenance: `github.com/vpavlin/loam-keycard/src/ui.tsx`; memory `keycard-loam-identity` (scala 0.9.22 implicit unlock).
[^pkg]: Layered package: driver / session / paths / identity-registry / ui, adopted by injecting a soft seam + keycard seam + theme. Provenance: `github.com/vpavlin/loam-keycard` (0.3.0).
