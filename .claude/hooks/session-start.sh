#!/usr/bin/env bash
# Put the Node the Angular CLI needs on PATH, install ui/'s dependencies, and
# make the Python half of the project runnable in a .venv.
#
# The images Claude Code on the web runs in ship a Node older than the one
# ci.yml pins, and the Angular CLI refuses to run under it -- so every session
# used to start by hunting for another interpreter and finding none. This
# fetches the pinned build once, into a directory the container keeps, and
# leaves it on PATH for the rest of the session. They ship no Python
# dependencies at all, so the other half installs what ci.yml installs.
#
# The versions are read out of ci.yml rather than written down again here: that
# file is the one pin the Dockerfile, scripts/start.ps1 and docs/testing.md
# already follow, and a fourth copy would be a fourth thing to bump.
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
say() { printf '%s\n' "$*" >&2; }

# Sort -V puts the lower version first, so this is "have is at least want".
at_least() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }

# What ci.yml pins, and the whole of what either half below decides for itself.
want=$(sed -n 's/.*node-version:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p' \
       "$root/.github/workflows/ci.yml" | head -1)
want_py=$(sed -n 's/.*python-version:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p' \
          "$root/.github/workflows/ci.yml" | head -1)

# Node's failures are Node's: a return rather than an exit, so an architecture
# without a build or a download that would not come leaves the Python half
# below still installed and the summary at the end still printed.
node_bin=""
install_node() {
  if [ -z "$want" ]; then
    say "session-start: no node-version pin in ci.yml; leaving Node alone."
    return 1
  fi

  local have arch prefix tarball tmp
  have=$(node --version 2>/dev/null | sed 's/^v//' || true)
  if [ -n "$have" ] && at_least "$want" "$have"; then
    say "session-start: node v$have already satisfies the v$want pin."
    return 0
  fi

  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) say "session-start: unsupported architecture $(uname -m); leaving Node alone."
       return 1 ;;
  esac

  prefix=/opt/node-$want
  [ -w /opt ] || prefix="$HOME/.local/share/node-$want"

  if [ ! -x "$prefix/bin/node" ]; then
    tarball="node-v$want-linux-$arch.tar.xz"
    say "session-start: node ${have:-absent} is below the v$want pin; fetching $tarball."
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' RETURN
    if ! curl -fsSL --retry 3 --retry-delay 2 \
         -o "$tmp/$tarball" "https://nodejs.org/dist/v$want/$tarball"; then
      say "session-start: could not download $tarball; leaving Node alone."
      return 1
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
}
install_node || true

# The Bash tool starts a fresh shell per call, so PATH has to be persisted.
if [ -n "$node_bin" ] && [ -n "${CLAUDE_ENV_FILE:-}" ] \
   && ! grep -qsF "$node_bin" "$CLAUDE_ENV_FILE"; then
  printf 'export PATH="%s:$PATH"\n' "$node_bin" >> "$CLAUDE_ENV_FILE"
fi

# ui/ is an ordinary npm workspace; nothing on the Python side needs it.
#
# npm writes ui/node_modules/.package-lock.json as the last step of an install, so
# a copy of it newer than the lockfile is a tree already installed from that
# lockfile -- which is a resumed session, where a reinstall is half a minute spent
# to change nothing. A missing marker is the cold container this hook is for.
ui="ui/ has no package.json"
installed="$root/ui/node_modules/.package-lock.json"
if [ -f "$root/ui/package.json" ]; then
  if [ -f "$installed" ] && [ ! "$root/ui/package-lock.json" -nt "$installed" ]; then
    ui="ui/node_modules is installed"
  else
    say "session-start: installing ui/ dependencies."
    if (cd "$root/ui" && npm install --no-audit --no-fund >&2); then
      ui="ui/node_modules is installed"
    else
      ui="npm install in ui/ FAILED -- run it by hand before trusting a UI test run"
    fi
  fi
  say "session-start: $ui."
fi

# The Python half is ci.yml's Python job and not a second opinion about it:
# requirements-dev.txt, and then the AP2 SDK in an install of its own for the
# reasons requirements-ap2.txt gives. Both, because the SDK is only optional to a
# *run*: to the suite it is the payment tests, which skip without it, and the 99%
# coverage floor, which cannot be reached with them sitting out -- so a session
# without it reports a red gate for a checkout CI would pass.
#
# Into .venv, which is the environment CLAUDE.md documents and .gitignore already
# covers, rather than the image's own site-packages, where `pip` and `python` are
# not always the same installation.
venv="$root/.venv"
stamp="$venv/.session-start-installed"
venv_bin=""
py="python is unavailable"

install_python() {
  local system_py have_py req up_to_date
  system_py=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
  if [ -z "$system_py" ]; then
    say "session-start: no python on PATH; leaving the Python side alone."
    return 1
  fi

  # Unlike Node there is no portable build to fetch, so an interpreter under the
  # pin is said once and used anyway: it is the platform difference ci.yml
  # matrixes for, not a session that cannot run the suite.
  have_py=$("$system_py" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)
  if [ -n "$want_py" ] && [ -n "$have_py" ] && ! at_least "$want_py" "$have_py"; then
    say "session-start: python $have_py is under ci.yml's $want_py pin; using it anyway."
  fi

  if [ ! -x "$venv/bin/python" ]; then
    say "session-start: creating .venv."
    rm -rf "$venv"
    if ! "$system_py" -m venv "$venv" >&2; then
      say "session-start: python -m venv failed; leaving the Python side alone."
      rm -rf "$venv"
      return 1
    fi
  fi
  venv_bin="$venv/bin"

  # The stamp is the npm marker's argument in the other language: written last, so
  # a requirements file newer than it is one edited since the install it records.
  # A resumed session then costs nothing, and a cold container, which has no
  # .venv at all, installs.
  up_to_date=""
  if [ -f "$stamp" ]; then
    up_to_date=yes
    for req in requirements.txt requirements-dev.txt \
               requirements-ap2-deps.txt requirements-ap2.txt; do
      if [ "$root/$req" -nt "$stamp" ]; then
        up_to_date=""
        break
      fi
    done
  fi
  if [ -n "$up_to_date" ]; then
    py="the venv is installed"
    return 0
  fi

  say "session-start: installing Python dependencies into .venv."
  if ! "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$root/requirements-dev.txt" >&2; then
    py="pip install of requirements-dev.txt FAILED -- run it by hand before trusting a test run"
    return 1
  fi

  # Two commands and not one: --no-deps is not a per-line option and the SDK is
  # the only thing here that needs it, which is what requirements-ap2.txt is a
  # second file for.
  if ! "$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r "$root/requirements-ap2-deps.txt" >&2 \
     || ! "$venv/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps -r "$root/requirements-ap2.txt" >&2; then
    py="the venv is installed WITHOUT the AP2 SDK -- the payment tests will skip and the coverage floor will fail"
    return 1
  fi

  : > "$stamp"
  py="the venv is installed"
}
install_python || true
say "session-start: $py."

# Same reason as Node's: a fresh shell per Bash call, so the venv is reached by
# PATH rather than by an activate nobody sourced.
if [ -n "$venv_bin" ] && [ -n "${CLAUDE_ENV_FILE:-}" ] \
   && ! grep -qsF "$venv_bin" "$CLAUDE_ENV_FILE"; then
  printf 'export PATH="%s:$PATH"\n' "$venv_bin" >> "$CLAUDE_ENV_FILE"
fi

# Which interpreter the venv ended up with is worth a session knowing, the pin
# being one thing the image is free to be under.
python_line="there is no .venv, so $py"
if [ -n "$venv_bin" ] && [ -x "$venv_bin/python" ]; then
  python_line=$(printf '.venv runs Python %s (ci.yml pins %s), and %s' \
    "$("$venv_bin/python" -c 'import platform; print(platform.python_version())' 2>/dev/null \
       || echo 'an interpreter it cannot run')" "${want_py:-nothing}" "$py")
fi

# Stdout is what the session reads, so it says what actually happened rather
# than what was meant to.
printf 'Node %s is on PATH (ci.yml pins v%s), and %s. The %s.\n' \
       "$(node --version 2>/dev/null || echo 'is unavailable')" "$want" "$ui" \
       "$python_line"
