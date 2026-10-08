#!/usr/bin/env bash
# offline-run.sh CMD [ARGS...]
# Run CMD with NO external network (loopback only), so an app can be driven with test posts/messages
# that provably stay on this machine, while a local HTTP server on 127.0.0.1 plays "attacker".
# Needs bubblewrap. Unlike `unshare -rn` this works without user-namespace mapping rights.
# AppImages: FUSE does not mount inside bwrap, so extract first and run the AppRun:
#     ./Basecamp.AppImage --appimage-extract      # creates ./squashfs-root
#     offline-run.sh env HOME=/tmp/x DISPLAY=:95 dbus-run-session -- ./squashfs-root/AppRun
# Check it: offline-run.sh curl -m 4 https://example.com   (must fail)
set -euo pipefail
command -v bwrap >/dev/null || { echo "offline-run: install bubblewrap" >&2; exit 1; }
exec bwrap --unshare-net --dev-bind / / "$@"
