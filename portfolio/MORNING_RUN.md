# Morning run (scheduled task instructions)

A scheduled task fires this every weekday morning (07:37 Europe/Lisbon) in a fresh
cloud session. The repo only supplies the code. The dashboard holds the price history.

Dashboard: https://claude.ai/artifact/5LBRd9Xqxsr6h62pbRWUM3 (dataset document `datasets/snapshots`).

1. Get the code: `git clone https://github.com/theotonio27/General.git` (if it isn't
   already there), then `git checkout claude/great-rubin-48ti85`, then `git pull`.
2. Get the current history from the dashboard, not from git:
   - ArtifactData `get` collection `datasets`, doc `snapshots`. Note `version` and
     `data.source.url` (`/_blob/<old id>`).
   - Artifact `read` with `url` = the dashboard and `path` = `<old id>`. Copy the saved
     file over `portfolio/snapshots.json`.
3. `cd portfolio && python3 fetch_prices.py`
   - Exit 4: nothing new (holiday, or this close is already stored). Stop and say
     "No new close since <date>". Write nothing.
   - Exit 3: Yahoo Finance is unreachable. Stop and report it.
   - Exit 2: more than 3 tickers are unpriced. Continue, and name them at the end.
4. `python3 -m unittest test_attribution` must pass. If it fails, stop.
5. Upload: Artifact `publish` with `url` = the dashboard, `file_path` = `portfolio/snapshots.json`,
   and `asset: true`. Note the new `/_blob/<new id>`.
6. Repoint: ArtifactData `update` collection `datasets`, doc `snapshots`, `if_version` =
   the version from step 2. Put the fields at the top level of `data`, not under a
   nested `data` key:
   `{"source": {"kind": "file", "format": "json", "name": "snapshots.json", "url": "/_blob/<new id>"},
     "updated": {"at": "<now, ISO>", "by": "Morning run"}}`.
   Then delete the old blob (Artifact `delete`, `url` = the dashboard, `path` = `<old id>`).
7. Optional backup: commit `portfolio/snapshots.json` with message `Morning prices <date>`
   and try `git push origin claude/great-rubin-48ti85` once. If the push is refused,
   don't retry. Just mention it in the final message.
8. Final message (this becomes the push notification): the script's `summary` line and
   any unpriced tickers.

When you trade, update the quantity in `holdings.json` (or ask Claude to). The next
morning's snapshot uses the new quantities. The dashboard shows the trade under
"Buys and sells", so it isn't counted as performance.
