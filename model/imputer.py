"""Core orchestration for 3-tier missing-data recovery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

import pandas as pd

from .db_handler import DBHandler
from .ml_predictor import NodeAwareMLPredictor, PredictionError
from .topology_engine import TopologyEngine


@dataclass(frozen=True)
class Gap:
    """Represents one contiguous missing segment in an index."""

    start: pd.Timestamp
    end: pd.Timestamp
    intervals: int


class GridImputer:
    """3-tier imputation orchestrator.

    Tier 1: topological deduction (parent == sum(children))
    Tier 2: micro-gap interpolation (<= 3 intervals)
    Tier 3: node-aware ML prediction (larger gaps)
    """

    def __init__(
        self,
        topology: TopologyEngine,
        db_handler: Optional[DBHandler] = None,
        ml_predictor: Optional[NodeAwareMLPredictor] = None,
        interval_minutes: int = 15,
        primary_column: str = "Pt",
    ) -> None:
        self.topology = topology
        self.db = db_handler or DBHandler(interval_minutes=interval_minutes)
        self.ml = ml_predictor or NodeAwareMLPredictor(topology=topology)
        self.interval_minutes = interval_minutes
        self.primary_column = primary_column

    def _validate_df(self, df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("Input DataFrame index must be a DatetimeIndex.")
        if self.primary_column not in df.columns:
            raise ValueError(f"Input DataFrame must include '{self.primary_column}' column.")
        out = df.sort_index().copy()
        out[self.primary_column] = pd.to_numeric(out[self.primary_column], errors="coerce")
        return out

    def _find_gaps(self, series: pd.Series) -> list[Gap]:
        missing = series.isna()
        if not missing.any():
            return []
        groups = (missing != missing.shift(fill_value=False)).cumsum()
        gaps: list[Gap] = []
        for _, chunk in series.groupby(groups):
            if chunk.notna().all():
                continue
            gaps.append(Gap(start=chunk.index.min(), end=chunk.index.max(), intervals=len(chunk)))
        return gaps

    def _tier1_topological_deduction(
        self,
        node_id: int,
        frame: pd.DataFrame,
    ) -> pd.DataFrame:
        col = self.primary_column
        if not self.topology.has_node(node_id):
            return frame

        parent_id = self.topology.get_parent(node_id)
        if parent_id is None:
            return frame

        siblings = self.topology.get_siblings(node_id)
        if not siblings:
            return frame

        idx_start = frame.index.min().to_pydatetime()
        idx_end = frame.index.max().to_pydatetime()

        try:
            parent_df = self.db.fetch_timeseries(parent_id, idx_start, idx_end, metric="power")
            sibling_df = self.db.fetch_multi_meter_power(siblings, idx_start, idx_end)
        except Exception:
            return frame

        if col not in parent_df.columns:
            return frame

        combined = frame.copy()
        parent_series = parent_df[col].reindex(combined.index)
        sibling_sum = sibling_df.reindex(combined.index).sum(axis=1, min_count=len(siblings))
        deduced = parent_series - sibling_sum

        fill_mask = combined[col].isna() & deduced.notna()
        combined.loc[fill_mask, col] = deduced.loc[fill_mask]
        return combined

    def _tier2_micro_interpolation(self, frame: pd.DataFrame) -> pd.DataFrame:
        col = self.primary_column
        out = frame.copy()
        gaps = self._find_gaps(out[col])
        if not gaps:
            return out

        small_gap_index = []
        for gap in gaps:
            if 1 <= gap.intervals <= 3:
                small_gap_index.extend(pd.date_range(gap.start, gap.end, freq=f"{self.interval_minutes}min"))
        if not small_gap_index:
            return out

        work = out[col].copy()
        # Prefer smooth polynomial interpolation for short gaps; fallback to linear.
        try:
            interpolated = work.interpolate(method="polynomial", order=2, limit=3, limit_direction="both")
        except Exception:
            interpolated = work.interpolate(method="time", limit=3, limit_direction="both")

        mask = work.index.isin(pd.DatetimeIndex(small_gap_index)) & work.isna() & interpolated.notna()
        out.loc[mask, col] = interpolated.loc[mask]
        return out

    def _tier3_ml_prediction(
        self,
        node_id: int,
        frame: pd.DataFrame,
    ) -> pd.DataFrame:
        col = self.primary_column
        out = frame.copy()
        missing_idx = out.index[out[col].isna()]
        if missing_idx.empty:
            return out

        # Optional context: main grid is node 1 in relation.json
        idx_start = out.index.min().to_pydatetime()
        idx_end = out.index.max().to_pydatetime()
        main_grid_load: Optional[pd.Series] = None
        try:
            root_df = self.db.fetch_timeseries(1, idx_start, idx_end, metric="power")
            if col in root_df.columns:
                main_grid_load = root_df[col]
        except Exception:
            main_grid_load = None

        category = self.topology.category(node_id) if self.topology.has_node(node_id) else ""
        neighboring_pv: Optional[pd.DataFrame] = None
        if "pv" in category.lower() or "solar" in category.lower():
            pv_ids = [29, 31, 56]
            peers = [m for m in pv_ids if m != node_id]
            if peers:
                try:
                    neighboring_pv = self.db.fetch_multi_meter_power(peers, idx_start, idx_end)
                except Exception:
                    neighboring_pv = None

        try:
            model_loaded = self.ml.load_trained_model(node_id=node_id)
            if not model_loaded:
                self.ml.train(
                    node_id=node_id,
                    target_series=out[col],
                    main_grid_load=main_grid_load,
                    neighboring_pv=neighboring_pv,
                )
            preds = self.ml.predict(
                node_id=node_id,
                target_index=missing_idx,
                main_grid_load=main_grid_load,
                neighboring_pv=neighboring_pv,
            )
        except PredictionError:
            return out

        out.loc[missing_idx, col] = preds
        return out

    def fill_missing(
        self,
        df: pd.DataFrame,
        node_id: int,
        date_range: tuple[datetime, datetime] | None = None,
    ) -> pd.DataFrame:
        """Fill missing data for one node using Tier 1->Tier 2->Tier 3 strategy.

        Parameters
        ----------
        df:
            Input meter frame with DatetimeIndex and `Pt` (or configured primary column).
        node_id:
            Node/meter identifier found in relation topology.
        date_range:
            Optional tuple of (start, end); if provided, output is clipped/reindexed to range.
        """
        work = self._validate_df(df)
        if date_range is not None:
            start, end = date_range
            target_index = pd.date_range(start=start, end=end, freq=f"{self.interval_minutes}min")
            work = work.reindex(target_index)

        # Tier 1: physics-aware deduction.
        work = self._tier1_topological_deduction(node_id=node_id, frame=work)
        # Tier 2: small-gap interpolation.
        work = self._tier2_micro_interpolation(frame=work)
        # Tier 3: node-aware ML for remaining larger gaps.
        work = self._tier3_ml_prediction(node_id=node_id, frame=work)

        return work
