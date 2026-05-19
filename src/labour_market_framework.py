"""
LabourMarketFramework — a reusable toolkit for labour-market analysis built with chosen variables.

This module defines the class. It does not contain any analysis script; the
notebooks instantiate it and call its methods.

The class is layered:
    1. Data access   — read series and metadata from the DuckDB database.
    2. Helpers       — date validation, frequency alignment, recession tagging.
    3. Output methods — figures, tables, summary metrics.
"""

from datetime import datetime
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import pandas as pd


class LabourMarketFramework:
    """
    Purpose
    ----------
    Toolkit for analysing US labour-market conditions through chosen variables.

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
        self._conn = duckdb.connect(str(self.db_path), read_only=True)

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
        Purpose
        ----------
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

        Raises
        ------
        ValueError
            If the category is not found in the database.
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
         Purpose
        ----------
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
        TypeError
            If series_id or dates are not strings.
        ValueError
            If series_id is not in the database, or if dates are invalid (wrong format or start_date >= end_date).
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
         Purpose
        ----------
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
        else raises a TypeError or ValueError with a message explaining what's wrong.
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
         Purpose
        ----------
        Combine several series into one DataFrame on a common frequency.

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
         Purpose
        ----------
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
         Purpose
        ----------
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
         Purpose
        ----------
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

    # ─────────────────────────────────────────────────────────────────────────
    # OUTPUT METHODS
    # ─────────────────────────────────────────────────────────────────────────

    def plot_beveridge_curve(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        annotate_recessions: bool = True,
        cmap: str = "plasma",
    ):
        """
        Plot the Beveridge curve. Job openings rate (JTSJOR) vs unemployment rate (UNRATE).

        The Beveridge curve is a measure for the inverse relationship between the job vacancy
        and the unemployment rate. The downward sloping curve captures economic cycles in the labor market.

        Parameters:
        start_date : str or None, optional
            Inclusive lower bound in 'YYYY-MM-DD' format. JOLTS data starts
            December 2000. earlier data is clipped.
        end_date : str or None, optional
            Inclusive upper bound in 'YYYY-MM-DD' format.
        annotate_recessions : bool, default True
            If True draws red rings around recession months.
        cmap : str, default 'plasma'
            Any matplotlib colormap name. Points are coloured by year.

        Returns:
        matplotlib.figure.Figure

        Raises:
        ValueError
            If dates are invalid (see `_validate_dates`).
            If the requested data is outside the data's time period.
        TypeError
            If dates aren't strings.
        """
        self._validate_dates(start_date, end_date)

        openings = self.load_series("JTSJOR", start_date, end_date)
        unrate = self.load_series("UNRATE", start_date, end_date)

        # inner join keeps only months with data in both series
        df = pd.concat([openings, unrate], axis=1, join="inner")
        df.columns = ["openings", "unemployment"]

        # raise an Error if the figure is empty
        if df.empty:
            raise ValueError(
                f"No overlapping JTSJOR/UNRATE data in the requested window "
                f"(start_date={start_date}, end_date={end_date}). "
                "JOLTS data runs from December 2000 to the present; "
                "choose a range inside that period."
            )

        fig, ax = plt.subplots(figsize=(9, 7))
        scatter = ax.scatter(
            df["unemployment"],
            df["openings"],
            c=df.index.year,
            cmap=cmap,
            alpha=0.7,
            s=25,
        )
        plt.colorbar(scatter, ax=ax, label="Year")

        if annotate_recessions:
            rec = self.load_series("USREC", start_date, end_date)
            rec_aligned = rec.reindex(df.index, fill_value=0)
            recession_points = df[rec_aligned == 1]
            if not recession_points.empty:
                ax.scatter(
                    recession_points["unemployment"],
                    recession_points["openings"],
                    edgecolors="red",
                    facecolors="none",
                    s=50,
                    linewidths=0.9,
                    label="Recession months",
                )
                ax.legend(fontsize=9)

        ax.set_xlabel("Unemployment Rate (%)")
        ax.set_ylabel("Job Openings Rate (%)")
        ax.set_title("Beveridge Curve, US 2000-present")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        return fig

    def descriptive_stats(
        self,
        series_ids: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        include_correlations: bool = False,
    ):
        """
        Summary statistics: count, mean, std, min, max.



        The default summarizes every series in the database. series_ids is used
        to limit the summary.
        By setting include_correlations=True a second table with the correlations is produced.


        Parameters:
        series_ids : list[str] or None, optional
            FRED series IDs to summarize. Defaults to every series in the database.
        start_date : str or None, optional
            Inclusive lower bound 'YYYY-MM-DD'.
        end_date : str or None, optional
            Inclusive upper bound 'YYYY-MM-DD'.
        include_correlations : bool, default False
            Set True to return correlation matrix. But only same-frequency series for corr
            to avoid misleading output by mixing incomparable frequencies. Stats table will have
            9 rows and corr only 8 because Master Enrollment data is only annually
            available.



        Returns:
        pandas.DataFrame
            Indexed by series_id, columns: count, mean, std, min, max. We round to 2dp.
        tuple[pandas.DataFrame, pandas.DataFrame], optional
            Set include_correlations=True to return this.

        Raises:
        ValueError
            If `series_ids` contains an unknown ID, or dates are invalid.
        TypeError
            If dates or series aren't strings.
        """
        self._validate_dates(start_date, end_date)

        if series_ids is None:
            series_ids = sorted(self._available_ids)
        for sid in series_ids:
            self._validate_series_id(sid)

        series_dict = {
            sid: self.load_series(sid, start_date, end_date) for sid in series_ids
        }

        stats = pd.DataFrame(
            {
                sid: {
                    "count": int(s.count()),
                    "mean": s.mean(),
                    "std": s.std(),
                    "min": s.min(),
                    "max": s.max(),
                }
                for sid, s in series_dict.items()
            }
        ).T.round(2)

        if include_correlations:
            # Only draw correlation on monthly series to avoid misleading
            # output from mixed frequencies (Master's enrollment only annually available)
            monthly_ids = (
                self._conn.execute(
                    "SELECT series_id FROM series WHERE frequency = 'M' "
                )
                .df()["series_id"]
                .tolist()
            )
            monthly_dict = {
                sid: s for sid, s in series_dict.items() if sid in monthly_ids
            }
            df_combined = self.align_frequencies(monthly_dict, how="inner")
            corr = df_combined.corr().round(2)
            return stats, corr
        return stats

    def compute_stress_index(
        self,
        series_ids: list[str] | None = None,
        weights: dict[str, float] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        freq: str = "MS",
    ) -> pd.Series:
        """
        Purpose
        ----------
        Aggregate labour-market stress index built from several FRED series.

        Each input series is z-scored (zero mean, unit variance) so its scale
        does not dominate. The z-scored series are then combined linearly using
        `weights`. Our default sign convention is: series where higher values
        mean a worse market get a positive weight; series where higher values mean
        a better market get a negative weight. The output is therefore always
        "higher = worse labour-market conditions".

        Parameters
        ----------
        series_ids : list[str] or None, optional
            Series to combine. Defaults to every series with
            `category == 'stress_index'` in the database.
        weights : dict[str, float] or None, optional
            `{series_id: weight}`. Must have exactly one entry per `series_id`.
            Defaults to ±1 based on the series' role (we use heuristic equal weighting —
            no series is considered more important than another).
        start_date : str or None, optional
            Inclusive lower bound 'YYYY-MM-DD'.
        end_date : str or None, optional
            Inclusive upper bound 'YYYY-MM-DD'.
        freq : str, default 'MS'
            Target frequency for `align_frequencies`. Any pandas offset alias works.

        Returns
        -------
        pandas.Series
            Monthly stress index named 'stress_index'.

        Raises
        ------
        ValueError
            If `weights` keys do not match `series_ids`, if the sum of absolute
            weights is zero, or if dates are invalid.
        """
        self._validate_dates(start_date, end_date)

        if series_ids is None:
            series_ids = self.available_series("stress_index")["series_id"].tolist()
        for sid in series_ids:
            self._validate_series_id(sid)

        # Default sign convention:
        _DEFAULT_SIGNS = {
            "UNRATE": +1,  # unemployment rate: higher is worse
            "LNS12032194": +1,  # involuntary part-time work: higher is worse
            "LNS14027662": +1,  # unemployment, bachelor's degree+: higher is worse
            "JTSJOR": -1,  # job openings rate: higher is better
            "JTSHIL": -1,  # hires rate: higher is better
            "JTSQUR": -1,  # quits rate (worker confidence): higher is better
            "PAYEMS": -1,  # nonfarm payrolls: higher is better
        }
        if weights is None:
            weights = {sid: _DEFAULT_SIGNS.get(sid, 1.0) for sid in series_ids}

        if set(weights.keys()) != set(series_ids):
            raise ValueError(
                f"weights keys {sorted(weights.keys())} must match series_ids "
                f"{sorted(series_ids)} exactly."
            )
        total_abs_weight = sum(abs(w) for w in weights.values())
        if total_abs_weight == 0:
            raise ValueError("Sum of absolute weights is zero, index is undefined.")

        series_dict = {
            sid: self.load_series(sid, start_date, end_date) for sid in series_ids
        }
        df = self.align_frequencies(series_dict, freq=freq, how="inner")

        # z-score each column, add the weights and combine them into the index.
        z = df.apply(self._zscore)
        weighted_sum = sum(z[sid] * weights[sid] for sid in series_ids)
        return (weighted_sum / total_abs_weight).rename("stress_index")
