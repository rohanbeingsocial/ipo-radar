# VM runner

The daily pipeline runs on a Linux VM instead of GitHub Actions: GitHub's cron started
it 4.5–6 hours late every day, a VM run has no time limit (a full RHP backfill takes
hours), NSE's index history only answers Indian IPs, and the Dhan token used for
NIFTY 1-minute data and LTPs already lives on the VM.

One-time setup (as the `ubuntu` user, repo cloned to `~/ipo-radar` over SSH with a
deploy key that has write access to this repository only):

```sh
sudo apt-get install -y python3.12-venv
cd ~/ipo-radar && python3 -m venv .venv && .venv/bin/pip install -r automation/requirements.txt
chmod +x automation/vm/daily.sh
sudo cp automation/vm/ipo-radar-daily.service automation/vm/ipo-radar-daily.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now ipo-radar-daily.timer
```

Optional secrets (never in the repo): `~/.kaggle/access_token` (Kaggle publish) and
Dhan creds via `DHAN_ENV_FILE` (defaults to `~/iposeller/.env`). Without them the run
still works: LTPs fall back to Chittorgarh, NIFTY to NSE's daily series, and the
Kaggle step skips.

Operate:

```sh
systemctl list-timers ipo-radar-daily.timer      # next run
sudo systemctl start ipo-radar-daily.service     # run now
tail -f ~/ipo-radar-logs/$(date +%F).log         # watch it
cat ~/ipo-radar/data/run_status.json             # last run's warnings
```

The GitHub workflows keep a manual "Run workflow" button as a fallback. Don't give
them a schedule again while this timer is enabled; both would commit the same files.
