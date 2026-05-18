"""
Build the DuckDB database used by the BeveridgeFramework class.

This is an ETL process. It pulls US labour market series from the FRED API
adds master's enrollment data from the NCES databank, and organizes everything into
a long-format observations table in data/fred.db. This DuckDB database is built so that
there is no need to retrieve data from FRED API or CSVs throughout the project.


Run from the repo root: uv run python data/build_fred.py

Save FRED_API_KEY in a local .env file.
"""

import os
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

# ─── configuration ─────────────────────────────────────────────────────────────────────

# The single point where we list every series the project uses.`category`
# is our own grouping. It lets the BeveridgeFramework class filter series
# by purpose without hard-coding series IDs.
SERIES = {
    "JTSJOR": {"category": "beveridge"},  # Job Openings Rate
    "UNRATE": {"category": "beveridge"},  # Civilian Unemployment Rate
    "JTSQUR": {"category": "stress_index"},  # Quits Rate
    "JTSHIL": {"category": "stress_index"},  # Hires Rate
    "LNS12032194": {"category": "stress_index"},  # Involuntary part time
    "PAYEMS": {"category": "stress_index"},  # Total Nonfarm Payrolls (denominator)
    "USREC": {"category": "recession"},  # NBER Recession Indicator
    "LNS14027662": {"category": "education"},  # Unemployment, Bachelor's+, 25+
}

# Master's enrollment is NOT on the FRED API. We source it from the NCES Digest
# of Education Statistics Table 303.80, "Total postbaccalaureate fall
# enrollment in degree-granting postsecondary institutions".
# https://nces.ed.gov/programs/digest/d23/tables/dt23_303.80.asp
#
# The figures below are actual reported enrollment from the 2023 NCES publication (Table 303.80)
# All institutions, both sexes, full- and part-time combined
# NCES has not released a figure for fall 2023. We thus exclude the Year.
NCES_MASTERS_ENROLLMENT = {
    2000: 2156896,
    2001: 2212377,
    2002: 2354634,
    2003: 2431117,
    2004: 2491414,
    2005: 2523511,
    2006: 2574639,
    2007: 2644598,
    2008: 2737094,
    2009: 2849415,
    2010: 2937011,
    2011: 2933287,
    2012: 2908840,
    2013: 2900373,
    2014: 2914956,
    2015: 2941531,
    2016: 2972255,
    2017: 3005115,
    2018: 3035042,
    2019: 3072639,
    2020: 3142851,
    2021: 3211199,
    2022: 3182544,
}

# We only care about the period covered by the research question.
# JOLTS (job openings, quits, hires) only starts December 2000, earlier
# requests still return data but it will be empty (NaN).

START_DATE = "2000-01-01"

# Resolve paths relative to this script so it runs the same from any CWD
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "fred.db"

# ─── extract data from FRED ────────────────────────────────────────────────────────────────────


def fetch_from_fred() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pull every configured series and its metadata from FRED.

    Returns a tuple(series_df, observations_df) ready to insert
    into the database.
    """
    load_dotenv()
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise RuntimeError("FRED_API_KEY is not set. Add your key to .env")

    fred = Fred(api_key=api_key)

    series_rows = []
    obs_frames = []

    for series_id, cfg in SERIES.items():
        print(f"  fetching {series_id} ...")

        # Metadata: title, units, frequency, SA, category
        info = fred.get_series_info(series_id)
        series_rows.append(
            {
                "series_id": series_id,
                "title": info["title"],
                "units": info["units_short"],
                "frequency": info["frequency_short"],
                "seasonal_adjustment": info["seasonal_adjustment_short"],
                "category": cfg["category"],
            }
        )

        # Observations: a pandas Series indexed by date
        values = fred.get_series(series_id, observation_start=START_DATE)
        obs_frames.append(
            pd.DataFrame(
                {
                    "series_id": series_id,
                    "date": values.index,
                    "value": values.values,
                }
            )
        )

    series_df = pd.DataFrame(series_rows)
    observations_df = pd.concat(obs_frames, ignore_index=True)
    return series_df, observations_df


# ─── extract NCES master's enrollment data ──────────────────────────────────────────────────


def build_nces_enrollment() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Master's enrollment from the NCES Digest of Education Statistics.

    NCES enrollment is not on the FRED API, so the annual figures are hard-coded
    in NCES_MASTERS_ENROLLMENT above. This function returns the same
    (series_df, observations_df) shape as fetch_from_fred() so the two sources
    can simply be concatenated before loading.

    FRED stays the core data source: 8 of the 9 series come from FRED. NCES
    enrollment is the single supplementary series, and it is the response
    variable of the research question, so it earns its place rather than
    padding the database.
    """
    # Defensive programming: all years must have a value, else raise an error
    missing = [year for year, value in NCES_MASTERS_ENROLLMENT.items() if value is None]
    if missing:
        raise RuntimeError(
            "NCES_MASTERS_ENROLLMENT still has placeholder (None) values for "
            f"years {missing}. Fill them in from the NCES Digest (Table 303.80) "
            "before building the database."
        )

    series_row = pd.DataFrame(
        [
            {
                "series_id": "MASTERS_ENROLLMENT",
                "title": "Total postbaccalaureate fall enrollment, US",
                "units": "Number of students",
                "frequency": "A",
                "seasonal_adjustment": "NSA",
                "category": "enrollment",
            }
        ]
    )

    # NCES figures are annual, we assign the date 'January 1st'
    obs_row = pd.DataFrame(
        {
            "series_id": "MASTERS_ENROLLMENT",
            "date": [
                pd.Timestamp(year=y, month=1, day=1) for y in NCES_MASTERS_ENROLLMENT
            ],
            "value": list(NCES_MASTERS_ENROLLMENT.values()),
        }
    )
    return series_row, obs_row


# ─── clean and transform ──────────────────────────────────────────────────────────────────


def clean(observations_df: pd.DataFrame) -> pd.DataFrame:
    """
    Only complete observations are kept.

    """
    n_before = len(observations_df)
    observations_df = observations_df.dropna(subset=["value"]).copy()
    observations_df["date"] = pd.to_datetime(observations_df["date"]).dt.date
    observations_df["value"] = observations_df["value"].astype(float)
    n_after = len(observations_df)
    print(f"  dropped {n_before - n_after} NaN observations ({n_after} kept)")
    return observations_df


# ─── write to database ───────────────────────────────────────────────────────────────────────


def write_database(series_df: pd.DataFrame, observations_df: pd.DataFrame) -> None:
    """
    Create data/fred.db and populate the two tables.

    We follow a One Dimension Star Schema:
        series: one row per series with metadata (dimension table)
        observations: one row per (series, date) with the value (fact table)

    series.series_id is the primary key. observations.(series_id, date) is a
    composite primary key and series_id is a foreign key into series. No observation
    can exist without belonging to a series (this is a relational database).
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

    # DuckDB recognizes in scope pandas DataFrames so we can INSERT
    # directly from them. There is no need for a CSV file.
    conn.execute("INSERT INTO series SELECT * FROM series_df")
    conn.execute("INSERT INTO observations SELECT * FROM observations_df")

    conn.close()


# ─── verification ─────────────────────────────────────────────────────────────────────


def summarise() -> None:
    """Print a small report as sanity-check."""
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    print("\nSeries written:")
    print(
        conn.execute("""
        SELECT category, series_id, title
        FROM   series
        ORDER  BY category, series_id
    """)
        .df()
        .to_string(index=False)
    )

    print("\nObservation coverage:")
    print(
        conn.execute("""
        SELECT series_id,
               COUNT(*)   AS n_obs,
               MIN(date)  AS first_date,
               MAX(date)  AS last_date
        FROM   observations
        GROUP  BY series_id
        ORDER  BY series_id
    """)
        .df()
        .to_string(index=False)
    )
    conn.close()


# ─── entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    print(f"Building {DB_PATH.relative_to(PROJECT_ROOT)} ...\n")

    print("FRED series:")
    fred_series, fred_obs = fetch_from_fred()

    print("NCES supplement:")
    nces_series, nces_obs = build_nces_enrollment()

    # Concatenate the two sources, then clean and load them together.
    series_df = pd.concat([fred_series, nces_series], ignore_index=True)
    observations_df = pd.concat([fred_obs, nces_obs], ignore_index=True)
    observations_df = clean(observations_df)

    write_database(series_df, observations_df)
    print(f"\nWrote {DB_PATH.relative_to(PROJECT_ROOT)}")
    summarise()


if __name__ == "__main__":
    main()
