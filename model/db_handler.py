"""Database access utilities for power grid telemetry.

This module reads from daily sharded MySQL tables and returns normalized
Pandas time-series data indexed at a fixed interval (default 15 minutes).
All meter values are scaled by the hardware ratio from `meter_info`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

import mysql.connector
import pandas as pd
from mysql.connector.connection import MySQLConnection

from db.mysql_env import TABLE_PREFIX, mysql_connect_kwargs


class DatabaseConnectionError(RuntimeError):
    """Raised when a database connection cannot be established."""


@dataclass(frozen=True)
class DateRange:
    """Inclusive datetime range."""

    start: datetime
    end: datetime

    def iter_days(self) -> Iterable[pd.Timestamp]:
        """Yield all calendar days in the range."""
        start_day = pd.Timestamp(self.start).normalize()
        end_day = pd.Timestamp(self.end).normalize()
        current = start_day
        while current <= end_day:
            yield current
            current += pd.Timedelta(days=1)


class DBHandler:
    """Read sharded power/energy telemetry from MySQL.

    Parameters
    ----------
    interval_minutes:
        Expected telemetry interval in minutes. Default is 15.
    """

    def __init__(self, interval_minutes: int = 15) -> None:
        self.interval_minutes = interval_minutes
        self._conn: Optional[MySQLConnection] = None

    def connect(self) -> MySQLConnection:
        """Connect to MySQL using environment-driven configuration."""
        if self._conn is not None and self._conn.is_connected():
            return self._conn
        try:
            self._conn = mysql.connector.connect(**mysql_connect_kwargs())
            return self._conn
        except mysql.connector.Error as exc:
            raise DatabaseConnectionError(
                "Failed to connect to MySQL. Check MYSQL_* env values and network access."
            ) from exc

    def close(self) -> None:
        """Close the active DB connection."""
        if self._conn is not None and self._conn.is_connected():
            self._conn.close()
        self._conn = None

    def _table_name(self, base: str, day: pd.Timestamp) -> str:
        return f"{base}_{day.strftime('%y%m%d')}"

    def _table_exists(self, table_name: str) -> bool:
        conn = self.connect()
        query = """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(query, (table_name,))
            count = cursor.fetchone()[0]
        return bool(count)

    def _table_columns(self, table_name: str) -> set[str]:
        conn = self.connect()
        query = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
        """
        with conn.cursor() as cursor:
            cursor.execute(query, (table_name,))
            rows = cursor.fetchall()
        return {str(row[0]) for row in rows}

    def _fetch_ratio(self, meter_number: int) -> float:
        conn = self.connect()
        query = """
            SELECT COALESCE(ratio, 1)
            FROM meter_info
            WHERE meter_number = %s
            LIMIT 1
        """
        with conn.cursor() as cursor:
            cursor.execute(query, (meter_number,))
            row = cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else 1.0

    def _load_daily_slice(
        self,
        table_name: str,
        meter_number: int,
        start_ts: datetime,
        end_ts: datetime,
        value_columns: Sequence[str],
    ) -> pd.DataFrame:
        conn = self.connect()
        available_columns = self._table_columns(table_name)
        selected_value_columns = [col for col in value_columns if col in available_columns]
        selected_columns = ", ".join(["ts", "meter_number", *selected_value_columns])
        query = f"""
            SELECT {selected_columns}
            FROM {table_name}
            WHERE meter_number = %s
              AND ts >= %s
              AND ts <= %s
            ORDER BY ts ASC
        """
        params = (meter_number, start_ts, end_ts)
        out = pd.read_sql(query, conn, params=params, parse_dates=["ts"])
        for col in value_columns:
            if col not in out.columns:
                out[col] = pd.NA
        return out

    @staticmethod
    def extract_power_series(df: pd.DataFrame) -> Optional[pd.Series]:
        """Extract a normalized power series from a DataFrame with varying column names."""
        if df.empty:
            return None
        canonical = {str(col).strip().lower(): str(col) for col in df.columns}
        for key in ("pt", "p_total", "total_power", "power", "kw"):
            if key in canonical:
                col = canonical[key]
                return pd.to_numeric(df[col], errors="coerce")
        return None

    def fetch_timeseries(
        self,
        meter_number: int,
        start_ts: datetime,
        end_ts: datetime,
        metric: str = "power",
    ) -> pd.DataFrame:
        """Fetch and normalize a meter's time series over a datetime range.

        Parameters
        ----------
        meter_number:
            Meter ID in telemetry tables and `meter_info`.
        start_ts / end_ts:
            Datetime boundaries (inclusive).
        metric:
            Either `power` or `energy`.
            - power columns: Pt, Ia, Ib, Ic
            - energy columns: ImpEP, ExpEP, TotalEP

        Returns
        -------
        pandas.DataFrame
            Continuous DatetimeIndex at `interval_minutes` with missing points as NaN.
        """
        metric = metric.lower().strip()
        if metric not in {"power", "energy"}:
            raise ValueError("`metric` must be either 'power' or 'energy'.")

        table_prefix = f"{TABLE_PREFIX}_power" if metric == "power" else f"{TABLE_PREFIX}_energy"
        value_columns = (
            ["Pt", "Ia", "Ib", "Ic"] if metric == "power" else ["ImpEP", "ExpEP", "TotalEP"]
        )

        date_range = DateRange(start=start_ts, end=end_ts)
        all_frames: list[pd.DataFrame] = []

        for day in date_range.iter_days():
            table_name = self._table_name(table_prefix, day)
            if not self._table_exists(table_name):
                continue
            day_start = max(pd.Timestamp(start_ts), day)
            day_end = min(pd.Timestamp(end_ts), day + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
            df_day = self._load_daily_slice(
                table_name=table_name,
                meter_number=meter_number,
                start_ts=day_start.to_pydatetime(),
                end_ts=day_end.to_pydatetime(),
                value_columns=value_columns,
            )
            if not df_day.empty:
                all_frames.append(df_day)

        if not all_frames:
            idx = pd.date_range(
                start=pd.Timestamp(start_ts),
                end=pd.Timestamp(end_ts),
                freq=f"{self.interval_minutes}min",
            )
            return pd.DataFrame(index=idx, columns=value_columns, dtype=float)

        df = pd.concat(all_frames, ignore_index=True)
        df = df.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")

        ratio = self._fetch_ratio(meter_number)
        for col in value_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") * ratio

        df = df.set_index("ts")[value_columns]
        full_index = pd.date_range(
            start=pd.Timestamp(start_ts),
            end=pd.Timestamp(end_ts),
            freq=f"{self.interval_minutes}min",
        )
        df = df.reindex(full_index)
        df.index.name = "ts"
        return df

    def fetch_multi_meter_power(
        self,
        meter_numbers: Sequence[int],
        start_ts: datetime,
        end_ts: datetime,
    ) -> pd.DataFrame:
        """Fetch Pt series for multiple meters as aligned wide table.

        Returns columns named by meter number as strings.
        """
        frames: list[pd.DataFrame] = []
        for meter in meter_numbers:
            meter_df = self.fetch_timeseries(
                meter_number=meter,
                start_ts=start_ts,
                end_ts=end_ts,
                metric="power",
            )
            power_series = self.extract_power_series(meter_df)
            if power_series is None:
                continue
            col_name = str(meter)
            frames.append(power_series.rename(col_name).to_frame())
        if not frames:
            idx = pd.date_range(start=start_ts, end=end_ts, freq=f"{self.interval_minutes}min")
            return pd.DataFrame(index=idx)
        return pd.concat(frames, axis=1).sort_index()
