# Exchange references and source assumptions

These references were reviewed on 21 November 2026. Exchange pages and contract terms can change; the running project must use date-valid contract masters and recheck sources rather than relying on this note as a permanent instrument specification.

## NSE

- [NSE All Reports](https://www.nseindia.com/all-reports#cr_equity_archives) provides historical index data and its date-range download controls.
- [NSE Indices historical data](https://www.niftyindices.com/reports/historical-data) provides historical OHLC data for NSE indices, including Nifty 50, Nifty Bank, FINNIFTY, Nifty Midcap Select, and Nifty Next 50.
- [NSE F&O All Reports](https://www.nseindia.com/all-reports-derivatives#cr_deriv_equity_archives) lists the current F&O-UDiFF Common Bhavcopy Final ZIP and other date-based derivatives reports.
- [NSE contract-wise F&O price/volume archives](https://www.nseindia.com/report-detail/fo_eq_security) provide historical contract-level data for the derivatives analysis.
- [NSE website policy circular](https://archives.nseindia.com/content/circulars/WEB45032.pdf) describes the website's front-end download controls and restrictions on scripted access.
- [Equity derivatives products](https://betanseapi.nseindia.com/static/products-services/equity-derivatives-products) lists Nifty 50, Nifty Bank, Nifty Financial Services, Nifty Midcap Select, and Nifty Next 50 among current index derivatives products.
- [Contract specifications](https://betanseapi.nseindia.com/static/products-services/equity-derivatives-contract-specifications) describes current trading cycles, expiry rules, strike intervals, and contract fields. The page can change; use it as a reference, not as a substitute for the historical contract master.
- [Contract information](https://betanseapi.nseindia.com/static/products-services/equity-derivatives-contract-information) provides the current information/download area, including permitted lot sizes and quantity-freeze references.
- [NSE circular on discontinuing weekly BANKNIFTY, FINNIFTY, and MIDCPNIFTY contracts](https://nsearchives.nseindia.com/content/circulars/FAOP64506.pdf) is a concrete example of why expiry availability must be date-aware. The current contract-specification page is the operative reference for present cycles.

## BSE

- [BSE Annual Report 2023–24](https://www.bseindia.com/downloads1/e1c61868-40f9-4105-a34e-71a6b6c28f0b.pdf) records the launch of derivatives contracts based on Sensex and Bankex.
- [BSE market-data portal](https://marketdata.bseindia.com/) describes index OHLC and equity-derivatives data products and access plans. Confirm the user’s available BSE source, historical depth, terms, and file layout before promising automated downloads or a complete backtest.
- BSE’s current contract master and notices must determine the expiry, strike grid, lot size, and tradable contracts by date. Do not hard-code an old weekday or lot-size announcement.

## Data claims to keep bounded

- End-of-day premium and OI do not identify the buyer/seller’s intent or trade aggressor.
- OI concentration is descriptive; it is not guaranteed support/resistance.
- No source is assumed to provide historical order-book depth, reliable trade direction, or full historical Greeks unless explicitly verified.
- Every calculation should carry source and coverage metadata. Missing BSE or NSE fields should lower coverage/confidence instead of being imputed silently.
