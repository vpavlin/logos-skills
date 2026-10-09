---
name: logos-headless-logosctl
description: "Run Basecamp modules headless on the Logos 0.3.x runtime with logosctl — the CLI that replaces logoscore and logos-hub. Use to stand up an always-on hub (a core module running with no GUI), to reproduce a Basecamp bug without the GUI, to drive a two-node sync test on one machine, or to call/watch a module from a script. Covers isolated sessions (LOGOSCTL_CONFIG_DIR + a per-session HOME), daemon start/stop, install/load, typed call arguments (str:/json:/@file), watch, where the logs are, the host-owned storage_module it autoloads, and the traps (onContextReady calls rejected, HOME-relative module data, rm -rf with a redefined HOME). Keywords: logosctl, logoscore, logos-hub, headless hub, daemon, session, call, watch, Basecamp 0.3."
---

# Headless Logos modules with `logosctl` (0.3.x runtime)

A Basecamp module is a plugin for the Logos Core runtime. Basecamp is one host; **`logosctl`** is the
other: a daemon plus a CLI that loads the same `.lgx` packages with no GUI. Use it for an always-on
hub, for scripted tests, and to reproduce a desktop bug where you can read every log line.

`logosctl` replaces both older tools: the 0.2.x `logoscore` (and its daemon-CLI successor) and the
`logos-hub` wrapper around it. Modules built for the 0.3 runtime (`logos-basecamp-module`, § 0.3.x)
belong under `logosctl`; a 0.2.x stack keeps using `logoscore`.[^why]

## Get it

`logosctl` ships as its own release (the `logos-logoscore-cli` project), as an AppImage and a
tarball — it is **not** inside the Basecamp AppImage. `logosctl --version` prints the commit of every
bundled piece (liblogos, cpp-sdk, protocol, capability/package modules, storage_module); keep it on
the same release line as the Basecamp your users run.[^why]

## Sessions: one directory per node

A session is a directory holding that node's config, installed modules, keyring, data and logs.
Choose it with `--config-dir` or `LOGOSCTL_CONFIG_DIR` (default `~/.logosctl`). Two nodes on one
machine = two sessions. **Also give each session its own `HOME`**: modules write their own state
under `$HOME` (the bundled Storage node uses `~/.logos_storage/`, many app cores use
`~/.<app>`), so two sessions sharing a HOME share — and corrupt — that state.[^sess]

```sh
S=/srv/hubs/node-a                       # absolute path
mkdir -p "$S/home"
export HOME="$S/home" LOGOSCTL_CONFIG_DIR="$S/cfg" QT_QPA_PLATFORM=offscreen
logosctl daemon start --detach           # returns once the daemon accepts commands
logosctl install /path/to/app_core.lgx -y   # repeat per package; dependencies are separate .lgx files
logosctl module load app_core            # loads its declared dependencies too
logosctl status                          # daemon + module health
```

A tiny wrapper (`ctl.sh SESSION args…` that sets the three variables and `exec`s `logosctl`) keeps
commands short and makes it impossible to run a command against the wrong session.

**Never `rm -rf` through a redefined `HOME`.** With `HOME` pointing into a session, `~` and `$HOME`
no longer mean your home directory, and a cleanup like `rm -rf $HOME/..` deletes the wrong tree.
Use literal absolute paths for anything destructive.[^sess]

## Calling methods: typed arguments

```sh
logosctl call app_core snapshot
logosctl call app_core addItem str:Groceries str:'{"qty":2}'   # strings
logosctl call app_core configure json:'{"rln":false}'           # a JSON value
logosctl call app_core importFile @/tmp/payload.json             # argument from a file
logosctl call app_core setLimit 100                              # ints and bools are auto-typed
logosctl watch app_core --event stateChanged                     # stream events
logosctl module ls | show NAME | unload NAME | reload NAME
```

Arguments are typed: bare numbers and booleans become ints/bools, `str:` forces a string, `json:`
passes a JSON value (including `json:{"_bytes":"…"}` for byte arguments), `@file` reads the
argument from a file. **Prefix every string with `str:`** — an id like `1000` or `true` otherwise
arrives as a number or bool, and a method expecting a string gets nothing useful. (This replaces the
0.2.x `logoscore -c 'mod.method(a,b)'` form, which mangled quotes, commas and numbers.)[^call]

Output is human-readable on a terminal and JSON when piped (`-j` / `--human` to force); the last
line is the result. Wrap a `call` in `timeout` in scripts — a call into a busy module can wait for the
full IPC timeout.

## Logs

The daemon log is `$LOGOSCTL_CONFIG_DIR/logs/daemon.log` (older runs rotate to
`daemon_<timestamp>.log`). Module `stderr` lands there too, so give each subsystem a fixed
`fprintf(stderr, "[app] …")` prefix and grep for it (`logos-distributed-debugging`). `-v` shows
the CLI's own debug output.

## What the runtime does for you (and to you)

- **It owns `storage_module`.** The daemon bundles Storage and initialises it at load from
  `$HOME/.logos_storage/config.json` (created with defaults on the public `logos.test` network if
  missing). A module's own `storage_module.init(cfg)` is then refused. To configure Storage on a hub
  (public address, AutoNAT/relay server, bootstrap node), edit that file before starting the daemon,
  and make your core adopt the host's node rather than fail. See `logos-storage`.[^stor]
- **It rejects module-to-module calls made inside `onContextReady()`** ("auth token not recognized"
  in the daemon log). Defer startup calls ~1 s (`logos-basecamp-module`, § 0.3.x). A core that
  "loads fine but never connects" under `logosctl` is usually this.[^ctx]
- **Old-built modules load but can't start.** A module built on builder 0.2.x loads, then its startup
  calls are refused the same way. Rebuild the chain on builder 0.3.1.[^ctx]

## An always-on hub

The hub is the same core the desktop runs, in a `logosctl` session on a box that stays up:

1. One session dir + HOME, as above. Install the **portable** `.lgx` of the core and every dependency.
2. Configure what the GUI would: the core's own config (env vars or a config method your core
   exposes), the delivery `entryNodes` (a hub without them is isolated), and Storage's
   `config.json` if it serves attachments.
3. Give it something to drive it. A GUI keeps a core busy by polling `snapshot()`; a hub has no
   view, so arm a self-drive timer in the core (a `QTimer` on the module's thread, never a
   `std::thread`) — `logos-basecamp-module`, headless-hub gotcha 0.
4. Run `logosctl daemon start` (without `--detach` it stays in the foreground until stopped) under a `systemd --user` unit with `Restart=always` and
   the three environment variables set; `loginctl enable-linger` so it survives logout. Run the
   `install`/`module load` steps from `ExecStartPost` or a one-shot unit.
5. Prove it works on the wire, not just that it loaded: another node writes, the hub's receive
   counter climbs, a third node catches up from it.

The hub gotchas in `logos-basecamp-module` (self-drive, `entryNodes`, matching delivery versions)
apply unchanged. Upgrade a hub **with** its clients — a hub one delivery version behind meshes and
still fails to converge.

**On a server**, run the `logosctl-x86_64.AppImage` with `APPIMAGE_EXTRACT_AND_RUN=1`, so it needs no
FUSE. Put the session variables in one env file, used both by the unit (`EnvironmentFile=`) and by
your own `env $(cat …/env) logosctl …` calls. Load the core and set it up from a small
`ExecStartPost` script that waits for `logosctl status`; quoting JSON inside the unit line breaks
easily. Check the box for port clashes first (`ss -tlnp`): Storage's `listen-port` has to be free
and open inbound. `swamp/hub/vps-hub.sh` does all of this.[^hubsh]

## Two nodes on one machine (a sync test rig)

Two sessions, two HOMEs, same packages. Ports: delivery binds random ports when `tcpPort` /
`discv5UdpPort` are `0` (set them explicitly; don't rely on defaults), so the nodes don't collide.
Point both at the same fleet (or one at the other as an entry node), join the same room on both,
write on A, `call` the read method on B. This rig replaced most GUI testing during the 0.3 port:
every app was proven node-to-node this way before anyone opened Basecamp.[^rig]

For a fully private network (no public fleet, no RLN), use the upstream e2e recipe: delivery
config with `preset:""`, a private `clusterId`, `numShardsInNetwork:1`, loopback `listenAddress`,
fixed ports, and the second node's `staticnodes` pointing at the first. (Recipe from upstream tests;
not yet verified on delivery v0.3.0 by us.)[^rig]

## Where else this applies

Any 0.3.x module: a CI smoke test that installs the release `.lgx`, loads it and calls one method;
a support repro of a user's Basecamp setup (same packages, empty session); a long-running bridge or
indexer that has no UI at all.

## Sources & evidence

[^why]: `loam-basecamp` branch `port/0.3`, `docs/port-0.3/analysis-basecamp-builder.md` § Headless: `logoscore` gone → `logosctl` (logos-logoscore-cli release, not in the AppImage), bundles capability_module, modules_state, package_manager, package_downloader and storage_module 3.0.0 (autoloaded). `logosctl --version` 0.3.1 (`4d47b0e`). The 0.2.x wrapper it replaces: `github.com/vpavlin/logos-hub` (memory `logos-hub`).
[^sess]: Port test sessions `~/port-0.3/sess-*` (`HOME=…/home`, `LOGOSCTL_CONFIG_DIR=…/cfg`, `QT_QPA_PLATFORM=offscreen`; `ctl.sh` wrapper); session dir contents `cache client daemon data keyring logs modules plugins`; Storage state at `$HOME/.logos_storage/`. The rm-rf warning is from memory `port-0-3`.
[^call]: Analysis § Headless (arg typing: ints/bools auto; `str:` / `json:` / `@file` / `json:{"_bytes":…}`); used throughout the port tests (`call scala handleShareLink str:$LINK str:`). The 0.2.x mangling it replaces: memory `logoscore-cli-arg-mangling`.
[^stor]: Memory `port-0-3` finding 2 + `TESTING.md` § Known limitations: the host initialises Storage from `$HOME/.logos_storage/config.json` (`loadConfigOrDefault`) on public `logos.test`; an app's `init()` is refused; scala adopts the host node (`m_storageHostOwned`). Hub test: a `logosctl` session on a VPS with `extip` + `autonat-server` + `relay-server` in that file.
[^ctx]: Analysis § Key findings ("capability_module: rejecting requestModule — auth token not recognized", 12× incl. retries, old-built scala under logosctl 0.3.1) + memory `port-0-3` finding 1 (official modules hit it too; all cores now defer ~1 s).
[^rig]: Memory `port-0-3` ("Two-node logosctl tests passed for scala, qaku, kith, kym, perun"); random ports: memory `loam-node-ports`. Private-network recipe: `analysis-delivery.md` § 6 (upstream `tests/e2e/libs/helpers.py`), marked UNVERIFIED on v0.3.0 there.
[^hubsh]: `vpavlin/swamp3d` `hub/vps-hub.sh` (idempotent, `/var/lib/swamp-hub`, `swamp-hub.service`), run on the VPS 2026-10-09 by jimmy-crib/vpavlin; 8299 was already taken by a scala hub's Storage, so Swamp uses 8399.
