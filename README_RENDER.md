# Deploy the index dashboard on Render

This project deploys as its own Python web service. It uses its own GitHub repository, Render service, password, and Google Drive market ZIP. Do not connect it to the stock dashboard's repository or reuse the stock dashboard's Drive file.

## Before creating the Render service

1. Create a private, empty GitHub repository under the separate GitHub account. Use the `main` branch. Do not send account passwords, recovery codes, or two-factor codes.
2. Push this project’s source code to that repository. The `data/` directory, SQLite database, reports, and ZIP files are excluded by `.gitignore`.
3. On the laptop dashboard, download the selected NSE and BSE reports over the same date range, run analysis through the latest stored index date, and use **Data centre → Prepare market file for Drive → Download prepared market file**. The export checks for OHLC history for all seven indices.
4. Upload the downloaded ZIP to a new, separate file in the existing Google Drive account. The current downloader uses the Drive file ID without signing into Google, so set this one file to **Anyone with the link · Viewer**, then copy its file ID. Anyone who gets that link can read the ZIP; it contains public market data for the seven indices plus the saved analysis and forecasts, with no stock symbols or personal records. If you want the Drive file to remain private, pause here; the hosted downloader will need a separate Google service-account setup.

## Create the independent Render service

1. In the existing Render workspace, connect/authorize the new GitHub account for the new repository.
2. Create a Blueprint from the repository's `render.yaml`. It creates `nse-index-options-research`, a separate web service with its own Render URL and automatic deploys from `main`.
3. When prompted, set `INDEX_DASHBOARD_PASSWORD` to a private password of at least 12 characters and set `INDEX_DRIVE_FILE_ID` to the new index ZIP's file ID. Render generates `INDEX_SESSION_SECRET`.
4. Wait for the service to become Live and its `/health` check to pass. Sign in using the dashboard password. The service loads the configured Drive snapshot when its hosted database is empty.

The Render service reads only its own `INDEX_*` settings. Never put passwords, session secrets, service-account keys, local databases, or market ZIPs in GitHub. The Drive snapshot is public market data only and contains no stock symbols or personal records.

## Daily data update

Normally, update data on the laptop, run analysis, prepare/download a fresh market ZIP, and upload it to the same separate index Drive file. On Render, click **Load saved Google Drive copy**. No GitHub commit or Render redeploy is needed for market-data updates.

## Update from Render when the laptop is unavailable

1. Open **Data centre** on the hosted dashboard. Choose the date range and report types, then click **Download selected reports**. Render fetches fresh NSE and BSE reports, validates them, and stores only the seven supported index series/contracts in its temporary working copy. Manual report-file imports remain laptop-only.
2. Click **Run analysis** and wait for the task to finish. The prepared file requires complete index OHLC history for all seven underlyings and a current saved analysis.
3. Click **Prepare market file for Drive**, then **Download prepared market file**. On iPhone, save the ZIP to Files and upload it to the existing separate index market file in Google Drive. Preserve the Drive file ID; if Drive creates a new file, update `INDEX_DRIVE_FILE_ID` in the Render service's private settings.
4. Return to Render and click **Load saved Google Drive copy**. Confirm replacing the temporary hosted data when prompted. This verifies and reloads the Drive snapshot into the Render copy.

Render's free filesystem is temporary. Downloaded reports and analysis can disappear if the service restarts before the ZIP is uploaded to Drive. Save the ZIP promptly. No GitHub commit or Render redeploy is needed for market-data updates; code changes deploy when pushed to `main`.
