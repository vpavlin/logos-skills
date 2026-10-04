---
name: logos-multiplatform-modules
description: "Ship Basecamp modules for more than Linux x86_64 — macOS Apple Silicon (darwin-arm64) and Linux ARM64 — without owning a Mac or an ARM box: build each platform on a GitHub runner from pinned flake refs, then MERGE the per-platform .lgx files into ONE package per module (lgx merge) with the existing platform's files proven byte-identical, and republish the SAME version. Use when someone asks 'does it run on Mac / ARM / Windows?', when adding a platform to an already-published module, or when a CI build fails on a non-Linux platform. Covers: the platform-modules workflow (bundled), the merge script (bundled), the blockers that stop a flake building off its home machine (local path: inputs, a relative path:../core between subflakes), the manifestVersion mismatch that makes lgx merge refuse, the darwin pcsclite include fix for keycard-qt, which platforms Basecamp itself ships (no Intel Mac; Windows only from builder 0.3.x), and republishing a same-version multi-platform package publicly."
---

# Ship a Basecamp module for more platforms

A Basecamp `.lgx` is a tarball with one `variants/<platform>/` directory per platform plus a
manifest whose `main` maps each platform to its library and whose `hashes` cover every file. So
"supporting macOS" is not a new package: it is **the same package, same version, with one more
variant inside**. Basecamp picks the variant matching the machine it runs on. That shape decides the
whole recipe: build the variant where it can be built, merge it in, republish.

You do not need the hardware to build. GitHub's hosted runners cover the platforms Basecamp ships
for. You do need it to *test* — say so whenever you publish an untested platform.

## Which platforms are worth building

Check what Basecamp itself is released for before building anything — a module for a platform
Basecamp doesn't run on is wasted work:[^bc]

```sh
gh release view <tag> --repo logos-co/logos-basecamp --json assets -q '.assets[].name'
```

| Platform | `lgx` variant | Runner | Notes |
|---|---|---|---|
| Linux x86_64 | `linux-amd64` | your box / `ubuntu-latest` | the base everything else merges into |
| macOS Apple Silicon | `darwin-arm64` | `macos-14` | Basecamp ships an aarch64 `.dmg` |
| Linux ARM64 | `linux-arm64` | `ubuntu-24.04-arm` | Basecamp ships an aarch64 AppImage |
| Intel Mac | `darwin-amd64` | — | **skip:** Basecamp ships no Intel-Mac build |
| Windows x86_64 | — | cross from Linux | only modules built with **module-builder 0.3.x** (it adds the `x86_64-windows` cross target); a 0.2.x stack must be upgraded first, and each native dependency must cross-build too |

## The recipe

1. **Make every module buildable off this machine** (see blockers below). The test: the Linux
   package you rebuild from the GitHub ref is **byte-identical** to the one you already published.
   That proves the ref is the real source of what users have, so the new platform is built from
   the same thing.
2. **Build the other platforms on GitHub.** Copy `platform-modules.yml` (next to this file) into
   a repo's `.github/workflows/`. Each line of the `modules` input is `name=flakeref [extra nix
   args]`; pin commits. Run once per platform:
   ```sh
   gh workflow run platform-modules.yml --repo <owner>/<repo> -f runner=macos-14 -f modules="$LIST"
   gh workflow run platform-modules.yml --repo <owner>/<repo> -f runner=ubuntu-24.04-arm -f modules="$LIST"
   gh run download <run-id> --repo <owner>/<repo> -D out-<platform>
   ```
   One module failing does not stop the rest: the summary says OK/FAILED per module and every
   build log's tail is in the artifact. Cold builds of a delivery/storage chain take ~15 min per
   heavy module; everything else comes from the binary cache in seconds.[^ci]
3. **Merge** each module with `lgx-merge-platforms.sh` (next to this file):
   ```sh
   LGX=~/lgxtool/bin/lgx lgx-merge-platforms.sh -o merged/logos-<name>-module.lgx \
       published/logos-<name>-module.lgx out-macos/<name>.lgx out-arm/<name>.lgx
   ```
   It aligns `manifestVersion`, refuses a name/version mismatch, and fails unless the base
   package's variants come out byte-identical. Get `lgx` with
   `nix build github:logos-co/logos-package -o ~/lgxtool`. `lgx verify` saying "Package is
   unsigned" is normal for these packages.
4. **Republish the same version** to every repo users install from (`logos-publish-artifacts`; `ALLOW_SAME_VERSION=1 publish.sh …`, since the script otherwise refuses a same-version package with new contents).
   Because the version doesn't change, overwrite the existing file *and* its index entry — see
   that skill's "same-version republish" section — then download each published file and check
   its sha256 against the index.
5. **Say what's untested.** A platform built on CI but never run is "published, untested on a
   real <machine>". Name the likely failure points (OS permissions, hardware access).

## Blockers that stop a flake building anywhere but home

- **`path:/home/...` inputs.** A flake input pointing at a local checkout builds only on that
  machine. Push the dependency (a fork branch is fine) and point the input at
  `github:<owner>/<repo>/<rev>` — then confirm the Linux rebuild is byte-identical. If it isn't,
  the local checkout had something GitHub doesn't, and you just learned what your published
  package was really built from.[^path]
- **`path:../sibling` between subflakes of one repo.** A view flake taking its core as
  `path:../core` resolves only inside a full local checkout; CI fetching `?dir=module` can't
  follow it. Point it at `github:<owner>/<repo>/<rev>?dir=core`: commit the core change, push,
  then lock the view to that commit.[^path]
- **A dependency that doesn't build on the new platform**, with your module otherwise fine. Fix
  the dependency on a fork branch and build yours with
  `--override-input <dep> github:<fork>/<rev>` (the workflow's "extra nix args"). That leaves
  your own flake.lock alone, so the Linux build doesn't change.[^kc]

## Known platform failures and fixes

- **keycard-qt on macOS: `fatal error: 'pcsclite.h' file not found`.** nixpkgs' pcsclite
  installs `winscard.h` under `include/PCSC/`, which `#include`s `<pcsclite.h>` from the same
  directory. Add that directory for darwin only, so Linux stays byte-identical:
  ```nix
  env.NIX_CFLAGS_COMPILE = lib.optionalString pkgs.stdenv.isDarwin
    "-I${lib.getDev pkgs.pcsclite}/include/PCSC";
  ```
  The module then loads on macOS but **sees no card reader** (there is no pcscd). Real support
  means Apple's own PCSC framework.[^kc]
- **`lgx merge` refuses: manifests differ.** Packages built with different builder revs stamp
  different `manifestVersion` values (seen: Linux 0.3.0, macOS 0.6.0 and 0.2.0 from the same
  sources). Basecamp also *requires* a current value on install ("Package is unsigned" is the
  misleading error), so align to the value of the base package you already ship and know works.
  The merge script does this.[^mv]
- **`lgx merge` refuses: contracts differ (builder 0.3.x).** Packages from builder 0.3.1 carry the
  module's LIDL contract under `assets/lidl/`, and merge refuses packages whose contract bytes
  differ. Build every platform with the **same builder rev** (the same flake.lock), not "whatever the
  runner resolved".[^lidl]
- **A library the host doesn't ship.** The portable bundle copies non-Qt libraries (OpenSSL, Boost)
  and assumes the host provides Qt — but Basecamp's AppImage ships ~80 Qt libraries and not, for
  example, QtBluetooth, so a BLE module failed to load (`libQt6Bluetooth.so.6: cannot open shared
  object file`) and took every module depending on it down with it. Before shipping, compare each
  module's `NEEDED libQt6*` (`readelf -d`) against the target Basecamp's bundled libs
  (`--appimage-extract`, then list `usr/lib`). Fix in the build (bundle the library with
  RUNPATH `$ORIGIN`), or patch a built package with `lgx add --variant <v>` — it replaces the whole
  variant, so pass all the old files plus the new one; it recomputes the hashes. Check the other
  platforms' variants too: each needs its own copy of the library.[^qtbt]
- **The library inside differs by platform.** If the Linux package was built from a patched or
  local fork of a native library and CI builds upstream, the platforms now run different code.
  Record which build each variant came from; publish the patched fork so it's reproducible.[^fork]

## Where else this applies

Any Basecamp module or app chain: a core + view pair, a chain of dependent modules (delivery →
storage → transport → app), or a single third-party module you want on more machines. The same
build-then-merge path takes in Windows once the stack is on a builder that cross-builds it.

## Sources & evidence

[^bc]: Official `logos-co/logos-basecamp` releases 0.2.3 and 0.3.0 (checked 2026-10-01): aarch64 + x86_64 AppImage, aarch64 `.dmg`, and an x86_64 Windows installer from 0.3.0 on. No Intel-Mac build. Module-builder `lib.common.systems`: 0.2.6 = darwin aarch64/x86_64 + linux aarch64/x86_64; 0.3.1 adds `x86_64-windows`.
[^ci]: `vpavlin/loam-basecamp` `.github/workflows/macos-modules.yml` (source of the bundled `platform-modules.yml`); runs 36835969518, 36839425578 (macOS) and 36841280630 (Linux ARM64) built the 8-module Scala chain (delivery_module, storage_module, keycard, keycard-ui, ble_mesh, loam_core, scala, scala_ui), published 2026-10-01 as three-platform packages at the same versions.
[^path]: `loam_core` took delivery_module as `path:` to a local checkout → published as `github:vpavlin/logos-delivery-module/loam-0.1.4`. Qaku's core took `loam_core` by local path and its view took the core as `path:../qaku_core` → both repointed to GitHub refs; Linux rebuilds were byte-identical to the published qaku_core 0.1.22 and qaku 0.1.28 (vpavlin/qaku-logos `ci/platforms`, 2026-10-01).
[^kc]: `vpavlin/keycard-basecamp` branch `macos-build` (27c0cdf): the darwin-only `NIX_CFLAGS_COMPILE` line for keycard-qt; loam_core built with `--override-input keycard github:vpavlin/keycard-basecamp/27c0cdf…`.
[^mv]: Memory `lgx-manifestversion-0-3-0` (Basecamp refuses manifestVersion 0.2.0); macOS CI stamped 0.6.0 (storage_module) and 0.2.0 (keycard, keycard-ui) where the Linux packages say 0.3.0.
[^fork]: delivery_module 0.1.4 on Linux bundles `liblogosdelivery v0.38.1-ge91aaa` (a locally patched build with Android fixes); the macOS/ARM variants bundle upstream `8ad99f1`. Functionally equivalent on desktop, recorded in `loam-basecamp/docs/delivery-upstream-vs-fork.md`.
[^lidl]: `loam-basecamp` `port/0.3` `docs/port-0.3/analysis-basecamp-builder.md` § Packages ("0.3.1 ships contracts in `assets/lidl/`; `lgx merge` refuses differing contract bytes → same builder on every platform").
[^qtbt]: Memory `ble-mesh-qtbluetooth-bundle` (ble_mesh 0.1.0 failed on stock Basecamp 0.3.1; 0.1.1 bundled `libQt6Bluetooth.so.6` from qtconnectivity 6.9.2 with RUNPATH `$ORIGIN` via `lgx add`, load-tested against the extracted AppImage libs; ble_mesh 0.2.0 bundles it from the flake, memory `port-0-3`). The scan of 20 modules found it the only missing Qt lib.
