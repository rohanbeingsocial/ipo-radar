# Shared by daily.sh and reanalyze.sh. Expects REPO, PY and LOG_DIR to be set.

setup_run() {
  # one writer at a time: the daily run waits (up to 3 h) for a re-analysis to finish
  exec 9>"/tmp/ipo-radar.lock"
  flock -w 10800 9 || { echo "ERROR: another ipo-radar run still holds the lock"; exit 1; }
  mkdir -p "$LOG_DIR"
  find "$LOG_DIR" -name '*.log' -mtime +60 -delete
  cd "$REPO" || exit 1
  export PYTHONUNBUFFERED=1                         # stream progress into the log as it happens
  export DHAN_ENV_FILE="${DHAN_ENV_FILE:-$HOME/iposeller/.env}"
  if [ -f "$HOME/.kaggle/access_token" ]; then
    KAGGLE_API_TOKEN="$(cat "$HOME/.kaggle/access_token")"
    export KAGGLE_API_TOKEN
  fi
  git fetch -q origin main && git merge -q --ff-only origin/main || {
    echo "ERROR: local main cannot fast-forward to origin/main; fix by hand"; exit 1; }
  "$PY" -m pip install -q -r automation/requirements.txt
}

# commit_and_push "<message>"  -> sets COMMITTED=1 when something was pushed
commit_and_push() {
  COMMITTED=0
  git add data docs/data ipodata backend/app/horizon_model.pkl backend/app/listing_model_signals.json
  if git diff --cached --quiet; then
    echo "no changes"
    return 0
  fi
  local n msg="$1"
  n=$("$PY" -c 'import json; print(len(json.load(open("data/run_status.json"))["warnings"]))' 2>/dev/null || echo "?")
  [ "$n" != "0" ] && [ "$n" != "?" ] && msg="$msg ($n warnings, see data/run_status.json)"
  git -c user.name="ipo-radar-bot" -c user.email="ipo-radar-bot@users.noreply.github.com" \
      commit -q -m "$msg"
  COMMITTED=1
  if ! git push -q origin HEAD:main; then
    # someone pushed meanwhile: replay this run's commit on top and try once more
    git pull -q --rebase origin main && git push -q origin HEAD:main || {
      echo "ERROR: push failed"; return 1; }
  fi
  echo "pushed: $msg"
}

publish_kaggle_if_changed() {
  if [ "${COMMITTED:-0}" = 1 ] && git diff --name-only HEAD~1 HEAD | grep -qE '^(ipodata/|data/cg_|data/ipo_outcomes)'; then
    "$PY" automation/kaggle_publish.py || echo "WARNING: Kaggle publish failed"
  fi
}
