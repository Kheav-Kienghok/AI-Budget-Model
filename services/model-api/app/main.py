from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from datetime import datetime, timezone
import asyncio
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import pandas as pd

from .core.api_utils import (
    build_classification_response,
    build_monthly_summary,
    parse_transactions,
)
from .core.dashboard import BudgetDashboardRenderer
from .core.forecast import SpendingForecaster, build_monthly_expense_series
from .core.insights import BudgetInsightEngine
from .core.model import classify_transactions, load_model

logger = logging.getLogger("model_api.api")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("model_api.log"), logging.StreamHandler()],
)


def _build_dashboard_payload(
    monthly_summary: dict,
    latest_month: dict,
    cat_list: list[dict],
    insight_df,
    forecast_result: dict | None,
    predicted_next: float,
    engine: BudgetInsightEngine,
) -> dict:
    """Build dashboard table + chart using the shared renderer.

    This helper converts the monthly summary + insight DataFrame into the
    DataFrame shapes expected by ``BudgetDashboardRenderer`` and returns the
    resulting payload (JSON table + image path/URL).
    """

    months_data: list[dict] = monthly_summary.get("months", [])
    if not months_data:
        return {
            "table": {"header": [], "rows": []},
            "image_url": None,
        }

    month_labels = [m["month"] for m in months_data]
    month_dt = pd.to_datetime(month_labels)

    monthly_df = pd.DataFrame(
        {
            "month_dt": month_dt,
            "total_income": [float(m["income"]) for m in months_data],
            "total_expenses": [float(m["expenses"]) for m in months_data],
        }
    )
    monthly_df["net_balance"] = (
        monthly_df["total_income"] - monthly_df["total_expenses"]
    )

    # Build monthly category matrix (rows = months, cols = categories)
    cat_rows: list[dict] = []
    for m in months_data:
        cat_map = {c["category"]: float(c["amount"]) for c in m.get("categories", [])}
        cat_rows.append(cat_map)

    if cat_rows:
        monthly_cat_df = pd.DataFrame(cat_rows, index=month_dt).fillna(0.0)
    else:
        monthly_cat_df = pd.DataFrame(index=month_dt)

    if cat_list:
        cat_series = pd.Series({c["category"]: float(c["amount"]) for c in cat_list})
    else:
        cat_series = pd.Series(dtype="float64")

    # Determine x-position for forecast marker
    if forecast_result is not None:
        next_month_str = str(forecast_result.get("next_month", latest_month["month"]))
        try:
            next_month_dt = pd.to_datetime(next_month_str)
        except Exception:  # noqa: BLE001
            next_month_dt = pd.to_datetime(latest_month["month"]) + pd.DateOffset(
                months=1
            )
    else:
        next_month_dt = pd.to_datetime(latest_month["month"]) + pd.DateOffset(months=1)

    latest_month_dt = pd.to_datetime(latest_month["month"])
    latest_month_str = latest_month_dt.strftime("%B %Y")

    renderer = BudgetDashboardRenderer(savings_rule=engine.savings_rule)

    # Save each dashboard image with a unique timestamp+UUID filename.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    unique_filename = f"dashboard_{timestamp}_{uuid4().hex}.png"
    output_path = STATIC_DIR / unique_filename

    rendered = renderer.render_dashboard(
        insight_df=insight_df,
        monthly=monthly_df,
        monthly_cat=monthly_cat_df,
        cat_series=cat_series,
        next_month=next_month_dt,
        next_pred=predicted_next,
        latest_month_str=latest_month_str,
        output_path=output_path,
    )

    # Build a JSON-friendly table representation from the insight DataFrame.
    header = ["category", "amount", "pct_income", "limit_pct", "status"]
    rows: list[dict] = []
    if not insight_df.empty:
        for _, r in insight_df.iterrows():
            rows.append(
                {
                    "category": str(r["Category"]),
                    "amount": float(r["Amount"]),
                    "pct_income": float(r["Pct_Income"]),
                    "limit_pct": float(r["Limit_Pct"]),
                    "status": str(r["Status"]),
                }
            )

    image_path = Path(rendered.get("image_path", "")).as_posix()
    if image_path.startswith("static/"):
        image_url = f"/{image_path}"
    elif image_path:
        image_url = f"/static/{Path(image_path).name}"
    else:
        image_url = None

    return {
        "table": {
            "header": header,
            "rows": rows,
        },
        "image_url": image_url,
    }


STATIC_DIR = Path("static")
STATIC_TTL_SECONDS = 600  # 10 minutes
CLEANUP_INTERVAL_SECONDS = 900  # 15 minutes


async def _static_cleanup_loop() -> None:
    """Periodically delete files in STATIC_DIR older than STATIC_TTL_SECONDS."""

    while True:
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            if STATIC_DIR.is_dir():
                for path in STATIC_DIR.iterdir():
                    if not path.is_file():
                        continue
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    if now_ts - mtime > STATIC_TTL_SECONDS:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            logger.exception(
                                "static_cleanup.unlink_error",
                                extra={"path": str(path)},
                            )
        except Exception:  # noqa: BLE001
            logger.exception("static_cleanup.error")

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    load_model()

    # Start background task to clean old static files
    cleanup_task = asyncio.create_task(_static_cleanup_loop())

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Expense Category Prediction API",
    description=(
        "Predict expense categories from transaction descriptions. "
        "Supports both JSON input and CSV file uploads."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,  # Disables Swagger UI
    redoc_url=None,  # Disables ReDoc
    openapi_url=None,  # Disables the underlying openapi.json
)

# Serve dashboard images and other assets from ./static under /static
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """Return an empty favicon to avoid 404 noise."""

    return Response(status_code=204)


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
async def unhandled_exception_logger(
    request: Request, exc: Exception
) -> JSONResponse:  # noqa: BLE001
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

    dashboard = _build_dashboard_payload(
        monthly_summary=monthly_summary,
        latest_month=latest_month,
        cat_list=cat_list,
        insight_df=insight_df,
        forecast_result=forecast_result,
        predicted_next=predicted_next,
        engine=engine,
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
        "dashboard": dashboard,
    }
