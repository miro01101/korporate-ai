from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


MODEL_NAMES = (
    "naive",
    "seasonal_naive_12",
    "moving_average_3",
    "moving_average_6",
    "croston_sba",
    "tsb",
)


@dataclass(frozen=True)
class BacktestMetric:
    model_name: str
    mae: float
    wape: float
    bias: float
    sample_size: int


@dataclass(frozen=True)
class ModelSelection:
    selected_model: str
    metrics: tuple[BacktestMetric, ...]


def _clean_history(
    history: pd.Series | np.ndarray | list[float],
) -> np.ndarray:
    values = np.asarray(history, dtype=float)
    values = np.nan_to_num(
        values,
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )

    return np.clip(values, 0.0, None)


def naive_forecast(history: np.ndarray) -> float:
    if len(history) == 0:
        return 0.0

    return float(history[-1])


def seasonal_naive_forecast(
    history: np.ndarray,
    season_length: int = 12,
) -> float:
    if len(history) >= season_length:
        return float(history[-season_length])

    return naive_forecast(history)


def moving_average_forecast(
    history: np.ndarray,
    window: int,
) -> float:
    if len(history) == 0:
        return 0.0

    return float(np.mean(history[-window:]))


def croston_sba_forecast(
    history: np.ndarray,
    alpha: float = 0.10,
) -> float:
    values = _clean_history(history)
    nonzero_positions = np.flatnonzero(values > 0)

    if len(nonzero_positions) == 0:
        return 0.0

    first = int(nonzero_positions[0])
    demand = float(values[first])
    interval = float(first + 1)
    last_nonzero = first

    for position in nonzero_positions[1:]:
        observed_demand = float(values[position])
        observed_interval = float(
            position - last_nonzero
        )

        demand = (
            alpha * observed_demand
            + (1.0 - alpha) * demand
        )

        interval = (
            alpha * observed_interval
            + (1.0 - alpha) * interval
        )

        last_nonzero = int(position)

    if interval <= 0:
        return max(demand, 0.0)

    correction = 1.0 - (alpha / 2.0)

    return max(
        correction * demand / interval,
        0.0,
    )


def tsb_forecast(
    history: np.ndarray,
    alpha_demand: float = 0.10,
    alpha_probability: float = 0.10,
) -> float:
    values = _clean_history(history)
    nonzero_positions = np.flatnonzero(values > 0)

    if len(nonzero_positions) == 0:
        return 0.0

    demand = float(values[nonzero_positions[0]])
    probability = float(np.mean(values > 0))

    for value in values:
        occurred = 1.0 if value > 0 else 0.0

        probability = (
            alpha_probability * occurred
            + (1.0 - alpha_probability) * probability
        )

        if occurred:
            demand = (
                alpha_demand * float(value)
                + (1.0 - alpha_demand) * demand
            )

    return max(probability * demand, 0.0)


def forecast_candidate(
    model_name: str,
    history: np.ndarray,
) -> float:
    clean = _clean_history(history)

    if model_name == "naive":
        return naive_forecast(clean)

    if model_name == "seasonal_naive_12":
        return seasonal_naive_forecast(clean)

    if model_name == "moving_average_3":
        return moving_average_forecast(clean, 3)

    if model_name == "moving_average_6":
        return moving_average_forecast(clean, 6)

    if model_name == "croston_sba":
        return croston_sba_forecast(clean)

    if model_name == "tsb":
        return tsb_forecast(clean)

    raise ValueError(f"Unknown model: {model_name}")


def _metrics(
    actual: np.ndarray,
    predicted: np.ndarray,
    model_name: str,
) -> BacktestMetric:
    errors = predicted - actual

    mae = float(np.mean(np.abs(errors)))

    denominator = float(np.sum(np.abs(actual)))

    wape = (
        float(np.sum(np.abs(errors)) / denominator)
        if denominator > 0
        else mae
    )

    bias = (
        float(np.sum(errors) / denominator)
        if denominator > 0
        else float(np.mean(errors))
    )

    return BacktestMetric(
        model_name=model_name,
        mae=mae,
        wape=wape,
        bias=bias,
        sample_size=int(len(actual)),
    )


def rolling_backtest(
    history: pd.Series | np.ndarray | list[float],
    *,
    minimum_training_months: int = 24,
    maximum_test_months: int = 12,
) -> tuple[BacktestMetric, ...]:
    values = _clean_history(history)

    if len(values) <= minimum_training_months:
        raise ValueError(
            "Insufficient history for rolling backtest."
        )

    first_test_index = max(
        minimum_training_months,
        len(values) - maximum_test_months,
    )

    actual_values = values[first_test_index:]

    metrics: list[BacktestMetric] = []

    for model_name in MODEL_NAMES:
        predictions: list[float] = []

        for position in range(
            first_test_index,
            len(values),
        ):
            training_history = values[:position]

            predictions.append(
                forecast_candidate(
                    model_name,
                    training_history,
                )
            )

        metrics.append(
            _metrics(
                actual_values,
                np.asarray(predictions),
                model_name,
            )
        )

    return tuple(metrics)


def select_baseline_model(
    history: pd.Series | np.ndarray | list[float],
    *,
    minimum_training_months: int = 24,
    maximum_test_months: int = 12,
) -> ModelSelection:
    metrics = rolling_backtest(
        history,
        minimum_training_months=minimum_training_months,
        maximum_test_months=maximum_test_months,
    )

    selected = min(
        metrics,
        key=lambda metric: (
            metric.wape,
            abs(metric.bias),
            metric.mae,
            metric.model_name,
        ),
    )

    return ModelSelection(
        selected_model=selected.model_name,
        metrics=metrics,
    )


def forecast_quantiles_from_backtest(
    history: pd.Series | np.ndarray | list[float],
    selected_model: str,
) -> tuple[float, float, float]:
    values = _clean_history(history)

    point = forecast_candidate(
        selected_model,
        values,
    )

    if len(values) < 25:
        return (
            max(point * 0.75, 0.0),
            max(point, 0.0),
            max(point * 1.25, 0.0),
        )

    residuals: list[float] = []

    first_test_index = max(24, len(values) - 12)

    for position in range(
        first_test_index,
        len(values),
    ):
        predicted = forecast_candidate(
            selected_model,
            values[:position],
        )

        residuals.append(
            float(values[position] - predicted)
        )

    residual_array = np.asarray(residuals)

    p10 = max(
        point + float(np.quantile(residual_array, 0.10)),
        0.0,
    )

    p50 = max(point, p10)

    p90 = max(
        point + float(np.quantile(residual_array, 0.90)),
        p50,
    )

    return p10, p50, p90
