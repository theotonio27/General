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
     "updated": {"at": "<output of date -u +%Y-%m-%dT%H:%M:%SZ>", "by": "Morning run"}}`.
   Don't delete the old blob (unattended runs aren't allowed to); a later manual session cleans up.
6b. Company prices (for the Companies page): first copy the dashboard's current
   `datasets/company_prices` file over `portfolio/company_prices.json`, the same way as in step 2.
   Then run `python3 fetch_company_prices.py` (it needs the updated `snapshots.json` and
   `lookthrough.json`). Upload the result and repoint `datasets/company_prices` as in steps 5–6.
   If it fails, say so, but the ETF prices from step 6 still stand.
7. Optional backup: commit `portfolio/snapshots.json` with message `Morning prices <date>`
   and try `git push origin claude/great-rubin-48ti85` once. If the push is refused,
   don't retry. Just mention it in the final message.
8. Monthly holdings refresh: only on the first weekday run of a month (today's
   day-of-month <= 3 and `datasets/lookthrough` `updated.at` is not in this month).
   - Run `python3 fetch_full_holdings.py` (full lists from iShares, Amundi and Xtrackers; Yahoo top 10
     for VGWD and RARE1). Check its report: every fund except LYTR has rows, and weights add up to
     0.95-1.01 (EUNA is about 0.74 because iShares caps the file, and VGWD/RARE1 are top 10 only).
     If a fund that had rows now has none, keep the previous `lookthrough.json` rows for it
     (re-run with `--only` the other funds) and say which one failed.
   - Then run `python3 fetch_company_prices.py` again (the top 600 companies may have changed).
   - Upload `lookthrough.json` and `company_prices.json` as assets and repoint `datasets/lookthrough`
     and `datasets/company_prices` as in step 6.
9. Final message (this becomes the push notification): the script's `summary` line and
   any unpriced tickers.

## Live refresh (the dashboard's "Atualizar" button)

The scheduled task "Portfolio refresh now" has no schedule; the dashboard's button starts it.
Keep it quick (the page waits for it) and touch nothing else:

1. Get the code as in step 1, and the current history and company prices from the dashboard as
   in steps 2 and 6b (`datasets/snapshots` and `datasets/company_prices`; note both versions).
2. `cd portfolio && python3 fetch_prices.py --live`. It stores any new close and, while a market
   session is newer than the latest close, current prices as a "live" snapshot. Exit 3: stop and
   report. Exit 4 can't happen in this mode unless nothing changed: then stop.
3. `python3 -m unittest test_attribution` must pass.
4. `python3 fetch_company_prices.py` (it prices the live date at the current moment).
5. Upload `snapshots.json` and `company_prices.json` as assets and repoint `datasets/snapshots` and
   `datasets/company_prices` (pinned with `if_version`, fields at the top level of `data`,
   `updated.by` = "Atualizar button", and `updated.at` = the real time from `date -u +%Y-%m-%dT%H:%M:%SZ`).
6. No git commit, no deleting old assets. Final message: the script's `summary` line.

When you trade, update the quantity in `holdings.json` (or ask Claude to). The next
morning's snapshot uses the new quantities. The dashboard shows the trade under
"Buys and sells", so it isn't counted as performance.
