from __future__ import annotations

from contextlib import asynccontextmanager
from typing import List
import logging

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from .core.api_utils import (
    build_classification_response,
    build_monthly_summary,
    parse_transactions,
)
from .core.forecast import SpendingForecaster, build_monthly_expense_series
from .core.insights import BudgetInsightEngine
from .core.model import classify_transactions, load_model


logger = logging.getLogger("model_api.api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("model_api.log"), logging.StreamHandler()],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    load_model()

    yield

    # Shutdown (optional)
    # cleanup here if needed


app = FastAPI(
    title="Expense Category Prediction API",
    description=(
        "Predict expense categories from transaction descriptions. "
        "Supports both JSON input and CSV file uploads."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log unexpected errors during request handling.

    Does not log per-request start/end, only errors that bubble up
    past route handlers.
    """

    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request.error",
            extra={"method": request.method, "path": str(request.url.path)},
        )
        raise

    return response


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint that verifies the model loads."""

    try:
        load_model()
    except Exception:  # noqa: BLE001
        logger.exception("health_check_failed")
        return {"status": "error"}
    return {"status": "ok"}


@app.exception_handler(HTTPException)
async def http_exception_logger(request: Request, exc: HTTPException) -> JSONResponse:
    """Log HTTP exceptions with path and details before returning them."""

    logger.warning(
        "http_error",
        extra={
            "method": request.method,
            "path": str(request.url.path),
            "status_code": exc.status_code,
            "detail": exc.detail,
        },
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_logger(request: Request, exc: Exception) -> JSONResponse:  # noqa: BLE001
    """Catch-all handler that logs unexpected errors with stack trace."""

    logger.exception(
        "unhandled_error",
        extra={"method": request.method, "path": str(request.url.path)},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.post("/classify")
async def classify(
    request: Request, file: List[UploadFile] | None = File(None)
) -> dict:
    """Classify transactions and return both row-level and monthly summaries.

    Accepts JSON or CSV payloads and returns:
    - ``classification`` – per-transaction classification result (single object
      or list with ``total_rows``/``rows``).
    - ``monthly_summary`` – aggregated income, expenses, net balance, and
      per-category spending by month.
    """

    transactions, is_single = await parse_transactions(request, file)
    categories = classify_transactions(transactions)
    classification = build_classification_response(transactions, categories, is_single)
    monthly = build_monthly_summary(transactions, categories)

    logger.info(
        "classify.success",
        extra={
            "path": str(request.url.path),
            "transactions": len(transactions),
            "is_single": is_single,
            "months": len(monthly.get("months", [])),
        },
    )

    return {
        "classification": classification,
        "monthly_summary": monthly,
    }


@app.post("/forecast")
async def forecast_spending(
    request: Request, file: List[UploadFile] | None = File(None)
) -> dict:
    """Forecast next month's total expenses for a single user.

    Accepts the same CSV/JSON payload as /classify and aggregates all
    non-income transactions into a monthly spending time series, then
    selects the best simple model (rolling average or weighted recent)
    via walk-forward validation and returns the forecast and trend.
    """

    transactions, _ = await parse_transactions(request, file)

    try:
        series = build_monthly_expense_series(transactions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    forecaster = SpendingForecaster()
    try:
        result = forecaster.forecast(series)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "forecast.success",
        extra={
            "path": str(request.url.path),
            "transactions": len(transactions),
            "series_length": len(series),
            "algorithm": result.get("algorithm"),
            "trend": result.get("trend"),
            "next_month": result.get("next_month"),
        },
    )

    return result


@app.post("/insights")
async def financial_insights(
    request: Request, file: List[UploadFile] | None = File(None)
) -> dict:
    """Generate rule-based financial insights and overspending detection.

    Uses the same input formats as /classify and /forecast, and combines:
    - per-transaction classification
    - monthly income/expense + category totals
    - simple forecast of next month's expenses
    - rule-based budget and savings evaluation with a narrative report
    """

    transactions, _ = await parse_transactions(request, file)

    # Reuse the classifier to determine per-transaction categories
    categories = classify_transactions(transactions)
    monthly_summary = build_monthly_summary(transactions, categories)
    months = monthly_summary.get("months", [])
    if not months:
        raise HTTPException(
            status_code=400, detail="No monthly data could be computed."
        )

    latest_month = months[-1]
    total_income = float(latest_month["income"])
    total_expenses = float(latest_month["expenses"])
    net_balance = float(latest_month["net_balance"])
    month_label = latest_month["month"]
    cat_list: list[dict] = latest_month.get(
        "categories", []
    )  # [{"category", "amount"}, ...]

    # Build a forecast of monthly expenses over the full history, when possible.
    forecast_result: dict | None
    try:
        series = build_monthly_expense_series(transactions)
    except ValueError:
        forecast_result = None
    else:
        forecaster = SpendingForecaster()
        try:
            forecast_result = forecaster.forecast(series)
        except ValueError:
            forecast_result = None

    if forecast_result is not None:
        try:
            predicted_next = float(forecast_result["next_month_forecast"])
        except (KeyError, TypeError, ValueError):  # pragma: no cover - defensive
            predicted_next = total_expenses
        trend = str(forecast_result.get("trend", "Stable"))
    else:
        predicted_next = total_expenses
        trend = "Stable"

    engine = BudgetInsightEngine()
    insight_df = engine.run_inference_engine(cat_list, total_income)
    report = engine.generate_nlp_report(
        insight_df,
        total_income=total_income,
        total_expense=total_expenses,
        predicted_next=predicted_next,
        trend=trend,
    )

    logger.info(
        "insights.success",
        extra={
            "path": str(request.url.path),
            "transactions": len(transactions),
            "month": month_label,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "trend": trend,
            "predicted_next": predicted_next,
        },
    )

    return {
        "month": month_label,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_balance": net_balance,
        "forecast": forecast_result,
        "insights": insight_df.to_dict(orient="records"),
        "report": report,
    }
