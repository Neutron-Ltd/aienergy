"""Node-aware machine learning predictor for large telemetry gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import pickle
from pathlib import Path
from typing import Dict, Optional, Sequence

import numpy as np
import pandas as pd

from .db_handler import DBHandler
from .topology_engine import TopologyEngine


class PredictionError(RuntimeError):
    """Raised when model training/prediction fails."""


@dataclass
class TrainedModel:
    """Container for a trained node model and metadata."""

    model: object
    feature_columns: list[str]
    strategy: str


class NodeAwareMLPredictor:
    """Train and infer node-specific models for long-gap imputation.

    The predictor uses category-aware features:
    - PV nodes: daylight gating + neighboring PV context.
    - Storage nodes: hour/day schedule features.
    - Load nodes: hour/day + optional main-grid context.
    """

    def __init__(self, topology: TopologyEngine, meta_path: str | Path | None = None) -> None:
        self.topology = topology
        self._models: Dict[int, TrainedModel] = {}
        self.artifacts_dir = Path(os.environ.get("MODEL_ARTIFACT_DIR", "/app/model_artifacts")).resolve()
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.meta_path = Path(meta_path) if meta_path else self.artifacts_dir / "model_meta.json"

    def _model_file_path(self, node_id: int) -> Path:
        return self.artifacts_dir / f"node_{node_id}.joblib"

    def _model_sidecar_path(self, node_id: int) -> Path:
        return self.artifacts_dir / f"node_{node_id}.meta.json"

    def _persist_model(self, node_id: int, trained: TrainedModel) -> None:
        model_path = self._model_file_path(node_id)
        sidecar_path = self._model_sidecar_path(node_id)
        with model_path.open("wb") as f:
            # We use pickle with .joblib extension to keep host artifact naming stable.
            pickle.dump(trained.model, f)
        with sidecar_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "node_id": node_id,
                    "strategy": trained.strategy,
                    "feature_columns": trained.feature_columns,
                    "saved_at": datetime.utcnow().isoformat() + "Z",
                },
                f,
                ensure_ascii=True,
                indent=2,
            )
        print(f"[NodeAwareMLPredictor] Saved model to: {model_path}")
        print(f"[NodeAwareMLPredictor] Saved model sidecar to: {sidecar_path}")
        print(f"[NodeAwareMLPredictor] Artifacts directory: {self.artifacts_dir}")

    def load_trained_model(self, node_id: int) -> bool:
        """Load a persisted model for a node from model_artifacts directory."""
        model_path = self._model_file_path(node_id)
        sidecar_path = self._model_sidecar_path(node_id)
        if not model_path.exists() or not sidecar_path.exists():
            return False
        with model_path.open("rb") as f:
            model = pickle.load(f)
        with sidecar_path.open("r", encoding="utf-8") as f:
            sidecar = json.load(f)
        trained = TrainedModel(
            model=model,
            feature_columns=list(sidecar.get("feature_columns", [])),
            strategy=str(sidecar.get("strategy", "historical_profile")),
        )
        self._models[node_id] = trained
        print(f"[NodeAwareMLPredictor] Loaded model from: {model_path}")
        return True

    def _is_pv(self, category: str) -> bool:
        return "pv" in category.lower() or "solar" in category.lower()

    def _is_storage(self, category: str) -> bool:
        return "storage" in category.lower()

    def _build_time_features(self, index: pd.DatetimeIndex) -> pd.DataFrame:
        df = pd.DataFrame(index=index)
        df["hour"] = index.hour
        df["minute"] = index.minute
        df["day_of_week"] = index.dayofweek
        df["month"] = index.month
        df["is_weekend"] = (index.dayofweek >= 5).astype(int)
        # Cyclical encodings.
        df["sin_hour"] = np.sin(2 * np.pi * (index.hour + index.minute / 60.0) / 24.0)
        df["cos_hour"] = np.cos(2 * np.pi * (index.hour + index.minute / 60.0) / 24.0)
        return df

    def _build_features(
        self,
        node_id: int,
        index: pd.DatetimeIndex,
        main_grid_load: Optional[pd.Series] = None,
        neighboring_pv: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        category = self.topology.category(node_id)
        feats = self._build_time_features(index)

        if self._is_pv(category):
            # Approximate daylight gate (can be swapped with sunrise/sunset APIs).
            hour_float = index.hour + index.minute / 60.0
            feats["is_daylight"] = ((hour_float >= 6) & (hour_float <= 18)).astype(int)
            if neighboring_pv is not None and not neighboring_pv.empty:
                feats["neighbor_pv_mean"] = neighboring_pv.reindex(index).mean(axis=1).fillna(0.0)

        if self._is_storage(category):
            # Storage often follows schedule-like charge/discharge cycles.
            feats["is_night"] = ((index.hour < 6) | (index.hour >= 22)).astype(int)
            feats["is_peak_window"] = (((index.hour >= 9) & (index.hour <= 12)) | ((index.hour >= 18) & (index.hour <= 21))).astype(int)

        if main_grid_load is not None:
            # Use ffill() for compatibility across pandas versions.
            feats["main_grid_load"] = main_grid_load.reindex(index).ffill().fillna(0.0)

        return feats

    def _build_model(self) -> tuple[object, str]:
        # Priority: XGBoost, then sklearn RandomForest, else profile fallback.
        try:
            from xgboost import XGBRegressor  # type: ignore

            model = XGBRegressor(
                n_estimators=250,
                max_depth=6,
                learning_rate=0.05,
                subsample=0.85,
                colsample_bytree=0.85,
                objective="reg:squarederror",
                random_state=42,
            )
            return model, "xgboost"
        except Exception:
            pass

        try:
            from sklearn.ensemble import RandomForestRegressor  # type: ignore

            model = RandomForestRegressor(
                n_estimators=300,
                max_depth=18,
                random_state=42,
                n_jobs=-1,
            )
            return model, "random_forest"
        except Exception:
            pass

        # Deterministic profile fallback.
        return object(), "historical_profile"

    def train(
        self,
        node_id: int,
        target_series: pd.Series,
        main_grid_load: Optional[pd.Series] = None,
        neighboring_pv: Optional[pd.DataFrame] = None,
        save_metrics: bool = True,
    ) -> TrainedModel:
        """Train or refresh a model for one node."""
        if target_series.empty:
            raise PredictionError("Training series is empty.")

        y = target_series.sort_index().astype(float)
        X = self._build_features(
            node_id=node_id,
            index=y.index,
            main_grid_load=main_grid_load,
            neighboring_pv=neighboring_pv,
        )

        valid_mask = y.notna()
        if valid_mask.sum() < 50:
            raise PredictionError(
                f"Insufficient valid samples to train node {node_id}. Need at least 50."
            )
        X_train = X.loc[valid_mask]
        y_train = y.loc[valid_mask]

        model, strategy = self._build_model()
        if strategy == "historical_profile":
            profile = (
                y_train.groupby([y_train.index.dayofweek, y_train.index.hour, y_train.index.minute])
                .mean()
                .rename("expected")
            )
            trained = TrainedModel(model=profile, feature_columns=[], strategy=strategy)
            self._models[node_id] = trained
            self._persist_model(node_id=node_id, trained=trained)
            if save_metrics:
                self._save_training_metrics(
                    node_id=node_id,
                    model_type=strategy,
                    y_true=y_train,
                    y_pred=self.predict(node_id=node_id, target_index=y_train.index),
                )
            return trained

        try:
            model.fit(X_train, y_train)
        except Exception as exc:
            raise PredictionError(f"Model training failed for node {node_id}: {exc}") from exc

        trained = TrainedModel(
            model=model,
            feature_columns=list(X.columns),
            strategy=strategy,
        )
        self._models[node_id] = trained
        self._persist_model(node_id=node_id, trained=trained)
        if save_metrics:
            try:
                y_pred_train = pd.Series(model.predict(X_train), index=y_train.index, dtype=float)
            except Exception:
                y_pred_train = pd.Series(index=y_train.index, data=np.nan, dtype=float)
            self._save_training_metrics(
                node_id=node_id,
                model_type=strategy,
                y_true=y_train,
                y_pred=y_pred_train,
            )
        return trained

    def _save_training_metrics(
        self,
        node_id: int,
        model_type: str,
        y_true: pd.Series,
        y_pred: pd.Series,
    ) -> None:
        aligned = pd.concat([y_true.rename("y_true"), y_pred.rename("y_pred")], axis=1).dropna()
        if aligned.empty:
            rmse = None
            r2 = None
            accuracy = None
        else:
            err = aligned["y_true"] - aligned["y_pred"]
            mse = float((err ** 2).mean())
            rmse = float(np.sqrt(mse))
            var = float(aligned["y_true"].var())
            r2 = float(1.0 - (mse / var)) if var > 0 else None
            # User-facing "accuracy %" from R^2 mapping, clipped to [0, 100].
            accuracy = round(float(max(0.0, min(1.0, r2 if r2 is not None else 0.0))) * 100.0, 2)

        payload = {"runs": []}
        if self.meta_path.exists():
            try:
                with self.meta_path.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = {"runs": []}

        runs = payload.get("runs", [])
        if not isinstance(runs, list):
            runs = []

        runs.append({
            "node_id": node_id,
            "model_type": model_type,
            "r2": r2,
            "rmse": rmse,
            "accuracy_pct": accuracy,
            "last_trained_timestamp": datetime.utcnow().isoformat() + "Z",
        })

        payload["runs"] = runs[-500:]
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        with self.meta_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)
        print(f"[NodeAwareMLPredictor] Saved metrics to: {self.meta_path.resolve()}")

    def predict(
        self,
        node_id: int,
        target_index: pd.DatetimeIndex,
        main_grid_load: Optional[pd.Series] = None,
        neighboring_pv: Optional[pd.DataFrame] = None,
    ) -> pd.Series:
        """Predict node load/generation on the provided timestamps."""
        if node_id not in self._models:
            raise PredictionError(
                f"No trained model found for node {node_id}. Call train() first."
            )

        trained = self._models[node_id]
        X = self._build_features(
            node_id=node_id,
            index=target_index,
            main_grid_load=main_grid_load,
            neighboring_pv=neighboring_pv,
        )

        if trained.strategy == "historical_profile":
            profile = trained.model  # pandas.Series
            keys = list(
                zip(
                    target_index.dayofweek.tolist(),
                    target_index.hour.tolist(),
                    target_index.minute.tolist(),
                )
            )
            pred = pd.Series([profile.get(k, np.nan) for k in keys], index=target_index, dtype=float)
            if pred.isna().any():
                pred = pred.fillna(float(profile.mean()) if len(profile) else 0.0)
            return pred

        try:
            preds = trained.model.predict(X[trained.feature_columns])
        except Exception as exc:
            raise PredictionError(f"Prediction failed for node {node_id}: {exc}") from exc

        series = pd.Series(preds, index=target_index, dtype=float)
        category = self.topology.category(node_id)
        if self._is_pv(category):
            # Enforce PV hard rule: no generation at night.
            hour_float = target_index.hour + target_index.minute / 60.0
            night_mask = (hour_float < 6) | (hour_float > 18)
            series.loc[night_mask] = 0.0
            series = series.clip(lower=0.0)
        return series

    def train_many(
        self,
        node_ids: Sequence[int],
        series_map: Dict[int, pd.Series],
        main_grid_load: Optional[pd.Series] = None,
        neighboring_pv_map: Optional[Dict[int, pd.DataFrame]] = None,
    ) -> Dict[int, TrainedModel]:
        """Train models for multiple nodes."""
        trained: Dict[int, TrainedModel] = {}
        for node_id in node_ids:
            if node_id not in series_map:
                continue
            trained[node_id] = self.train(
                node_id=node_id,
                target_series=series_map[node_id],
                main_grid_load=main_grid_load,
                neighboring_pv=(neighboring_pv_map or {}).get(node_id),
            )
        return trained

    @staticmethod
    def build_yymmdd_date_list(start_date: str, end_date: str) -> list[str]:
        """Build inclusive YYMMDD list for sharded-table date ranges."""
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        if end_dt < start_dt:
            raise ValueError("end_date must be >= start_date")
        out: list[str] = []
        current = pd.Timestamp(start_dt).normalize()
        end = pd.Timestamp(end_dt).normalize()
        while current <= end:
            out.append(current.strftime("%y%m%d"))
            current += pd.Timedelta(days=1)
        return out

    def train_models(
        self,
        node_ids: Sequence[int],
        start_date: str,
        end_date: str,
        db_handler: Optional[DBHandler] = None,
    ) -> Dict[int, TrainedModel]:
        """Entry-point training pipeline with explicit user-provided date range.

        Parameters
        ----------
        node_ids:
            Node IDs to train.
        start_date / end_date:
            Inclusive date range in YYYY-MM-DD format.
        db_handler:
            Optional injected DB handler.
        """
        _ = self.build_yymmdd_date_list(start_date=start_date, end_date=end_date)
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")

        db = db_handler or DBHandler(interval_minutes=15)
        root_df = db.fetch_timeseries(1, start_dt, end_dt, metric="power")
        main_grid_load = root_df["Pt"] if "Pt" in root_df.columns else None

        trained: Dict[int, TrainedModel] = {}
        for node_id in node_ids:
            meter_df = db.fetch_timeseries(node_id, start_dt, end_dt, metric="power")
            if "Pt" not in meter_df.columns:
                continue
            trained[node_id] = self.train(
                node_id=node_id,
                target_series=meter_df["Pt"],
                main_grid_load=main_grid_load,
                neighboring_pv=None,
                save_metrics=True,
            )
        return trained
