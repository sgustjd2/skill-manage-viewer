#!/bin/bash
# The in-place upgrade test for the macOS app, for CI only (a throw-away Mac: it starts and replaces a SkillDesk.app in a
# temp folder and leaves /Applications alone).
#
#   tools/update_e2e.sh OLD.dmg OLD_VERSION NEW.dmg NEW_VERSION
#
# 1. puts the OLD app in a temp "Applications" folder and starts it hidden, with SKILLDESK_UPDATE_TEST_ORIGIN pointing at a
#    local web server that plays GitHub (the release answer, SHA256SUMS.txt and the NEW dmg);
# 2. expects the app to download and verify the dmg by itself, quit, have the helper script swap the .app and open the NEW
#    version again, hidden;
# 3. then puts the OLD app back and serves a wrong hash: the app must neither quit nor change.
set -euo pipefail

old_dmg=$1 old_version=$2 new_dmg=$3 new_version=$4
repo="sgustjd2/skill-manage-viewer"
port=18766
work=$(mktemp -d)
store="$HOME/.skill-manager"
apps="$work/Applications"
app="$apps/SkillDesk.app"
bin="$app/Contents/MacOS/SkillDesk"
name=$(basename "$new_dmg")
web=""

show_logs() {
  for f in error.log update-apply.log update-check.log; do
    if [ -f "$store/$f" ]; then echo "---- $f"; tail -n 60 "$store/$f"; fi
  done
}
fail() { show_logs; echo "::error::$1"; exit 1; }
cleanup() { pkill -f "$bin" 2> /dev/null || true; [ -z "$web" ] || kill "$web" 2> /dev/null || true; }
trap cleanup EXIT

app_version() { /usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$app/Contents/Info.plist"; }
wait_until() {  # wait_until SECONDS COMMAND...
  local n=$1; shift
  for ((i = 0; i < n; i++)); do if "$@"; then return 0; fi; sleep 1; done
  return 1
}
put_app() {  # put_app DMG: a fresh copy of the app from that dmg
  mkdir -p "$apps"
  rm -rf "$app"
  hdiutil attach "$1" -nobrowse -readonly -mountpoint "$work/mnt" > /dev/null
  ditto "$work/mnt/SkillDesk.app" "$app"
  hdiutil detach "$work/mnt" > /dev/null
}
is_new() { [ "$(app_version)" = "$new_version" ]; }
running_other_than() { pgrep -f -- "$bin --hidden" | grep -vx "$1" > /dev/null; }  # the app itself, not its --tray-child

# ---- the fake GitHub ------------------------------------------------------------------------------------------
dl="$work/srv/$repo/releases/download/v$new_version"
mkdir -p "$dl" "$work/srv/repos/$repo/releases"
cp "$new_dmg" "$dl/$name"
serve_sums() { echo "$1  $name" > "$dl/SHA256SUMS.txt"; }
serve_sums "$(shasum -a 256 "$new_dmg" | cut -d' ' -f1)"
printf '{"tag_name": "v%s", "draft": false, "prerelease": false, "assets": [{"name": "%s"}, {"name": "SHA256SUMS.txt"}]}' \
  "$new_version" "$name" > "$work/srv/repos/$repo/releases/latest"
python3 -m http.server "$port" --bind 127.0.0.1 --directory "$work/srv" > /dev/null 2>&1 &
web=$!
export SKILLDESK_UPDATE_TEST_ORIGIN="http://127.0.0.1:$port"
wait_until 20 curl -fs "http://127.0.0.1:$port/repos/$repo/releases/latest" > /dev/null || fail "the fake release server did not start"

# ---- 1. the good update -----------------------------------------------------------------------------------------
put_app "$old_dmg"
[ "$(app_version)" = "$old_version" ] || fail "the old build says $(app_version), not $old_version"
rm -f "$store/update-apply.log" "$store/update-attempt.json"
"$bin" --hidden > "$work/old.txt" 2>&1 &
first=$!
wait_until 180 is_new || fail "the app did not upgrade itself to $new_version within 3 minutes (still $(app_version))"
wait_until 30 bash -c "! kill -0 $first 2> /dev/null" || fail "the old app is still running after the upgrade"
wait_until 60 running_other_than "$first" || fail "the new version was not opened again after the upgrade"
sleep 8
second=$(pgrep -f -- "$bin --hidden" | grep -vx "$first" | head -n 1) || fail "the new version opened and then quit within 8 seconds"
ps -o args= -p "$second" | grep -q -- "--hidden" || fail "the new version was not started hidden: $(ps -o args= -p "$second")"
[ ! -e "$app.update-old" ] && [ ! -e "$app.update-new" ] || fail "the swap left its temp copies behind"
codesign --verify --deep --strict "$app" || fail "the upgraded app's signature does not verify"
echo "OK: $old_version upgraded itself to $new_version and opened again hidden"
pkill -f "$bin" || true
sleep 3

# ---- 2. a download that does not match the list is never used -----------------------------------------------------
put_app "$old_dmg"
rm -f "$store/update-apply.log"
serve_sums "$(printf '0%.0s' {1..64})"
"$bin" --hidden > "$work/old2.txt" 2>&1 &
third=$!
sleep 60  # the check starts 15 seconds after the app does
kill -0 "$third" 2> /dev/null || fail "the app quit although the download did not match its hash"
[ "$(app_version)" = "$old_version" ] || fail "a download that did not match its hash was installed"
[ ! -e "$store/update-apply.log" ] || fail "the helper script ran for a download that did not match its hash"
[ -z "$(ls -d "${TMPDIR:-/tmp}"/skilldesk-update-* 2> /dev/null)" ] || fail "the rejected download was not deleted"
echo "OK: a download with a wrong hash was dropped and the old app kept running"
