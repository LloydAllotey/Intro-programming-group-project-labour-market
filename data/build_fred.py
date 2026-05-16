"""
Build the DuckDB database used by the BeveridgeFramework class.

This is an ETL. It pulls a fixed set of US labour-market series from the FRED API,
normalises them into a tidy long-format observations table and writes the result
to data/fred.db. After the database is built, no other code in this project touches
the FRED API or any CSV. Thus, every read goes through DuckDB.

Run from the repo root:     uv run python data/build_fred.py

Requires FRED_API_KEY to be set in a local .env file.
"""
import os
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

# ─── CONFIG ─────────────────────────────────────────────────────────────────────

# The single point where we list every series the project uses.`category` 
# is our own grouping. It lets the BeveridgeFramework class filter series
# by purpose without hard-coding series IDs.
SERIES = {
    "JTSJOR":     {"category": "beveridge"},      # Job Openings Rate
    "UNRATE":     {"category": "beveridge"},      # Civilian Unemployment Rate
    "JTSQUR":     {"category": "stress_index"},   # Quits Rate
    "JTSHIL":     {"category": "stress_index"},   # Hires Rate
    "LNS12032194":{"category": "stress_index"},   # Involuntary part time
    "PAYEMS":     {"category": "stress_index"},   # Total Nonfarm Payrolls (denominator)
    "USREC":      {"category": "recession"},      # NBER Recession Indicator
    "LNS14027662":{"category": "education"},      # Unemployment, Bachelor's+, 25+
}

# We only care about the period covered by the research question.
# JOLTS (job openings, quits, hires) only starts December 2000 — earlier
# requests still return data but it will be empty (NaN).

START_DATE = "2000-01-01"

# Resolve paths relative to this script so it runs the same from any CWD
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "fred.db"

# ─── EXTRACT ────────────────────────────────────────────────────────────────────

def fetch_from_fred() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pull every configured series and its metadata from FRED.

    Returns a tuple(series_df, observations_df) shaped for direct insertion
    into the database. We do all network input/output here, so the rest of 
    the pipeline can be tested without hitting FRED again.
    """
    load_dotenv()
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise RuntimeError(
            "FRED_API_KEY is not set. Add your key to .env"
        )

    fred = Fred(api_key=api_key)

    series_rows = []
    obs_frames = []

    for series_id, cfg in SERIES.items():
        print(f"  fetching {series_id} ...")

        # Metadata: title, units, frequency, SA, category
        info = fred.get_series_info(series_id)
        series_rows.append({
            "series_id":           series_id,
            "title":               info["title"],
            "units":               info["units_short"],
            "frequency":           info["frequency_short"],
            "seasonal_adjustment": info["seasonal_adjustment_short"],
            "category":            cfg["category"],
        })

        # Observations: a pandas Series indexed by date
        values = fred.get_series(series_id, observation_start=START_DATE)
        obs_frames.append(
            pd.DataFrame({
                "series_id": series_id,
                "date":      values.index,
                "value":     values.values,
            })
        )

    series_df = pd.DataFrame(series_rows)
    observations_df = pd.concat(obs_frames, ignore_index=True)
    return series_df, observations_df

# ─── TRANSFORM ──────────────────────────────────────────────────────────────────

def clean(observations_df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop rows FRED returns as missing and enforce types.

    FRED uses NaN for missing observations. Keeping them would force every 
    downstream query to filter them out manually, so we strip them once here.
    """
    n_before = len(observations_df)
    observations_df = observations_df.dropna(subset=["value"]).copy()
    observations_df["date"]  = pd.to_datetime(observations_df["date"]).dt.date
    observations_df["value"] = observations_df["value"].astype(float)
    n_after = len(observations_df)
    print(f"  dropped {n_before - n_after} NaN observations ({n_after} kept)")
    return observations_df

# ─── LOAD ───────────────────────────────────────────────────────────────────────

def write_database(series_df: pd.DataFrame, observations_df: pd.DataFrame) -> None:
    """
    Create data/fred.db and populate the two tables.

    Our One Dimension Star Schema:
        series —> one row per series, holds metadata 'Dimension Table'
        observations —> one row per (series, date), holds the value 'Fact Table'

    series.series_id is the primary key. observations.(series_id, date) is a
    composite primary key and series_id is a foreign key into series. That
    relationship is what makes this a relational database rather than a flat
    file, where no observation can exist without a series it belongs to.
    """
    # If we are rerunning, start from a clean file
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = duckdb.connect(str(DB_PATH))

    conn.execute("""
        CREATE TABLE series(
            series_id            VARCHAR PRIMARY KEY,
            title                VARCHAR NOT NULL,
            units                VARCHAR,
            frequency            VARCHAR,
            seasonal_adjustment  VARCHAR,
            category             VARCHAR NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE observations(
            series_id  VARCHAR NOT NULL REFERENCES series(series_id),
            date       DATE    NOT NULL,
            value      DOUBLE  NOT NULL,
            PRIMARY KEY (series_id, date)
        )
    """)

    # As DuckDB recognises in scope pandas DataFrames by name, we can INSERT
    # directly from them without writing a CSV.
    conn.execute("INSERT INTO series SELECT * FROM series_df")
    conn.execute("INSERT INTO observations SELECT * FROM observations_df")

    conn.close()

# ─── VERIFY ─────────────────────────────────────────────────────────────────────

def summarise() -> None:
    """Print a small report as sanity-check."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    print("\nSeries written:")
    print(conn.execute("""
        SELECT category, series_id, title
        FROM   series
        ORDER  BY category, series_id
    """).df().to_string(index=False))

    print("\nObservation coverage:")
    print(conn.execute("""
        SELECT series_id,
               COUNT(*)   AS n_obs,
               MIN(date)  AS first_date,
               MAX(date)  AS last_date
        FROM   observations
        GROUP  BY series_id
        ORDER  BY series_id
    """).df().to_string(index=False))
    conn.close()

# ─── ENTRY POINT ────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"Building {DB_PATH.relative_to(PROJECT_ROOT)} from FRED ...\n")
    series_df, observations_df = fetch_from_fred()
    observations_df = clean(observations_df)
    write_database(series_df, observations_df)
    print(f"\nWrote {DB_PATH.relative_to(PROJECT_ROOT)}")
    summarise()


if __name__ == "__main__":
    main()
