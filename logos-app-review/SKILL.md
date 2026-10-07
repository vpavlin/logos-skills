---
name: logos-app-review
description: Review and UX-test a Logos app (LEZ program, Basecamp module/app, or Logos Storage/Messaging project) end to end - code review, tests, probes that reproduce findings, GUI test in Basecamp with screenshots and recordings, and a published HTML report. Use when asked to "review", "test" or "UX test" a Logos app or a lez-programs / Basecamp project.
---

# Logos app review

Goal: a review the author can act on. Every finding is reproduced or clearly marked as not reproduced, and the report says what was NOT tested.

## 0. Scope and setup
- Pin the exact commit under review (`git rev-parse HEAD`) and write it in the report.
- Work in a temp dir (`/tmp/<app>-review/`: `src/`, `probes/`, `report/`, helper scripts). Never modify the user's repo; probes and extra tests live outside it unless the user asks to commit them.
- Find the target environment first: which LEZ / Basecamp / module versions does the app pin, and which does the current testnet run? Version mismatch (e.g. app built for LEZ v0.2.4, testnet on v0.3.0) is often the root cause of "the app errors"; check before blaming the code.
- Untrusted downloads/packages go in their own empty dir; run Python with `-I`.

## 1. Code review (read before running)
- Read docs/ADRs first, then trace the trust boundaries: what comes from remote peers, other users, the chain, files, printers/devices, locale/environment.
- Look for: attacker-controlled ordering/timestamps ("newest wins"), unvalidated remote JSON/shards, unverified search/index data, locale-dependent parsing (`strtod`, `stod`), first-match tool detection, success reported before the device/chain confirms, overflow/rounding, missing authorization checks.
- For LEZ programs: swap/liquidity math (constant product, fees, rounding direction, first-depositor inflation), PDA derivation, ChainedCall account ordering, authorization of every account, oracle/TWAP seeding.
- Record file:line for each finding.

## 2. Tests
- Run the existing suite exactly as CI does (`RISC0_DEV_MODE=1 cargo test -p <pkg>`, ctest, etc.). Missing fixtures/headers: fetch or copy them and note it in the report.
- Add deterministic randomized/property tests for invariants (k never decreases, round trip never profits, fees conserved). Fixed seeds, no flakiness.
- Write small standalone probes (one file per finding) that print a clear result line, so the author can re-run them. Ship them with a README.

## 3. GUI test in Basecamp (real UI, not just code)
- Isolated state: fresh `HOME`/`--user-dir`, own Xvfb display (`Xvfb :N -screen 0 1600x1000x24`), never the user's real profile or wallet.
- Tools: `xdotool` (click/type), `scrot` (screenshots), `ffmpeg -f x11grab` (recordings). Click into file dialogs before typing; they ignore keyboard focus on Xvfb otherwise.
- Install packages via lgpm/.lgx; check core vs ui_qml modules and that module versions match what the app pins.
- Walk the real user journey: first run, empty states, the happy path, then the unhappy paths (bad input, busy device, offline, wrong network). Screenshot every step; record the flows that matter. Look at screenshots yourself before claiming anything about them (misread buttons are easy).
- Use fakes for hardware/services (mock printer, local sequencer) and say so in the report.
- Process hygiene: start things with a recorded PID/PGID and stop them by exact PID or by an env marker in a `stop.sh`. NEVER `pkill -f <pattern>` (it kills your own shell). Verify nothing is left running at the end.

## 4. Triage
- Severity: High (security/funds/data loss or broken core flow), Medium, Low, UX, Good (call out what works well).
- Withdraw findings that don't hold up; double-check counts, line numbers and wording before publishing.
- Separate "reproduced", "by inspection only", and "not tested" (real hardware, multi-user sync, other versions).

## 5. Report
- One static `index.html` + `media/` + `probes/`, same layout every time: summary table with severities, per-finding card (what, where, repro, impact, suggested fix), screenshots/videos inline, "not tested" section, how to re-run.
- Keep media small (compress video, ~MBs).
- Also write a handover note (where things are, open decisions, what was changed on the machine/VPS).

## 6. Publish
- Default: a private/LAN file drop (e.g. a WebDAV server: `curl -X MKCOL` / `PUT`), then verify byte for byte by downloading and comparing.
- Public (your own public static host, e.g. rsync to a Caddy/nginx docroot) only with the user's explicit OK. Reviews of unreleased projects describe unfixed vulnerabilities, so ask first and suggest giving the author time to fix.
- Finish with a short message: top findings, what was not tested, URLs, open decisions.
