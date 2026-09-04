#!/usr/bin/env bash
# Put the Node the Angular CLI needs on PATH, and install ui/'s dependencies.
#
# The images Claude Code on the web runs in ship a Node older than the one
# ci.yml pins, and the Angular CLI refuses to run under it -- so every session
# used to start by hunting for another interpreter and finding none. This
# fetches the pinned build once, into a directory the container keeps, and
# leaves it on PATH for the rest of the session.
#
# The version is read out of ci.yml rather than written down again here: that
# file is the one pin the Dockerfile, scripts/start.ps1 and docs/testing.md
# already follow, and a fourth copy would be a fourth thing to bump.
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
say() { printf '%s\n' "$*" >&2; }

want=$(sed -n 's/.*node-version:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p' \
       "$root/.github/workflows/ci.yml" | head -1)
if [ -z "$want" ]; then
  say "session-start: no node-version pin in ci.yml; leaving Node alone."
  exit 0
fi

# Sort -V puts the lower version first, so this is "have is at least want".
at_least() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }

have=$(node --version 2>/dev/null | sed 's/^v//' || true)
node_bin=""
if [ -n "$have" ] && at_least "$want" "$have"; then
  say "session-start: node v$have already satisfies the v$want pin."
else
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) say "session-start: unsupported architecture $(uname -m); leaving Node alone."; exit 0 ;;
  esac

  prefix=/opt/node-$want
  [ -w /opt ] || prefix="$HOME/.local/share/node-$want"

  if [ ! -x "$prefix/bin/node" ]; then
    tarball="node-v$want-linux-$arch.tar.xz"
    say "session-start: node ${have:-absent} is below the v$want pin; fetching $tarball."
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    if ! curl -fsSL --retry 3 --retry-delay 2 \
         -o "$tmp/$tarball" "https://nodejs.org/dist/v$want/$tarball"; then
      say "session-start: could not download $tarball; leaving Node alone."
      exit 0
    fi
    tar -xJf "$tmp/$tarball" -C "$tmp"
    rm -rf "$prefix.partial"
    mv "$tmp/node-v$want-linux-$arch" "$prefix.partial"
    rm -rf "$prefix"
    mv "$prefix.partial" "$prefix"
  fi

  node_bin="$prefix/bin"
  export PATH="$node_bin:$PATH"
  hash -r
  say "session-start: node $(node --version) at $prefix."
fi

# The Bash tool starts a fresh shell per call, so PATH has to be persisted.
if [ -n "$node_bin" ] && [ -n "${CLAUDE_ENV_FILE:-}" ] \
   && ! grep -qsF "$node_bin" "$CLAUDE_ENV_FILE"; then
  printf 'export PATH="%s:$PATH"\n' "$node_bin" >> "$CLAUDE_ENV_FILE"
fi

# ui/ is an ordinary npm workspace; nothing on the Python side needs it.
ui="ui/ has no package.json"
if [ -f "$root/ui/package.json" ]; then
  say "session-start: installing ui/ dependencies."
  if (cd "$root/ui" && npm install --no-audit --no-fund >&2); then
    ui="ui/node_modules is installed"
  else
    ui="npm install in ui/ FAILED -- run it by hand before trusting a UI test run"
  fi
  say "session-start: $ui."
fi

# Stdout is what the session reads, so it says what actually happened rather
# than what was meant to.
printf 'Node %s is on PATH (ci.yml pins v%s), and %s.\n' \
       "$(node --version 2>/dev/null || echo 'is unavailable')" "$want" "$ui"
