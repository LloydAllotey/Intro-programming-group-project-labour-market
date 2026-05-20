# Group 37 — Labour Market Conditions and Further Education in the US since 2000

## Authors

- Allotey Kelvin, Lloyd
- Braun, Finn
- Bruehwiler, Nils
- Troillet, Cyril

**Research question.** Do young Americans pursue further education in response to deteriorating labour market conditions in the United States since 2000?

The Beveridge curve plots job openings against unemployment. It gives a quick visual read on how tight the labour market is. We plot the curve for US data since 2000. We also overlay master's enrollment over the unemployment rate and our own "stress index" measuring the condition of the labour market, to check whether downturns push people back into school. The stress index combines several FRED series, with weights the user can change. The three recessions in the window (2001, 2008, 2020) are also taken into consideration by displaying them as a vertical grey band. The class is written so the same logic can be reused for a different country or sector by swapping the input data.

## Requirements

- Python 3.13
- [uv]
- [Quarto]

## Run the analysis

**Windows (PowerShell):**

```powershell
uv sync
$env:QUARTO_PYTHON = ".venv\Scripts\python.exe"
quarto render notebooks\analysis.qmd
quarto render notebooks\user_guide.qmd
```
**macOS / Linux:**

```bash
uv sync
export QUARTO_PYTHON=.venv/bin/python
quarto render notebooks/analysis.qmd
quarto render notebooks/user_guide.qmd
```

`data/fred.db` is committed, so no API key is needed to render the notebooks.

## Rebuild the database

```bash
uv run python data/build_fred.py
```

This needs a `FRED_API_KEY` in a local `.env` file. Template is `.env.example`.

## Project structure

```
├── data/
│   ├── build_fred.py             # FRED + NCES to DuckDB ETL script
│   ├── fred.db
│   └── star_schema_sketch.png
├── src/
│   └── labourmarket_framework.py    # LabourMarketFramework class
└── notebooks/
    ├── analysis.qmd / .html
    └── user_guide.qmd / .html

```

## Data

Time series come from [FRED](https://fred.stlouisfed.org). Master's enrollment is from the [NCES Digest of Education Statistics](https://nces.ed.gov/programs/digest/).

| Series ID      | Description                                                          | Use                                       |
|----------------|----------------------------------------------------------------------|-------------------------------------------|
| `JTSJOR`       | Job Openings Rate, Total Nonfarm                                     | Beveridge x-axis                          |
| `UNRATE`       | Civilian Unemployment Rate                                           | Beveridge y-axis                          |
| `JTSQUR`       | Quits Rate, Total Nonfarm                                            | input to stress index                     |
| `JTSHIL`       | Hires Rate, Total Nonfarm                                            | input to stress index                     |
| `USREC`        | NBER Recession Indicator                                             | recession shading on plots                |
| `LNS12032194`  | Employed Part Time for Economic Reasons                              | involuntary part-time (share via PAYEMS)  |
| `PAYEMS`       | Total Nonfarm Payrolls                                               | denominator for the part-time share       |
| `LNS14027662`  | Unemployment Rate, Bachelor's+, age 25 and older                     | outside option for the grad-school choice |
| `MASTERS_ENROLLMENT` | Total postbaccalaureate fall enrollment, US (NCES Digest, Table 303.80) | response variable, the further-education outcome |
