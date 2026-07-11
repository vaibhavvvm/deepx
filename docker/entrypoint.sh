#!/bin/sh
# Auto-Dev sandbox entrypoint.
#
# The CLI runs the container with `--user <host-uid>:<host-gid>` on Linux so
# files written to the bind-mounted /workspace are owned correctly on the host,
# and `--cap-drop ALL --security-opt no-new-privileges` so even a container
# escape cannot gain host privileges. This script just makes an arbitrary,
# passwd-less UID usable: it gives HOME a sane default and ensures /workspace is
# the working directory.
set -e

: "${HOME:=/workspace}"
export HOME

# Some tools read the user's shell/home from /etc/passwd; when we run as an
# unmapped UID that entry won't exist. Point HOME at a writable location.
if [ ! -w "$HOME" ]; then
    export HOME=/tmp
fi

cd /workspace 2>/dev/null || cd /

exec "$@"
