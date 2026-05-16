"""
BeveridgeFramework — a reusable toolkit for labour-market analysis built around
the Beveridge curve (job openings vs. unemployment).

This module defines the class. It does not contain any analysis script; the
notebooks instantiate it and call its methods.

The class is layered:
    1. Data access   — read series and metadata from the DuckDB database.
    2. Helpers       — date validation, frequency alignment, recession tagging.
    3. Output methods (added in later PRs) — figures, tables, summary metrics.
"""

from datetime import datetime
from pathlib import Path

import duckdb as db
import pandas as pd


class BeveridgeFramework:
    """
    Toolkit for analysing US labour-market conditions through the Beveridge curve.

    The class reads from a local DuckDB database built by `data/build_fred.py`,
    which holds the FRED series plus the NCES enrollment series. Once
    instantiated, every output method works off that database: no network
    access, no CSV files. The same class can be repointed at a different
    database (different recession, country, sector) by passing a different
    `db_path`.

    Parameters
    ----------
    db_path : str or pathlib.Path, default "data/fred.db"
        Path to the DuckDB database. Must be readable.

    Attributes
    ----------
    db_path : pathlib.Path
        Resolved path to the database.

    Raises
    ------
    FileNotFoundError
        If the database file does not exist. Hints at the build script.
    """

    # FRED series ID for the NBER recession indicator.
    _RECESSION_SERIES_ID = "USREC"

    def __init__(self, db_path: str | Path = "data/fred.db") -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found at '{self.db_path}'. "
                "Run 'uv run python data/build_fred.py' to build it."
            )
        # Read-only mode prevents accidental writes during analysis.
        self._conn = db.connect(str(self.db_path), read_only=True)

        # Cache the list of available series IDs once. Cheaper than querying
        # on every load, and used by _validate_series_id.
        self._available_ids = (
            self._conn.execute("SELECT series_id FROM series")
            .df()["series_id"]
            .tolist()
        )

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._conn.close()

    # ─────────────────────────────────────────────────────────────────────────
    # DATA ACCESS
    # ─────────────────────────────────────────────────────────────────────────

    def available_series(self, category: str | None = None) -> pd.DataFrame:
        """
        Return the metadata of every series in the database.

        Useful for discovering what's available before calling `load_series`.

        Parameters
        ----------
        category : str or None, optional
            If given, restrict to series with this category
            (e.g. 'beveridge', 'stress_index'). Pass None to return all.

        Returns
        -------
        pandas.DataFrame
            Columns: series_id, title, units, frequency, seasonal_adjustment, category.
        """
        if category is None:
            query = "SELECT * FROM series ORDER BY category, series_id"
            return self._conn.execute(query).df()

        query = "SELECT * FROM series WHERE category = ? ORDER BY series_id"
        result = self._conn.execute(query, [category]).df()
        if result.empty:
            valid = sorted(
                self._conn.execute("SELECT DISTINCT category FROM series")
                .df()["category"]
                .tolist()
            )
            raise ValueError(
                f"No series in category '{category}'. Valid categories: {valid}."
            )
        return result

    def load_series(
        self,
        series_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.Series:
        """
        Read a single time series from the database.

        Parameters
        ----------
        series_id : str
            FRED series ID, e.g. 'UNRATE'. Must exist in the database — call
            `available_series()` to see what's there.
        start_date : str or None, optional
            Inclusive lower bound in 'YYYY-MM-DD' format.
        end_date : str or None, optional
            Inclusive upper bound in 'YYYY-MM-DD' format.

        Returns
        -------
        pandas.Series
            Indexed by date (DatetimeIndex), named after series_id, dtype float.

        Raises
        ------
        ValueError
            If `series_id` is unknown, or dates are invalid (wrong type, wrong
            format, or start_date >= end_date).
        """
        self._validate_series_id(series_id)
        self._validate_dates(start_date, end_date)

        # Read the whole series from the database, then apply any date
        # filters in pandas.
        query = "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date"
        df = self._conn.execute(query, [series_id]).df()
        df["date"] = pd.to_datetime(df["date"])
        series = df.set_index("date")["value"].rename(series_id)

        if start_date is not None:
            series = series[series.index >= start_date]
        if end_date is not None:
            series = series[series.index <= end_date]
        return series

    def load_category(
        self,
        category: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, pd.Series]:
        """
        Load every series in a given category as a dict of pandas Series.

        Convenience wrapper around `load_series` — saves the caller from
        looking up the series IDs in a category by hand.

        Parameters
        ----------
        category : str
            One of 'beveridge', 'stress_index', 'recession', 'education', 'enrollment'.
        start_date : str or None, optional
            Inclusive lower bound.
        end_date : str or None, optional
            Inclusive upper bound.

        Returns
        -------
        dict[str, pandas.Series]
            Keyed by series_id.
        """
        ids = self.available_series(category)["series_id"].tolist()
        return {sid: self.load_series(sid, start_date, end_date) for sid in ids}

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _validate_series_id(self, series_id: str) -> None:
        """
        Check that the series is known. Raises ValueError with valid IDs if not.
        """
        if not isinstance(series_id, str):
            raise TypeError(
                f"series_id must be a string, got {type(series_id).__name__}."
            )
        if series_id not in self._available_ids:
            raise ValueError(
                f"Series '{series_id}' not in database. "
                f"Available: {sorted(self._available_ids)}."
            )

    def _validate_dates(
        self,
        start_date: str | None,
        end_date: str | None,
    ) -> None:
        """
        Check that date arguments are well-formed and ordered.

        We accept None (no bound), or a string in 'YYYY-MM-DD' format. Anything
        else raises ValueError with a message explaining what's wrong.
        """
        parsed = {}
        for label, value in [("start_date", start_date), ("end_date", end_date)]:
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError(
                    f"{label} must be a string in 'YYYY-MM-DD' format, "
                    f"got {type(value).__name__}."
                )
            try:
                parsed[label] = datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise ValueError(
                    f"{label} '{value}' is not a valid date. "
                    "Expected format: YYYY-MM-DD."
                )

        if "start_date" in parsed and "end_date" in parsed:
            if parsed["start_date"] >= parsed["end_date"]:
                raise ValueError(
                    f"start_date ({start_date}) must be strictly before "
                    f"end_date ({end_date})."
                )

    def align_frequencies(
        self,
        series_dict: dict[str, pd.Series],
        freq: str = "MS",
        how: str = "inner",
    ) -> pd.DataFrame:
        """
        Combine several series into one DataFrame on a common frequency.

        FRED series come at different cadences (monthly, weekly, quarterly).
        To plot or compute anything joint we need a single shared index.
        This helper resamples each series to `freq` and joins them.

        Parameters
        ----------
        series_dict : dict[str, pandas.Series]
            Series to align, e.g. the output of `load_category`.
        freq : str, default 'MS'
            Target frequency. 'MS' = month start. Any pandas offset alias works.
        how : {'inner', 'outer'}, default 'inner'
            The join type passed to pandas when combining the series. 'inner'
            keeps only dates where every series has data (no gaps); 'outer'
            keeps the union of dates (preserves history but introduces NaNs).

        Returns
        -------
        pandas.DataFrame
            Columns named after the series IDs, indexed by date.

        Raises
        ------
        ValueError
            If `series_dict` is empty or `how` is not 'inner'/'outer'.
        """
        if not series_dict:
            raise ValueError("series_dict is empty — nothing to align.")
        if how not in ("inner", "outer"):
            raise ValueError(f"how must be 'inner' or 'outer', got '{how}'.")

        # Resample each series to the target frequency. We take the mean over
        # each period; for a rate or level the mean keeps the average value,
        # which is the natural summary (median, e.g., would also be feasible).

        resampled = {name: s.resample(freq).mean() for name, s in series_dict.items()}
        return pd.concat(resampled, axis=1, join=how)

    def tag_recession_episodes(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """
        Identify continuous recession periods from the USREC indicator.

        USREC is 1 during NBER recession months and 0 otherwise. This helper
        scans for 0→1 (recession starts) and 1→0 (recession ends) transitions
        and returns a list of (start, end) timestamp pairs.

        Parameters
        ----------
        start_date : str or None, optional
            Restrict the scan window.
        end_date : str or None, optional
            Restrict the scan window.

        Returns
        -------
        list[tuple[pandas.Timestamp, pandas.Timestamp]]
            One pair per recession episode in chronological order.
        """
        rec = self.load_series(self._RECESSION_SERIES_ID, start_date, end_date)

        episodes = []
        in_recession = False
        episode_start = None

        for d, v in rec.items():
            if v == 1 and not in_recession:
                episode_start = d
                in_recession = True
            elif v == 0 and in_recession:
                # The previous month was the last recession month.
                episodes.append((episode_start, d))
                in_recession = False

        # Close an open episode if the series ends mid-recession.
        if in_recession and episode_start is not None:
            episodes.append((episode_start, rec.index[-1]))

        return episodes

    def _shade_recessions(self, ax, start_date=None, end_date=None) -> None:
        """
        Draw grey vertical bands on `ax` for each recession episode.

        Used internally by every time-series plot so recession context is
        consistent across the project.

        Parameters
        ----------
        ax : matplotlib.axes.Axes
            The axis to shade.
        start_date, end_date : str or None, optional
            Passed through to `tag_recession_episodes` to limit the scan.
        """
        for episode_start, episode_end in self.tag_recession_episodes(
            start_date, end_date
        ):
            ax.axvspan(
                episode_start,
                episode_end,
                color="lightgrey",
                alpha=0.4,
                label="_nolegend_",  # keep recession bands out of the legend
            )

    @staticmethod
    def _zscore(series: pd.Series) -> pd.Series:
        """
        Standardise a series to zero mean and unit variance.

        The stress-index method relies on this: it lets series on very
        different scales (an unemployment rate near 5, a payroll count in the
        millions) be compared on the same footing.

        Parameters
        ----------
        series : pandas.Series

        Returns
        -------
        pandas.Series
            Same index, name preserved.

        Raises
        ------
        ValueError
            If the series has zero standard deviation (would divide by zero).
        """
        std = series.std()
        if std == 0:
            raise ValueError(
                f"Series '{series.name}' has zero standard deviation; cannot z-score."
            )
        return (series - series.mean()) / std
