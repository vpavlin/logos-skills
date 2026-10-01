#!/usr/bin/env bash
# Merge per-platform builds of ONE module into a single multi-platform .lgx, safely.
#
#   lgx-merge-platforms.sh -o OUT.lgx BASE.lgx EXTRA.lgx [EXTRA.lgx ...]
#
# BASE is the package you already publish (e.g. the linux-amd64 one); each EXTRA adds its platforms
# (darwin-arm64 from macOS CI, linux-arm64 from ARM CI, ...). What this adds over a bare `lgx merge`:
#   1. aligns each EXTRA's manifestVersion to BASE's — different builder revs stamp different values
#      (0.2.0 / 0.3.0 / 0.6.0) and `lgx merge` refuses mismatched manifests; only manifest.json changes;
#   2. checks name + version match (merging two different releases is never what you want);
#   3. proves BASE's existing variants are BYTE-IDENTICAL in the output, so users already on those
#      platforms get exactly what they had.
# Needs the `lgx` CLI (nix build github:logos-co/logos-package -o ~/lgxtool; LGX=~/lgxtool/bin/lgx).
set -euo pipefail
LGX="${LGX:-lgx}"
[ "${1:-}" = "-o" ] && [ $# -ge 4 ] || { sed -n '2,13p' "$0"; exit 2; }
OUT="$2"; BASE="$3"; shift 3
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
field() { tar xzOf "$1" manifest.json | python3 -c "import json,sys;print(json.load(sys.stdin)['$2'])"; }
WANT_MV=$(field "$BASE" manifestVersion); NAME=$(field "$BASE" name); VER=$(field "$BASE" version)
cur="$BASE"; i=0
for extra in "$@"; do
  [ "$(field "$extra" name)" = "$NAME" ] && [ "$(field "$extra" version)" = "$VER" ] \
    || { echo "!! $extra is $(field "$extra" name) $(field "$extra" version), expected $NAME $VER"; exit 1; }
  fixed="$TMP/extra$i.lgx"
  python3 - "$extra" "$fixed" "$WANT_MV" <<'PY'
import tarfile, io, json, sys
src, dst, mv = sys.argv[1:4]
with tarfile.open(src) as t:
    members = [(m, t.extractfile(m).read() if m.isfile() else None) for m in t.getmembers()]
with tarfile.open(dst, "w:gz") as t:
    for m, data in members:
        if m.name.lstrip("./") == "manifest.json":
            d = json.loads(data); d["manifestVersion"] = mv
            data = json.dumps(d, indent=2).encode(); m.size = len(data)
        t.addfile(m, io.BytesIO(data) if data is not None else None)
PY
  nxt="$TMP/merged$i.lgx"
  "$LGX" merge "$cur" "$fixed" -o "$nxt" -y >/dev/null
  cur="$nxt"; i=$((i+1))
done
cp "$cur" "$OUT"
"$LGX" verify "$OUT" >/dev/null 2>&1 || "$LGX" verify "$OUT" | tail -2   # "Package is unsigned" is normal
mkdir -p "$TMP/a" "$TMP/b"; tar xzf "$BASE" -C "$TMP/a"; tar xzf "$OUT" -C "$TMP/b"
for v in "$TMP"/a/variants/*; do
  p=$(basename "$v"); diff -q -r "$v" "$TMP/b/variants/$p" >/dev/null || { echo "!! $p changed in the merge"; exit 1; }
done
echo "$NAME $VER -> $OUT: $(ls "$TMP/b/variants" | tr '\n' ' ')(base variants byte-identical)"
