---
name: logos-app-review
description: Review and UX-test a Logos app (LEZ program, Basecamp module/app, multi-writer sync app, mobile app, Logos Storage/Messaging project) end to end - code review at the trust boundaries, sync-correctness and metadata-privacy checks, tests, one probe per finding, GUI test in Basecamp (one and two instances), phone test, fix-verification, and a published HTML report. Use when asked to "review", "test" or "UX test" a Logos app, or to re-check an app after the author's fixes.
---

# Logos app review

Goal: a review the author can act on. Every finding is **reproduced**, **read in code**, or **not tested**, and the report says what was NOT tested. Evidence first: one probe per finding, so the author can re-run it.

Related skills: `logos-basecamp-module` (what a correct module looks like), `logos-multiwriter-sync` and `logos-reliable-channels` (the sync model and its silent failures), `logos-distributed-debugging` (finding where a pipeline stops), `logos-headless-logosctl` (headless runs), `logos-mobile-app` and `logos-fdroid` (phones), `logos-publish-artifacts` (shipping a fixed build).

## 0. Scope and setup
- Pin the exact commit (`git rev-parse HEAD`) and the package version under review; write both in the report. If the author ships a package, check that it matches the source (hash against the catalog index, then a source build if feasible).
- Work in a temp dir (`/tmp/<app>-review/`: `src/`, `probes/`, `report/`, helper scripts). `/tmp` may be wiped between sessions: keep the report and probes on a drop as you go. Never modify the user's repo; probes and extra tests live outside it unless asked.
- Find the target environment first: which LEZ / Basecamp / module / delivery versions does the app pin, and which does the network run? A version mismatch is often the root cause of "the app errors"; check before blaming the code.
- Downloaded files and packages go in their own empty dir; run Python with `-I`.
- If the sources are not reachable (private repo, other machine), ask for a `git bundle` on a shared drop rather than guessing.

## 1. Code review (read before running)
Read the docs/ADRs/README first: the claims the app makes are the checklist. Then trace the trust boundaries: what comes from remote peers, other users, the chain, files, devices, locale/environment.

**Generic checks**
- Attacker-controlled ordering/timestamps ("newest wins", windows counted on the sender's own `ts`), unvalidated remote JSON (a throwing accessor like `nlohmann::value()` on wrong types), unverified search/index data, locale-dependent parsing (`strtod`, `stod`), first-match tool detection, success reported before the device/chain confirms, overflow/rounding, missing authorization, secrets in logs/files/QString copies, files and folders created world-readable.
- For big code bases, split the read-through between read-only review agents, one per layer (crypto/identity; protocol that handles untrusted network data; UI/backend). Treat their output as candidates: re-check each with a probe or in the GUI, and mark the rest "read in code".
- Compare README promises with behaviour in a table. Promises about privacy, limits and "never" are where reviews of privacy apps find the most.

**LEZ programs**: swap/liquidity math (constant product, fees, rounding direction, first-depositor inflation), PDA derivation, ChainedCall account ordering, authorization of every account, oracle/TWAP seeding.

**Multi-writer sync apps (event log + fold)** - where the serious bugs live:
- Two devices fold the same log to the same state regardless of arrival order. Test with shuffled and duplicated logs; compare state hashes.
- If there are two implementations (TS and C++, desktop and phone), feed both the same vectors and require identical output.
- Signature parity across platforms: a high-S signature accepted by OpenSSL but rejected by noble (or the reverse) makes platforms diverge. Test low-S and high-S, malleated signatures, and invalid keys (a key >= n, off-curve points, all-zero keys).
- Roles and permissions are enforced inside the fold, not just in the UI: replay a "member" event that claims an admin action.
- A replay/duplicate/out-of-window event never changes state; the id is derived from content, not chosen by the sender.
- Flood and rate limits are enforced by receivers on something the sender cannot choose (try newest-first ordering, fresh keys, and forged ids that suppress repair).

**Metadata privacy (what does a wire observer learn?)** - check for each transport (relay topic, BLE, store queries, deep links):
- Stable identifiers: is a Bluetooth/peer id equal to a hash of the sender id? Is one sender id shared across all apps on the device? Do "anonymous" or one-time keys ever repeat (after a restore, an index counter, a derived sequence)?
- Plaintext at rest and on the wire that links personas/accounts/rooms (outbox tables, logs, topic names, room ids in clear text in frames).
- What a request reveals (the day asked about, the ids you already hold, a deep link's target).
- Per-account isolation on a shared device: lock, create a second account, and look for the first account's data.

**Basecamp checks to run first** (most field-found bugs):
- Blocking `logos.callModule` from QML (use the async call); module calls inside `onContextReady` (rejected on 0.3).
- View/core version mismatch ("Invalid response"); highest version wins across repositories; portable vs dev builds; a package that asks for another module version than the host bundles.
- `Text` without `textFormat: Text.PlainText` on remote text (rich text can fetch remote images; a sanitiser that adds backslashes can usually be bypassed with a backslash of its own).
- Exceptions escaping Qt callbacks (`std::terminate` kills the whole core module); then restart: if the bad input is saved, the crash repeats on every start.
- Secrets or passwords crossing the Remote Objects contract; Argon2/crypto work inside the backend slot.

## 2. Tests and probes
- Run the existing suite as CI does. Fetch missing fixtures and say so. Note what the suite does not cover (often the Qt backend, where the receive path lives).
- Add deterministic randomized/property tests for invariants (k never decreases, round trip never profits, fold order-independence). Fixed seeds.
- One standalone probe per finding, printing a clear result line: link against the app's real headers/library; for logic inside a class, build it like the author's e2e test (fake SDK, `-fno-access-control`) so you test the real function, not a copy. Create fresh identities per case when clocks move forward. Ship probes with a README and a `results.txt`.
- Backends that cannot be built without the Logos SDK (Qt backend + `delivery_module` types): link the app's real engine library, and replay the backend's decision logic in a probe that is a clearly labelled copy of the cited lines (with file:line), then say so in the report. A copy proves the logic; the GUI or a live run proves the wiring.
- Basecamp 0.3 views are sandboxed with no network: a remote-fetch probe (an `<img>` pointing at a local server) seeing no request means the sandbox held, not that the app's filter works. Report both facts.
- Core modules can be driven headless: `nix build .#lgx-portable`, then `logosctl` in its own session (own `HOME` + `LOGOSCTL_CONFIG_DIR`). Put the app next to real servers/peers where you can, plus a small hostile stand-in (wrongly-typed JSON, a redirect to another host, going away). Check persistence of failures with a restart.

## 3. GUI test in Basecamp
**Setup**
- Fresh `HOME`/`--user-dir`, own Xvfb display per instance (`Xvfb :N -screen 0 1600x900x24`), never the user's profile or wallet. Tools: `xdotool`, `scrot`, `ffmpeg -f x11grab`.
- Install through the same path a user would (add the catalog URL under Package Repositories, Install) and say what happens; fall back to `lgpm --modules-dir <HOME>/.local/share/Logos/LogosBasecamp/modules --ui-plugins-dir <HOME>/.local/share/Logos/LogosBasecamp/plugins --allow-unsigned install --file X.lgx`. Check the package's declared dependencies against what the host bundles. If the in-app install hangs or fails, note how long you waited (3-4 minutes is enough), then download the package and its dependencies from the index URLs by hand, verify each sha256 against the index, and install with `lgpm`; the app can keep running meanwhile. Say which of the two paths you tested.

**Process hygiene**
- Start with a recorded PID, or mark every process with an env var (`<APP>_REVIEW_NODE=a`) and stop by that marker in a `stop.sh`. NEVER `pkill -f <pattern>` (it kills your own shell). `$!` after `cd x && prog &` is the subshell's PID: start with `setsid prog &` on its own line. Make `stop.sh` take the marker as an argument (so it never matches other sessions or Xvfb) and silence `/proc` permission noise with `exec 2>/dev/null`. Verify nothing is left running.

**Driving the UI**
- Click into file dialogs before typing. Coordinates move when panels appear: re-take a screenshot before clicking a state-dependent button. Look at screenshots yourself before claiming anything. Watch a second node cheaply by comparing a cropped screenshot hash instead of reading full images.
- Walk the real journey: first run, empty states, the happy path, then the unhappy paths (bad input, busy device, offline, wrong network).
- For apps with accounts or identities, always run these three: (1) **second account**: post as the first (including anonymously), lock, create a second account, and check every list (my posts, outbox, notifications) for the first account's data; (2) **restore on a fresh profile** from the recovery phrase, then compare the keys/personas it uses with the original's, and post anonymously after the restore; (3) **restore again on the same profile** and check that settings and counters are not reset or reused.
- Grep the README for absolute words ("never", "always", "no file", "only you", "nobody", "every peer") and test each promise; list them in the README-vs-reality table.
- `xdotool type` mangles non-ASCII text (65 of 110 characters arrived in one run): do not report a truncated field as an app bug before typing the text another way.
- Expect hypotheses to fail: test them (e.g. "scroll resets on each poll") and report the ones that turn out fine under "What works".

**Two or more instances** (anything that syncs between users)
- Own profile, display, key and recording per instance. Publish a tiny clearly-labelled item ("review-test") from A and give B several minutes plus the app's own "sync now". If nothing arrives, read both logs before blaming the network: separate "channel/topic could not be created" (app or version bug) from "peers unreachable". Check what the UI says while nothing syncs: "Connected" with a silently failing channel is itself a finding. Two nodes on one machine do not find each other for file transfer without loopback/bootstrap config (see the author's two-node script).
- Ask before sending test traffic to a shared network. Use throw-away identities and tiny harmless payloads. Never publish adversarial data (future-dated manifests, malformed shards, attack payloads) to a network others use; reproduce those with local probes. To test rendering/storage without posting, run the app with no external network: `offline-run.sh` (in this folder, `bwrap --unshare-net --dev-bind / /`, loopback still works) and use a local HTTP server on 127.0.0.1 as the "attacker" URL. Extract AppImages first (FUSE does not mount inside bwrap), and verify the sandbox with a failing `curl`. Do not rely on an app setting such as "a network it cannot send on": a node can show "Connected" and still read from and send to the network. `unshare -rn` is not permitted on some machines.

**Phones (Android)**
- Install via the F-Droid repo (or the `.apk`), not by `adb` only: that is what a user does. Get diagnostics off the device without adb (a copyable debug/status screen, a share action).
- Test phone <-> desktop sync both ways, with the phone backgrounded and after a restart. Measure catch-up time: slow catch-up usually comes from a long re-ask interval; check what the constants are. Check RLN/rate-limit budget use. Use `logos-mobile-app` for the known traps.

## 4. Triage
- Severity: High (security, funds, data loss, broken core flow, a broken privacy promise), Medium, Low, UX, Good. Call out what works well.
- Withdraw findings that do not hold; double-check counts, line numbers and wording before publishing.
- Separate "reproduced", "read in code" and "not tested" (real hardware, multi-user sync, other versions, other platforms).

## 5. Report
- Start from `report-template.html` in this folder (summary, stat tiles, README-vs-reality table, per-finding cards with repro and suggested fix, what works, not tested). One static page + `media/` + `probes/`. Keep media small (compress video, a few MB).
- Also write a handover note (where things are, open decisions, what was changed on the machine).

## 6. Fix verification (second round)
When the author ships fixes: fetch the new source and packages, diff against the reviewed commit, and re-run every probe and the GUI repro. Add a status column to the findings table: Fixed (how checked), Partly (what still works), Open, New. Look hard at the new code: fixes often move the bug (a date window that is now 5 minutes wide instead of unbounded; a flag added to the core that the view never reads). Re-run the author's tests too, and report any that fail on your machine with the exact output.

## 7. Publish
- Default: the private/LAN drop (WebDAV: `curl -X MKCOL` / `PUT`), then verify byte for byte by downloading and comparing.
- Public (a read-only static host, files copied over ssh) only with the user's explicit OK. Reviews of unreleased projects describe unfixed vulnerabilities: ask first and suggest telling the author before it goes up.
- Finish with a short message: top findings, what was not tested, URLs, open decisions.
