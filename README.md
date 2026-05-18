# Group 37 — Beveridge Curve and Further Education in the US since 2000

## Authors

- Allotey Kelvin, Lloyd
- Braun, Finn
- Bruehwiler, Nils
- Troillet, Cyril

**Research question.** Do young Americans pursue further education in response to deteriorating labour market conditions in the United States since 2000?

The Beveridge curve plots job openings against unemployment. It gives a quick visual read on how tight the labour market is. The project is built around it. We plot the curve for US data since 2000 and overlay master's enrollment, to check whether downturns push people back into school. The three recessions in the window (2001, 2008, 2020) are also compared on a single chart so the depth and shape of each are visible. A separate output method computes a stress index that combines several FRED series, with weights the user can change. The class is written so the same logic can be reused for a different recession, country or sector by swapping the input data.

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
│   ├── er_diagram.md             # ER diagram (Mermaid)
│   ├── fred.db
│   └── star_schema_sketch.png
├── src/
│   └── beveridge_framework.py    # BeveridgeFramework class
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
