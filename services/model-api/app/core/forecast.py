from __future__ import annotations

from typing import List

import pandas as pd

from ..schemas.transaction import Transaction


class SpendingForecaster:
    """Simple per-user monthly spending forecaster.

    Uses rolling average and a weighted-recent model with walk-forward
    validation to choose the best window & algorithm based on MAE.
    """

    def __init__(
        self, windows: List[int] | None = None, trend_threshold: float = 20.0
    ) -> None:
        self.windows: List[int] = windows or [2, 3, 4, 5]
        self.trend_threshold = float(trend_threshold)

    @staticmethod
    def _rolling_average_forecast(series: pd.Series, window: int = 2) -> float:
        return float(series.iloc[-window:].mean())

    @staticmethod
    def _weighted_recent_forecast(series: pd.Series, window: int = 2) -> float:
        recent = series.iloc[-window:].values
        weights = list(range(1, window + 1))
        total_weight = float(sum(weights))
        return float(sum(v * w for v, w in zip(recent, weights)) / total_weight)

    @staticmethod
    def _mean_absolute_error(actuals: list[float], preds: list[float]) -> float:
        if not actuals or len(actuals) != len(preds):
            raise ValueError(
                "Actual and predicted lists must be the same non-zero length."
            )

        total = 0.0
        for a, p in zip(actuals, preds):
            total += abs(float(a) - float(p))
        return total / float(len(actuals))

    def _find_best_window(self, series: pd.Series) -> tuple[int, str, float]:
        """Walk-forward validation - finds window + model with lowest MAE."""

        best_window, best_mae, best_model = 2, float("inf"), "RA"
        for w in self.windows:
            if w >= len(series):
                continue

            ra_preds: list[float] = []
            wt_preds: list[float] = []
            actuals: list[float] = []

            for t in range(w, len(series)):
                hist = series.iloc[:t]
                ra_preds.append(self._rolling_average_forecast(hist, w))
                wt_preds.append(self._weighted_recent_forecast(hist, w))
                actuals.append(float(series.iloc[t]))

            ra_mae = self._mean_absolute_error(actuals, ra_preds)
            wt_mae = self._mean_absolute_error(actuals, wt_preds)

            if ra_mae < best_mae:
                best_mae, best_window, best_model = ra_mae, w, "RA"
            if wt_mae < best_mae:
                best_mae, best_window, best_model = wt_mae, w, "WT"

        if best_mae == float("inf"):
            raise ValueError("Not enough data to evaluate any forecast window.")

        return best_window, best_model, best_mae

    def forecast(self, series: pd.Series) -> dict:
        """Run model selection + forecasting on a monthly spending series.

        The series index should be a datetime-like month (e.g. Timestamp),
        and the values represent total expenses for that month.
        """

        if len(series) < 2:
            raise ValueError("Need at least 2 months of data to forecast.")

        series = series.sort_index()

        best_window, best_model_name, best_mae = self._find_best_window(series)

        if best_model_name == "WT":
            next_pred = self._weighted_recent_forecast(series, best_window)
            algorithm = "Weighted Recent"
        else:
            next_pred = self._rolling_average_forecast(series, best_window)
            algorithm = "Rolling Average"

        last_month = series.index[-1]
        if not isinstance(last_month, pd.Timestamp):
            last_month = pd.to_datetime(last_month)
        next_month = last_month + pd.DateOffset(months=1)

        recent_slope = float(series.iloc[-1] - series.iloc[-best_window])
        threshold = self.trend_threshold
        if recent_slope > threshold:
            trend = "Upward"
        elif recent_slope < -threshold:
            trend = "Downward"
        else:
            trend = "Stable"

        return {
            "algorithm": algorithm,
            "best_window_months": best_window,
            "mae": f"{best_mae:.2f}",
            "trend": trend,
            "next_month": next_month.strftime("%Y-%m"),
            "next_month_forecast": f"{next_pred:.2f}",
        }


def build_monthly_expense_series(transactions: List[Transaction]) -> pd.Series:
    """Aggregate raw transactions into a monthly total-expenses series.

    - Groups by calendar month.
    - Ignores transactions whose type is "income".
    """

    if not transactions:
        raise ValueError("No transactions provided to build monthly series.")

    records: list[dict[str, object]] = []
    for tx in transactions:
        ttype = (tx.transaction_type or "").strip().lower()
        if ttype == "income":
            # Only focus on spending (expenses)
            continue
        records.append({"date": tx.date, "amount": float(tx.amount)})

    if not records:
        raise ValueError("No expense transactions found to build monthly series.")

    df = pd.DataFrame.from_records(records)
    df["month_dt"] = pd.to_datetime(df["date"]).dt.to_period("M").dt.to_timestamp()

    monthly = df.groupby("month_dt")["amount"].sum().sort_index()
    monthly.name = "total_expenses"
    return monthly
