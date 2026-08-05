# Publishing a Logos module + the .lgx merkle-hash repack

## The `.lgx` package format

An `.lgx` is a **gzip'd tar**. Only these are allowed at the root: `manifest.json`, `manifest.cose`, `variants/`, `docs/`, `licenses/`. Your flat source files get relocated by the builder under `variants/<variant>/` (e.g. `variants/linux-amd64/`). The `manifest.json` carries `name`, `version`, `type`, `view`/`main`, `icon`, `dependencies`, and a `hashes` map (`{<leaf-dir>…, variants, root}`).[^14]

Variants:
- **Repo/catalog distribution → PORTABLE `linux-amd64`** (`nix build .#lgx-portable`). Every official package (chat_ui etc.) ships portable. A `-dev` `.lgx` in a repo shows **"NOT AVAILABLE"** in Basecamp's package manager — silent, no error. Signatures are NOT the gate (official packages are `[unsigned]` too).
- `lgpm install --file` (a local installer) is a *different code path* and wants `-dev` (`linux-x86_64-dev`). Don't publish based on what lgpm demands.[^15]

## Standing up the repo (static files over HTTPS)

Two JSON files served from one directory:

**`logos-repo.json`** (identity card):
```json
{ "schemaVersion": 1, "name": "my-repo", "displayName": "…",
  "description": "…", "indexUrl": "https://HOST:8444/basecamp/index.json",
  "trustedSigners": [] }
```
The repo URL you add in Basecamp points at **`logos-repo.json`** (the catalog card carrying `indexUrl`), NOT at `index.json`. It MUST be `https://` — the downloader hard-rejects any other scheme.

**`index.json`** (schema 2) — one entry per package, versions newest-first. Per-version fields are computed from the built `.lgx`:
```json
{ "schemaVersion": 2, "repositoryName": "my-repo",
  "packages": [{ "name": "<pkg>", "versions": [{
      "releasedAt": "…Z", "publisherRef": "<pkg>-v<ver>",
      "url": "https://HOST:8444/basecamp/<file>.lgx",
      "size": <bytes>, "sha256": "<of the .lgx file>",
      "rootHash": "<manifest.hashes.root>", "manifest": { …embedded… } }]}]}
```

Generate it by reading each `.lgx`'s embedded manifest (`tar xzOf f.lgx manifest.json`), hashing the file bytes, and emitting the entry. **Regenerate on every `.lgx` change** — the index embeds size/sha256/rootHash; a stale index → Basecamp downloads bytes that don't match → **silent install refusal**. **Bump `version` every republish** — the GUI caches the index and ignores same-version changes.

Serve as a `systemd --user` service (`Restart=always`, `loginctl enable-linger`) — files are served live, no restart after republish. TLS: a **leaf** cert (`basicConstraints=critical,CA:FALSE` + `extendedKeyUsage=serverAuth`) — `openssl req -x509` defaults to `CA:TRUE` on OpenSSL 3 and the host rejects a CA cert used to terminate TLS ("fetch failed"). Verify with **`lgpd`** (logos-package-downloader — the catalog tool Basecamp's repo view uses), not lgpm: `lgpd config init` → `repo add <…/logos-repo.json>` → `repo refresh` → `--json info <pkg>`; compare your `variants` to an official package that IS available (e.g. `chat_ui`). Match theirs (`linux-amd64`).

A single `regen.sh` that builds both `.#lgx-portable`, installs into the served dir, regenerates `index.json`, rewrites `logos-repo.json`, and ensures the service is up is the reliable one-command ship.[^15]

## Hot-swap one `.so` without a full rebuild (merkle repack)

When you patched a single shared lib and don't want a full `nix build .#lgx-portable`, hand-repack — Basecamp/logoscore **verify the merkle root on install**, so this must be exact.[^14]

1. `tar xf old.lgx` → `manifest.json` + `variants/<variant>/*`.
2. Swap the one changed file. **Verify ABI first**: `diff <(nm -D old.so|grep -oE '<prefix>_[a-z_]+'|sort -u) <(nm -D new.so|…)` must be empty.
3. Recompute the `hashes` map — VERIFIED algorithm (reproduces real hashes exactly):
   - file hash = `sha256(file bytes)` hex.
   - leaf dir (e.g. `variants/linux-amd64`) = `sha256( concat over files sorted by name of  name + "\0" + filehash_hex + "\n" )`.
   - parent dir (e.g. `variants`) = `sha256( concat over children sorted by name of  childname + "\0" + childhash + "\n" )` (childname = bare dir name, e.g. `linux-amd64`).
   - root = the parent formula over top-level entries **EXCEPT `manifest.json`** — for a variants-only package that's just `[("variants", variants_hash)]`.
   - `manifest.json` is NOT in the merkle (root excludes it), so its JSON formatting only affects the whole-file sha256, not `rootHash`.
4. Bump `manifest.json` `version`; set `hashes` to the recomputed leaf/parent/root.
5. `tar czf new.lgx manifest.json variants` → `sha256sum` + `stat -c%s` it.
6. Insert a newest-first `index.json` version entry with the new `version`, `url`, `size`, `sha256` (of the `.lgx` file), `rootHash` (= merkle root), and embedded `manifest`.
7. Self-verify: extract the published `.lgx`, recompute the merkle, assert == manifest `hashes`.

Note: patching only `manifest.json` fields (e.g. `main`) does NOT change the `variants/` tree hash, so it's accepted. A *merged multi-variant* `.lgx` DOES fail the hash check (can't recompute the aggregate) — keep one variant per file.[^14]

---
[^14]: memory `logos-lgx-hash-repack` (self-verified in-session to reproduce real 0.2.1 hashes). [^15]: `~/vpavlin-home/regen.sh`, `scripts/gen-lan-repo.sh`, `scripts/serve-lan.sh`, memories `logos-repo-publishing` + `kym-lan-repo-publishing`.