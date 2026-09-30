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
PY="$REPO/.venv/bin/python"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/$(date +%F).log") 2>&1
# shellcheck source=lib.sh
. "$REPO/automation/vm/lib.sh"

echo "== $(date '+%F %T %Z') ipo-radar daily run"
setup_run
export RHP_MAX_PER_RUN="${RHP_MAX_PER_RUN:-30}"   # prospectus downloads + analyses per run
export CG_MAX_FETCH="${CG_MAX_FETCH:-300}"        # Chittorgarh IPO pages per run

"$PY" automation/update.py
"$PY" automation/track_rule.py
if [ "$(date +%u)" = 7 ]; then
  echo "== Sunday: retrain"
  "$PY" automation/retrain.py
fi

commit_and_push "data: daily refresh $(date +%F)" || exit 1
publish_kaggle_if_changed
echo "== $(date '+%F %T %Z') done"
