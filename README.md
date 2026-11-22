# After the Close — NSE/BSE Index Options Research

**Status: separate laptop master with an optional hosted Render copy.** The frozen `INDEX-PRESSURE-1.0` baseline produces a cautious next-session classification and an evidence breakdown. Its rule score is not a probability, and historical results must be checked before considering any option position. This is a standalone project, separate from the stock accumulation dashboard.

## Start the dashboard

Double-click **Start Dashboard.cmd** in this folder. It opens the dashboard at a loopback address with a free port chosen for that run. Keep its console window open while using the dashboard; close that window to stop it. The existing accumulation dashboard uses its own port, folder, and database; this program stores its database at **data/index_options_research.sqlite3** in this project.

Python 3.10 or newer is required. The launcher uses the Windows Python launcher (`py -3`), then falls back to `python` on PATH. Before opening the browser, the program checks that the dashboard answers a local HTTP request.

## Fixed research universe

The program accepts only these seven index underlyings and their index futures/options contracts:

| Display name | Exchange | Underlying |
| --- | --- | --- |
| Nifty 50 | NSE | NIFTY |
| Nifty Bank | NSE | BANKNIFTY |
| Sensex | BSE | SENSEX |
| FINNIFTY (Nifty Financial Services) | NSE | FINNIFTY |
| BANKEX | BSE | BANKEX |
| Nifty Midcap Select | NSE | MIDCPNIFTY |
| Nifty Next 50 | NSE | NIFTYNXT50 |

No company symbols, cash-stock records, constituent data, breadth data, or other index universes are imported or analyzed. Contract rows retain the index underlying, exchange, expiry, strike, and CE/PE identity.

## Data centre, downloads, and imports

Open **Data centre**, choose **From** and **Through**, select the report types, and click **Download from NSE**. The download runs only when you start it. It checks up to 93 calendar days and fetches a fresh NSE index OHLC report and/or daily F&O UDiFF archive for every selected weekday, even when that date is already stored. Reports are validated before stored rows for that day are replaced; a failed or partial download leaves existing rows intact and can be retried. The raw report is processed in memory, not saved as a downloaded file. A progress log records refreshed and unavailable dates. No background download is scheduled.

- **Index OHLC:** the report downloader processes NSE's daily index history report and retains only the five in-scope NSE indices. For manual imports, choose the matching index if the CSV has no index-name column.
- **NSE options and futures:** the downloader processes the daily F&O UDiFF ZIP. The archive is a combined market report, so rows are read in memory; only contracts for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, and NIFTYNXT50 are stored. Single-stock and other out-of-scope rows are discarded. The raw ZIP/CSV is not copied into this project. The same UDiFF ZIP or contract-wise CSV can be imported manually if a report download is unavailable.
- **Sensex and BANKEX:** these are BSE indices, so use BSE's official market-data source for their OHLC and contract history. The project accepts normalized CSV imports for these two underlyings; NSE does not provide their contract reports.
- **Normalized CSVs:** index options and futures may also be imported using the formats below. The exchange column can be omitted because it is inferred from the selected underlying.

The official NSE index-history, F&O archive, and contract-wise price/volume pages are linked below the download controls for manual recovery or inspection. The import controls accept multiple CSV/ZIP files and can optionally apply the selected date range. Index OHLC, options, futures, source dates, and the local database path/size are listed under **Coverage & freshness**.

- Index bars: `exchange,underlying,date,open,high,low,close`
- Options: `exchange,underlying,date,expiry,strike,option_type,close,volume,oi`
- Futures: `exchange,underlying,date,expiry,close,volume,oi`

Option and futures files may also include `open,high,low,oi_change,lot_size`. Dates accept `YYYY-MM-DD` or `DD-MM-YYYY`; `CE`/`PE` and `Call`/`Put` are accepted. The laptop database and import coverage metadata are kept in this project's `data/` directory; the Drive ZIP is a separate market-only snapshot for Render.

## Hosted Render copy and Google Drive updates

The laptop database remains the master. The hosted dashboard is a temporary working copy, separate from the stock dashboard and its Drive file.

1. On the laptop, download/import the market reports and run analysis through the latest stored index date.
2. In **Data centre**, click **Prepare market file for Drive**, then **Download prepared market file**. This creates a ZIP containing the index-only SQLite database and a manifest with its date, counts, and SHA-256 checksum. It contains no company symbols or personal data.
3. Upload that ZIP to a **separate file** in the same Google Drive account. Set Drive access so the hosted downloader can read that market-only file. Put its file ID in the Render service's private `INDEX_DRIVE_FILE_ID` setting. Do not use the stock dashboard's Drive file ID.
4. Push code to the separate GitHub repository to deploy code changes. Daily data updates do not require a GitHub commit: upload the new ZIP to Drive, then use **Load saved Google Drive copy** on the hosted dashboard.

The hosted service attempts to restore the configured Drive file when its database is empty. Render's free filesystem is temporary, so Drive is the hosted copy's recovery source. Analysis can run online, but market report downloads/imports and publishing a new master snapshot happen on the laptop. The Render dashboard has its own sign-in password and session secret; set these in Render, never in GitHub.

The index project needs its own Render web service and URL in the same Render workspace. Its `render.yaml` defines the service, health check, build/start commands, and separate `INDEX_*` settings. Create the GitHub repository under the intended account and grant Render access to that repository. Do not commit files from `data/`; the Git ignore rules exclude the local database, market ZIP, and report files.

## What the model and dashboard do

- The Market overview follows the NSE Accumulation dashboard layout: one **As of** date control, seven clickable index tiles at the top, and the selected index's data beneath. Choosing a date excludes later-dated rows from index features, derivative inputs, forecast outcomes, the OI table, and historical evaluations. Clicking an index tile leaves the page at its next-session forecast heading. **Latest data** jumps to the newest stored index date. The Data Centre's From/Through fields remain the report-download range; its coverage/file inventory describes the full local archive and is not model input.
- Research table headers can be clicked to sort ascending or descending; they also support Enter and Space from the keyboard.
- Recent index closes, five-session returns, 20-session mean, and ATR14 when enough index history is imported.
- Click **Run analysis** after downloading or importing to save a dated snapshot for all seven indices through the selected As-of date; the selected index view shows every available trading session in the prior two calendar months under the same table headers. A single chart plots index close on the left ₹ axis and PCR OI 1D change, futures OI 1D change, and futures OI 5D change on a shared right percentage axis; hovering a date shows all four values in one tooltip. The second table column includes each row's index close and its percentage change from the previous trading-session close. Every row is calculated only through its own session date. It reports nearest eligible-expiry PCR OI, its one-session percent change, nearest-expiry futures OI and its one-session percent change, plus five-session futures OI change for up to three expiries matched to the same contracts five sessions earlier. One-day comparisons require the same expiry; missing or rolled contracts display as unavailable. Imports that change data on or before the saved date mark that snapshot stale until analysis is run again.
- The same **Run analysis** also saves an append-only forecast for every index with OHLC coverage. Each forecast records its signal date, model version, input revision, RISE/FALL/RANGE/NO EDGE/INSUFFICIENT DATA label, component scores, evidence, and warnings. A later analysis can add the next imported session's observed outcome without rewriting the forecast. If imported data changes, a new input revision creates a new forecast record.
- A directional or range classification requires at least 20 index sessions; shorter histories remain **INSUFFICIENT DATA**. The current rules combine index trend/location/range, same-expiry futures price and OI changes, near-spot option premium/OI changes, and same-day movement among the seven indices. The dashboard displays the bounded score components separately. The detailed frozen thresholds and limitations are in [MODEL-DESIGN.md](MODEL-DESIGN.md).
- Click **Evaluate selected index** to replay past sessions chronologically. The replay calculates each signal using data dated on or before that close and labels the next imported session using a fixed ATR-relative rule. It reports coverage, accuracy, directional hit rate, and a majority-class baseline for earlier 60%, next 20%, latest 20% holdout, and all replay observations. Fewer than 30 directional calls are marked too small to interpret. This is an index-direction diagnostic, not an options-strategy backtest or a profit estimate.
- Option OI, absolute CE/PE OI changes, PCR OI, and their previous-session percentage changes grouped by expiry. The previous comparison date is shown beside the chain date; percentages use the same expiry, and side OI percentages use the previous session's total OI for that side.
- Separate CE/PE open-interest-by-strike charts for the nearest and next expiries, followed by a strike table for those two expiries only. Strikes within 10% of the option-chain date's index close are shown, with an ATM highlight within 3%.
- Nearest-expiry index-futures close, OI, OI change, and basis versus the index close, when same-day futures data is available.
- Source-file and date coverage tracking.

This model does **not** prove institutional accumulation, identify trade initiators, select a strike, recommend a trade, or estimate option-strategy profitability. “Moderate — uncalibrated” is still not a probability. The first holdout results may be too small to interpret, and the downloaded/imported data must be broad enough for an index before the model can classify it. Use the model output as a research hypothesis, then inspect the evidence and evaluate the historical sample.

For the exchange source assumptions, see [SOURCES.md](SOURCES.md).
