#!/usr/bin/env python3
# Publish the LAN-tested Basecamp modules to BOTH apps.vpavlin.xyz surfaces, additively:
#  1. install repo  ~/logos-apps/basecamp/index.json (+ logos-<name>-module.lgx)
#  2. website/documented catalog = release asset `index` on vpavlin/logos-basecamp-modules
import urllib.parse, json, hashlib, subprocess, ssl, urllib.request, tarfile, io, os, sys, datetime

# Usage: fetch lan.json (LAN index) + cat.json (catalog index asset) into $SP, then
#   publish-public-basecamp.py name1,name2 [--dry] [--catalog-only]; then git push ~/logos-apps,
#   gh release upload index $SP/catout/index.json --clobber, gh workflow run "Build storefront".
SP = os.environ.get("SP") or os.path.dirname(os.path.abspath(__file__))
NAMES = sys.argv[1].split(",")
DRY = "--dry" in sys.argv
CAT_REPO = "vpavlin/logos-basecamp-modules"
APPS = os.path.expanduser("~/logos-apps/basecamp")
RAW = "https://raw.githubusercontent.com/vpavlin/logos-apps/refs/heads/main/basecamp"
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
ctx = ssl._create_unverified_context()

lan = {p["name"]: p["versions"][0] for p in json.load(open(f"{SP}/lan.json"))["packages"]}
pub = json.load(open(f"{APPS}/index.json"))
cat = json.load(open(f"{SP}/catout/index.json" if "--catalog-only" in sys.argv else f"{SP}/cat.json"))
cat_assets = set(json.loads(subprocess.check_output(
    ["gh", "release", "view", "index", "--repo", CAT_REPO, "--json", "assets", "-q", "[.assets[].name]"])))
os.makedirs(f"{SP}/lgx", exist_ok=True)

def upsert(index, name, ver_entry, keep_history):
    for p in index["packages"]:
        if p["name"] == name:
            v = ver_entry["manifest"].get("version")
            older = [x for x in p["versions"] if x.get("manifest", {}).get("version") != v]   # same version → replaced
            p["versions"] = [ver_entry] + (older if keep_history else [])
            return
    index["packages"].append({"name": name, "versions": [ver_entry]})
    index["packages"].sort(key=lambda p: p["name"])

for n in NAMES:
    e = lan[n]; ver = e["manifest"]["version"]
    # The LAN repo is served from THIS box, under a LAN name the box itself may
    # not resolve: fetch via loopback when the name doesn't resolve (TLS is unverified here anyway;
    # the sha256 check below is what guarantees we got the right bytes).
    url = e["url"]
    try:
        import socket; socket.gethostbyname(urllib.parse.urlparse(url).hostname)
    except Exception:
        u = urllib.parse.urlparse(url); url = u._replace(netloc="127.0.0.1:%d" % (u.port or 443)).geturl()
    data = urllib.request.urlopen(url, context=ctx).read()
    assert hashlib.sha256(data).hexdigest() == e["sha256"] and len(data) == e["size"], f"{n}: LAN bytes mismatch"
    path = f"{SP}/lgx/{n}-{ver}.lgx"; open(path, "wb").write(data)
    tag = f"{n}-v{ver}"
    base = {k: e[k] for k in ("size", "sha256", "rootHash", "manifest")}
    # icon (catalog only): baked icon.png → sha-named asset on the `index` release
    icon = None
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        for m in t.getmembers():
            if os.path.basename(m.name) == "icon.png" and m.isfile():
                b = t.extractfile(m).read(); h = hashlib.sha256(b).hexdigest()
                icon = {"path": f"{h}.png", "sha256": h, "size": len(b)}
                open(f"{SP}/lgx/{h}.png", "wb").write(b)
                break
    print(f"{n:18} {ver:8} icon={'y' if icon else '-'}")
    if DRY: continue
    # 1. install repo
    if "--catalog-only" not in sys.argv:
      open(f"{APPS}/logos-{n}-module.lgx", "wb").write(data)
      upsert(pub, n, {"releasedAt": now, "publisherRef": tag, "url": f"{RAW}/logos-{n}-module.lgx", **base}, False)
    # 2. catalog: per-module release holding <name>-<ver>.lgx
    asset = f"{SP}/lgx/{n}-{ver}.lgx"
    if subprocess.run(["gh", "release", "view", tag, "--repo", CAT_REPO], capture_output=True).returncode != 0:
        subprocess.check_call(["gh", "release", "create", tag, asset, "--repo", CAT_REPO,
                               "--title", f"{n} {ver}", "--notes", f"{n} {ver}", "--latest=false"])
    else:
        # Same version re-published (e.g. now multi-platform): replace the asset so the bytes at the
        # URL match the size/sha256/rootHash we write into the catalog.
        subprocess.check_call(["gh", "release", "upload", tag, asset, "--repo", CAT_REPO, "--clobber"])
    url = f"https://github.com/{CAT_REPO}/releases/download/{tag}/{n}-{ver}.lgx"
    ce = {"releasedAt": now, "publisherRef": tag, "url": url, **base, "urls": [url]}
    if icon:
        ce["icon"] = icon
        if icon["path"] not in cat_assets:
            subprocess.check_call(["gh", "release", "upload", "index", f"{SP}/lgx/{icon['path']}", "--repo", CAT_REPO])
            cat_assets.add(icon["path"])
    upsert(cat, n, ce, True)

if not DRY:
    pub["generatedAt"] = now; cat["generatedAt"] = now
    if "--catalog-only" not in sys.argv:
        json.dump(pub, open(f"{APPS}/index.json", "w"), indent=2); open(f"{APPS}/index.json", "a").write("\n")
    os.makedirs(f"{SP}/catout", exist_ok=True)
    json.dump(cat, open(f"{SP}/catout/index.json", "w"), indent=2)
    print("catalog packages:", [p["name"] for p in cat["packages"]])
