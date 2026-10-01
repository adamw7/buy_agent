#!/usr/bin/env bash
# Remote sessions only: put ci.yml's Node on PATH (the image's is too old for the
# Angular CLI), run npm ci in ui/, and build .venv as ci.yml's Python job does.
# Versions are read out of ci.yml, never written down again here.
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
say() { printf '%s\n' "$*" >&2; }

# Sort -V puts the lower version first, so this is "have is at least want".
at_least() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]; }

want=$(sed -n 's/.*node-version:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p' \
       "$root/.github/workflows/ci.yml" | head -1)
want_py=$(sed -n 's/.*python-version:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p' \
          "$root/.github/workflows/ci.yml" | head -1)

# A return, not an exit, so the Python half still runs.
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

# The Bash tool starts a fresh shell per call, so PATH entries go to
# $CLAUDE_ENV_FILE.
keep_on_path() {
  local directory=$1
  if [ -n "$directory" ] && [ -n "${CLAUDE_ENV_FILE:-}" ] \
     && ! grep -qsF "$directory" "$CLAUDE_ENV_FILE"; then
    printf 'export PATH="%s:$PATH"\n' "$directory" >> "$CLAUDE_ENV_FILE"
  fi
}

keep_on_path "$node_bin"

# A marker newer than the lockfile means a resumed session: skip. `npm ci`, as
# in ci.yml, because `npm install` rewrites the lockfile.
ui="ui/ has no package.json"
installed="$root/ui/node_modules/.package-lock.json"
if [ -f "$root/ui/package.json" ]; then
  if [ -f "$installed" ] && [ ! "$root/ui/package-lock.json" -nt "$installed" ]; then
    ui="ui/node_modules is installed"
  else
    say "session-start: installing ui/ dependencies."
    if (cd "$root/ui" && npm ci --no-audit --no-fund >&2); then
      ui="ui/node_modules is installed"
    else
      ui="npm ci in ui/ FAILED -- run it by hand before trusting a UI test run"
    fi
  fi
  say "session-start: $ui."
fi

# ci.yml's Python job: requirements-dev.txt, then the AP2 SDK, without which the
# coverage floor fails. Into .venv, as CLAUDE.md documents.
venv="$root/.venv"
stamp="$venv/.session-start-installed"
venv_bin=""
py="python is unavailable"

# The newest final Python on PATH (the image's python3 is too old to parse the
# suite), skipping the venv's own directory; the earlier on PATH wins a tie.
newest_python() {
  local directory path said version level final best="" best_version="" best_final=""
  local -a directories
  IFS=: read -ra directories <<< "$PATH"
  for directory in "${directories[@]}"; do
    case "$directory" in "$venv"/*) continue ;; esac
    for path in "$directory"/python*; do
      [[ ${path##*/} =~ ^python(3(\.[0-9]+)?)?$ ]] && [ -x "$path" ] || continue
      said=$("$path" -c 'import platform, sys; print(platform.python_version(), sys.version_info.releaselevel)' 2>/dev/null) || continue
      read -r version level <<< "$said"
      final=""
      [ "$level" = final ] && final=yes
      if [ -z "$best" ] \
         || { [ -n "$final" ] && [ -z "$best_final" ]; } \
         || { [ "$final" = "$best_final" ] && ! at_least "$version" "$best_version"; }; then
        best=$path best_version=$version best_final=$final
      fi
    done
  done
  if [ -n "$best" ]; then
    printf '%s %s\n' "$best" "$best_version"
  fi
}

install_python() {
  local chosen system_py have_py built req up_to_date
  chosen=$(newest_python)
  if [ -z "$chosen" ]; then
    say "session-start: no python on PATH; leaving the Python side alone."
    return 1
  fi
  system_py=${chosen% *}
  have_py=${chosen##* }

  # No portable Python to fetch, so one under the pin is used with a warning.
  if [ -n "$want_py" ] && ! at_least "$want_py" "$have_py"; then
    say "session-start: python $have_py is the newest on PATH and under ci.yml's $want_py pin; using it anyway."
  fi

  # A venv older than the chosen interpreter is rebuilt, stamp and all.
  if [ -x "$venv/bin/python" ]; then
    built=$("$venv/bin/python" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)
    if [ -z "$built" ] || ! at_least "$have_py" "$built"; then
      say "session-start: .venv runs python ${built:-that will not start}, older than $have_py; rebuilding it."
      rm -rf "$venv"
    fi
  fi

  if [ ! -x "$venv/bin/python" ]; then
    say "session-start: creating .venv with python $have_py at $system_py."
    rm -rf "$venv"
    if ! "$system_py" -m venv "$venv" >&2; then
      say "session-start: python -m venv failed; leaving the Python side alone."
      rm -rf "$venv"
      return 1
    fi
  fi
  venv_bin="$venv/bin"

  # Written last, so a requirements file newer than the stamp means reinstall.
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

  # --no-deps applies to a whole file, hence requirements-ap2.txt.
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

keep_on_path "$venv_bin"

python_line="there is no .venv, so $py"
if [ -n "$venv_bin" ] && [ -x "$venv_bin/python" ]; then
  python_line=$(printf '.venv runs Python %s (ci.yml pins %s), and %s' \
    "$("$venv_bin/python" -c 'import platform; print(platform.python_version())' 2>/dev/null \
       || echo 'an interpreter it cannot run')" "${want_py:-nothing}" "$py")
fi

# Stdout is what the session reads.
printf 'Node %s is on PATH (ci.yml pins v%s), and %s. The %s.\n' \
       "$(node --version 2>/dev/null || echo 'is unavailable')" "$want" "$ui" \
       "$python_line"
