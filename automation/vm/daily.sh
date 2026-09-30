#!/usr/bin/env bash
# Daily IPO-radar refresh on the VM (systemd: ipo-radar-daily.timer, 20:00 IST).
#
#   refresh data -> analyze new RHPs -> rebuild site + finalipodata sheet
#   -> commit + push to GitHub (Pages redeploys) -> publish to Kaggle if the dataset changed
#   Sundays also retrain the models (promotion is still gated inside retrain.py).
#
# Secrets live outside the repo: Dhan creds are read from IPOSeller's .env (it rotates
# the token), the Kaggle token from ~/.kaggle/access_token, and pushes use a deploy key
# that can write only to this repository.
set -uo pipefail

REPO="${REPO:-$HOME/ipo-radar}"
LOG_DIR="${LOG_DIR:-$HOME/ipo-radar-logs}"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/$(date +%F).log") 2>&1
find "$LOG_DIR" -name '*.log' -mtime +60 -delete

cd "$REPO"
PY="$REPO/.venv/bin/python"
export RHP_MAX_PER_RUN="${RHP_MAX_PER_RUN:-30}"   # prospectus downloads + analyses per run
export CG_MAX_FETCH="${CG_MAX_FETCH:-300}"        # Chittorgarh IPO pages per run
export DHAN_ENV_FILE="${DHAN_ENV_FILE:-$HOME/iposeller/.env}"
if [ -f "$HOME/.kaggle/access_token" ]; then
  KAGGLE_API_TOKEN="$(cat "$HOME/.kaggle/access_token")"
  export KAGGLE_API_TOKEN
fi

echo "== $(date '+%F %T %Z') ipo-radar daily run"
git fetch -q origin main && git merge -q --ff-only origin/main || {
  echo "ERROR: local main cannot fast-forward to origin/main; fix by hand"; exit 1; }
"$PY" -m pip install -q -r automation/requirements.txt

"$PY" automation/update.py
"$PY" automation/track_rule.py
if [ "$(date +%u)" = 7 ]; then
  echo "== Sunday: retrain"
  "$PY" automation/retrain.py
fi

git add data docs/data ipodata backend/app/horizon_model.pkl backend/app/listing_model_signals.json
committed=0
if git diff --cached --quiet; then
  echo "no changes"
else
  n=$("$PY" -c 'import json; print(len(json.load(open("data/run_status.json"))["warnings"]))' 2>/dev/null || echo "?")
  msg="data: daily refresh $(date +%F)"
  [ "$n" != "0" ] && msg="$msg ($n warnings, see data/run_status.json)"
  git -c user.name="ipo-radar-bot" -c user.email="ipo-radar-bot@users.noreply.github.com" \
      commit -q -m "$msg"
  committed=1
  if ! git push -q origin HEAD:main; then
    # someone pushed meanwhile: replay this run's commit on top and try once more
    git pull -q --rebase origin main && git push -q origin HEAD:main || {
      echo "ERROR: push failed"; exit 1; }
  fi
  echo "pushed: $msg"
fi

if [ "$committed" = 1 ] && git diff --name-only HEAD~1 HEAD | grep -qE '^(ipodata/|data/cg_|data/ipo_outcomes)'; then
  "$PY" automation/kaggle_publish.py || echo "WARNING: Kaggle publish failed"
fi
echo "== $(date '+%F %T %Z') done"
