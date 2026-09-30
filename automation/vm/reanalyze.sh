#!/usr/bin/env bash
# After an analyzer change: re-analyze every prospectus, then rebuild everything that
# depends on RHP scores as ONE unit (scores are not comparable across analyzer versions):
#
#   reanalyze.py -> build_dataset -> retrain --force -> site + finalipodata -> push -> Kaggle
#
# --force only skips the "beat the incumbent" comparison (the incumbent was measured on
# the old scores); a head that doesn't beat its own baseline still never ships.
# Run detached so it survives the SSH session, e.g.
#   sudo systemd-run --unit=ipo-radar-reanalyze --uid=ubuntu -p Nice=10 \
#        /home/ubuntu/ipo-radar/automation/vm/reanalyze.sh
# It stops taking new prospectuses at UNTIL (default 08:30, before the market opens) and
# rebuilds nothing if work is left; running it again resumes where it stopped.
set -uo pipefail

REPO="${REPO:-$HOME/ipo-radar}"
LOG_DIR="${LOG_DIR:-$HOME/ipo-radar-logs}"
PY="$REPO/.venv/bin/python"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_DIR/reanalyze-$(date +%F).log") 2>&1
# shellcheck source=lib.sh
. "$REPO/automation/vm/lib.sh"

echo "== $(date '+%F %T %Z') full re-analysis"
setup_run
"$PY" automation/reanalyze.py --workers "${WORKERS:-3}" --until "${UNTIL:-08:30}"
rc=$?
if [ "$rc" = 2 ]; then
  echo "stopped at the deadline with prospectuses left; nothing rebuilt. Run again to resume."
  exit 2
fi

"$PY" automation/build_dataset.py
"$PY" automation/retrain.py --force
"$PY" - <<'EOF'
import sys
sys.path.insert(0, "automation")
import update, finalsheet
update.build_site()
update.rebuild_excel()
finalsheet.build()
EOF

v=$("$PY" -c 'import json; print(json.load(open("data/reanalysis_status.json"))["analyzer_version"])')
commit_and_push "data: re-analyze every RHP on analyzer $v, rebuild dataset, retrain, rebuild site" || exit 1
publish_kaggle_if_changed
echo "== $(date '+%F %T %Z') done"
