---
name: logos-app-review
description: Review and UX-test a Logos Basecamp module/app or Logos Storage/Messaging project end to end - code review, tests, probes that reproduce findings, GUI test in Basecamp with screenshots and recordings, and a published HTML report. Use when asked to "review", "test" or "UX test" a Basecamp / Loam project. For a LEZ zkVM program (RISC Zero guest, SPEL IDL, on-chain accounts) use the sibling skill logos-lez-program instead, or alongside this one when a submission ships both halves.
---

# Logos app review

Goal: a review the author can act on. Every finding is reproduced or clearly marked as not reproduced, and the report says what was NOT tested.

## 0. Scope and setup
- Pin the exact commit under review (`git rev-parse HEAD`) and write it in the report.
- Work in a temp dir (`/tmp/<app>-review/`: `src/`, `probes/`, `report/`, helper scripts). Never modify the user's repo; probes and extra tests live outside it unless the user asks to commit them.
- Find the target environment first: which LEZ / Basecamp / module versions does the app pin, and which does the current testnet run? Version mismatch (e.g. app built for LEZ v0.2.4, testnet on v0.3.0) is often the root cause of "the app errors"; check before blaming the code.
- Untrusted downloads/packages go in their own empty dir; run Python with `-I`.

## 0b. When the brief is a fixed rubric, or two submissions compete
- If given an external rubric/spec with named criteria, build a per-criterion table first: PASS / FAIL / UNVERIFIABLE, with the evidence for each. UNVERIFIABLE is a legitimate answer — don't guess, and don't let a carve-out in the spec ("checked separately for authenticity") become an excuse to skip stating the gap plainly.
- Comparing two submissions against the same rubric: run the full single-app review (sections 1-4 below) independently for each one before comparing, so the comparison doesn't contaminate either review. Write a short "where they differ" section separately from the per-criterion tables — not pass/fail, just the texture that matters for "which is better" (which ships more evidence, which has thinner docs, which is larger in scope).
- Any test traffic you post to a shared/live resource during this kind of review matters doubly here: a mislabelled post could itself look like the real-adoption evidence the rubric is trying to measure. Label it clearly as evaluator test traffic (see §3 on throwaway identities) and explain in the report why the labelling itself protects both the review and the submission's own numbers.

## 1. Code review (read before running)
- Read docs/ADRs first, then trace the trust boundaries: what comes from remote peers, other users, the chain, files, printers/devices, locale/environment.
- Look for: attacker-controlled ordering/timestamps ("newest wins"), unvalidated remote JSON/shards, unverified search/index data, locale-dependent parsing (`strtod`, `stod`), first-match tool detection, success reported before the device/chain confirms, overflow/rounding, missing authorization checks.
- If the project includes a LEZ zkVM program (RISC Zero guest, on-chain accounts), load **logos-lez-program** for the program-specific checks (swap/liquidity math, PDA derivation, ChainedCall account ordering, account authorization, IDL-matches-source, ImageID/deployment identity) — this skill covers the UI/transport half, that one covers the on-chain half.
- Record file:line for each finding.

## 2. Tests
- Run the existing suite exactly as CI does (`RISC0_DEV_MODE=1 cargo test -p <pkg>`, ctest, etc.). Missing fixtures/headers: fetch or copy them and note it in the report.
- Add deterministic randomized/property tests for invariants (k never decreases, round trip never profits, fees conserved). Fixed seeds, no flakiness.
- Core modules (C++/Qt, `type: core`) can be tested without the GUI: build with `nix build .#lgx-portable`, then `logosctl` in its own session (own `HOME` + `LOGOSCTL_CONFIG_DIR`; `install`, `module load`, `call mod method str:ARG`; module crashes show in `$LOGOSCTL_CONFIG_DIR/logs/daemon.log`). Call string arguments with `str:`.
- Put the app next to real servers/peers, not mocks, where you can (e.g. the real dufs binary), then add a small hostile stand-in that answers with wrongly-typed JSON, redirects to another host, or goes away. "Remote data of the wrong shape" and "redirect carries credentials" are the two cheap probes with the highest hit rate.
- Check persistence of failures: after a crash, restart the module/app. A crash that repeats on every start (bad state saved to disk) is a different severity from a one-off.
- Write small standalone probes (one file per finding) that print a clear result line, so the author can re-run them. Ship them with a README.
- **Cross-check the submission's own claimed numbers against reality, not just that the suite passes.** A claimed test count, a CI-green claim, a line count in a README table — run the real binary and count, or hit the GitHub API for the actual check-runs on the actual current HEAD, rather than trusting the README's own numbers. This is cheap (seconds) and catches real, reportable doc-accuracy gaps even when the underlying code is fine — treat a mismatch as a finding about how carefully the doc was checked against the code, not a pass/fail verdict on its own.

## 3. GUI test in Basecamp (real UI, not just code)
- Isolated state: fresh `HOME`/`--user-dir`, own Xvfb display (`Xvfb :N -screen 0 1600x1000x24`), never the user's real profile or wallet.
- Tools: `xdotool` (click/type), `scrot` (screenshots), `ffmpeg -f x11grab` (recordings). Click into file dialogs before typing; they ignore keyboard focus on Xvfb otherwise.
- Install packages via lgpm/.lgx; check core vs ui_qml modules and that module versions match what the app pins.
- **If the Basecamp AppImage won't run at all on your host** (e.g. `GLIBC_2.38 not found` or a missing `GLIBCXX`/`CXXABI` symbol), the host's system glibc is older than what the AppImage was built against. Don't downgrade your approach to static analysis over this — fetch a newer glibc's `.deb`s (`apt-get download libc6 libstdc++6` from a newer release, or their `.debs` from a repo mirror) into a scratch dir, extract with `dpkg-deb -x` (do not install system-wide), then invoke the AppImage's actual `.elf` binary directly through that newer `ld-linux-x86-64.so.2 --library-path <extracted>/lib/x86_64-linux-gnu ... <elf>`, bypassing the kernel's normal PT_INTERP loading. Any child processes the app spawns (a `logos_host`/`ui-host` helper) need the same treatment — wrap them in a small script at the exact path the error message names, each invoking the real `.elf` through the same interpreter. This setup is reusable across a whole review session once built.
- Walk the real user journey: first run, empty states, the happy path, then the unhappy paths (bad input, busy device, offline, wrong network). Screenshot every step; record the flows that matter. Look at screenshots yourself before claiming anything about them (misread buttons are easy).
- Install packages without clicking: `lgpm --modules-dir <HOME>/.local/share/Logos/LogosBasecamp/modules --ui-plugins-dir <HOME>/.local/share/Logos/LogosBasecamp/plugins --allow-unsigned install --file X.lgx` before first start (still test the GUI install flow if the app's install is what is under review).
- **Install exactly as the app's README says first**, before any path you already know works. If
  the documented steps don't produce a working install (a dependency built at a revision that
  installs under a different module name, say), that's a finding, and usually a cheap one to fix.
- **Test the real Basecamp, not the standalone host.** `nix run .#<ui>` runs `logos-standalone-app`,
  which has the QML inspector for scripted UI tests but not Basecamp's plugin sandbox (file/network
  limits, shared module instances). Passing there doesn't prove it works in Basecamp. Basecamp has no
  inspector, so drive it with `xdotool` on Xvfb.
  - Before launching, list the Basecamp instances already running and leave the user's alone.
  - Confirm `--user-dir` took effect: the log prints `Base data directory: <dir>`. Basecamp is
    single-instance per user dir; a second launch hands over to the running one.
- **"Submitted" is not "succeeded".** Check every UI action against chain or server state
  (`spel inspect`, an API read), not the UI's own message. Then force a failure the UI can't prevent
  and see what the user is told. For a swap, a front-run does it: quote, move the pool from the CLI,
  then confirm. A UI that shows the same success message whether or not the backend accepted the
  action is a High finding.
- **Toasts are transient; get them from the recording.** Screenshots taken a few seconds apart
  routinely miss them.
  1. Record the recording's start time (`ps -o lstart= -p <ffmpeg pid>`; parse it with `LC_ALL=C`,
     because a localised date breaks `date -d`).
  2. Log the time of each action.
  3. Pull frames around it, e.g. `ffmpeg -nostdin -ss <offset> -t 14 -i rec.mkv -vf fps=2 f%02d.png`,
     then a `tile=7x4` contact sheet to find the frame, then crop it full-resolution.
- **Data-truth checks for anything with numbers:**
  - Are declared decimals honoured, or does the user enter base units?
  - Do balances refresh after each transaction?
  - Does the fee shown match the fee charged (compare with the on-chain delta)?
  - Do two screens showing the same value agree?
  - Is number formatting consistent under the user's locale (an AppImage inherits it)? Compare the
    separator used by every number on one screen — a value rendered via a different code path
    (computed client-side vs. echoed verbatim from an API response) can silently pick up a different
    locale convention than its neighbours.
  - Do defaults hurt the user (e.g. a minimum deposit where a fixed protocol lock dominates), and does
    the confirm step warn them?
- `$!` after `cd x && prog &` is the subshell's PID, not the program's; record the real PID (`pgrep -x`, or start with `setsid prog &` on its own line) or `kill` will silently miss it.
- Coordinates move when panels appear (preview pane, transfer bar): re-take a screenshot before clicking a button whose position depends on state.
- Expect hypotheses to fail: test them (e.g. "scroll resets on each poll") and report the ones that turn out fine under "What works".
- Two or more instances (any app that syncs between users): run each with its own `HOME`, Xvfb display (`:81`, `:82`), key and recording, started by a `launch.sh a|b` and stopped by an env marker (`SWAMP_MB_NODE=a`), never by pattern. Publish a tiny, clearly-labelled item ("review-test") from A and watch B by comparing a cropped screenshot hash instead of reading full screenshots. Do not bound the check by one short wait: give it several minutes and press the app's own "sync now". If nothing arrives, read both logs before blaming the network: separate "channel/topic could not be created" (app or version bug) from "peers unreachable". Check what the UI says while nothing syncs; "Connected" with a silently failing channel is itself a finding.
- Ask before sending test traffic to a public/shared network, use throwaway identities and tiny payloads, and never publish adversarial data (future-dated manifests, malformed shards) to a network other people use; reproduce those with local probes.
- Use fakes for hardware/services (mock printer, local sequencer) and say so in the report.
- Verifying a submitted demo video: download the actual file (not just the title/thumbnail/description), then extract stills at specific timestamps (`ffmpeg -ss TIMESTAMP -i INPUT -frames:v 1 -q:v 3 OUT.jpg`) and check what's actually on screen against the claims, rather than trusting the title. Burned-in captions are a reliable proxy for narration content if present; without them, you have visual confirmation of what's on screen but not of the spoken narration — say that plainly in the report rather than implying you "watched" the video.
- Process hygiene: start things with a recorded PID/PGID and stop them by exact PID or by an env marker in a `stop.sh`. NEVER `pkill -f <pattern>` (it kills your own shell). Verify nothing is left running at the end.
  - `pgrep -f <pattern> | xargs kill` fails the same way. So does a bracketed pattern
    (`pkill -f "[x]yz"`) when your own command line also contains the full string, e.g. in a path.
    It can also hit the user's own instances of the same app. Before killing, read
    `/proc/<pid>/cmdline` and check it contains your marker (your `--user-dir`, your display).
  - Long-lived infrastructure (sequencer, daemon, Xvfb) outlives a single tool call. Start it detached
    (`setsid nohup … & echo $! > x.pid`), because agent background jobs can be cut off by a time limit
    and something outside the session can still stop it. Health-check it (an RPC call) before each
    phase, and restart on the same state rather than assuming it's up.
  - In scripts, run `ffmpeg -nostdin`, or it reads stdin and stalls. Never name a shell function after
    a coreutils command (`cut`, `sort`): the function ends up calling itself.

## 4. Triage
- Severity: High (security/funds/data loss or broken core flow), Medium, Low, UX, Good (call out what works well).
- Withdraw findings that don't hold up; double-check counts, line numbers and wording before publishing.
- Separate "reproduced", "by inspection only", and "not tested" (real hardware, multi-user sync, other versions).
- When the UI fronts a chain or server, a backend rejection that the UI reports as success is High even if the backend behaved correctly.

## 5. Report
- One static `index.html` + `media/` + `probes/`, same layout every time: summary table with severities, per-finding card (what, where, repro, impact, suggested fix), screenshots/videos inline, "not tested" section, how to re-run.
- Keep media small (compress video, ~MBs).
- Also write a handover note (where things are, open decisions, what was changed on the machine/VPS).

## 6. Publish
- Default: a private/LAN file drop (e.g. a WebDAV server: `curl -X MKCOL` / `PUT`), then verify byte for byte by downloading and comparing.
- Public (your own public static host, e.g. rsync to a Caddy/nginx docroot) only with the user's explicit OK. Reviews of unreleased projects describe unfixed vulnerabilities, so ask first and suggest giving the author time to fix.
- On a sealed-cage agent session (receives files but cannot push out via any send-file/API route): the working egress path is writing the report into its outbox directory while a task from the recipient is still open, then closing that task — a non-caged process on the host packs and sends whatever is in the outbox at that point. Confirm size/file-count against the package limit (regular files only, symlinks never followed) before closing the task, and split into two batches with the outbox cleared between them if it won't fit in one.
- Finish with a short message: top findings, what was not tested, URLs, open decisions.
