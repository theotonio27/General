# Morning run (scheduled task instructions)

A scheduled task fires this every weekday morning (07:37 Europe/Lisbon) in a fresh
cloud session on branch `claude/great-rubin-48ti85`. Follow these steps exactly.

Dashboard: https://claude.ai/artifact/5LBRd9Xqxsr6h62pbRWUM3 (dataset id `snapshots`).

1. `cd portfolio && python3 fetch_prices.py`
   - Exit 3: Yahoo Finance is blocked by the environment's network policy. Stop and
     report that `query1.finance.yahoo.com` / `query2.finance.yahoo.com` must be allowed.
   - Exit 2: more than 3 tickers had no price. Continue, but name them in the summary.
   - The script prints JSON with `date`, `missing` and `summary`.
2. If `snapshots.json` did not change (the date was already stored, e.g. a holiday),
   stop and say "No new close since <date>".
3. Run `python3 -m unittest test_attribution` (must pass).
4. Commit `portfolio/snapshots.json` (and `holdings.json` if a symbol was repaired) with
   message `Morning prices <date>` and push to `claude/great-rubin-48ti85`.
5. Upload the new file to the dashboard: Artifact tool, `action: publish`,
   `url` = the dashboard, `file_path` = `portfolio/snapshots.json`, `asset: true`.
6. Point the dataset at it: ArtifactData `get` `datasets/snapshots`, then `update` it
   (pinned with `if_version`) with `source.url` = the new `/_blob/<id>`,
   `source.name` = `snapshots.json`, `source.kind` = `file`, `source.format` = `json`,
   and `updated` = `{at: <now ISO>, by: "Morning run"}`.
   Then delete the previous blob (Artifact `action: delete`, `url`, `path` = old id).
7. Final message (it becomes the push notification): the `summary` line, plus any
   missing tickers.

When you trade, update the quantity in `holdings.json` (or ask Claude to). The next
morning's snapshot uses the new quantities, and the dashboard shows the trade under
"Buys and sells" so it is not counted as performance.
