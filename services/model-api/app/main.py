from __future__ import annotations

import io
from typing import List

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from pydantic import ValidationError

from .core.forecast import SpendingForecaster, build_monthly_expense_series
from .core.insights import BudgetInsightEngine
from .core.model import classify_transactions, load_model
from .schemas.transaction import Transaction, MonthData


app = FastAPI(
	title="Expense Category Prediction API",
    description=(
        "Predict expense categories from transaction descriptions. "
        "Supports both JSON input and CSV file uploads."
    ),
    version="1.0.0",
)

@app.on_event("startup")
def _startup() -> None:  # pragma: no cover - simple startup hook
	# Ensure model is loaded at startup so first request is fast and fails early
	load_model()


@app.get("/health")
def health() -> dict[str, str]:
	"""Health check endpoint."""

	try:
		load_model()
	except Exception:  # noqa: BLE001
		return {"status": "error"}
	return {"status": "ok"}


async def _parse_transactions(request: Request, file: List[UploadFile] | None) -> tuple[list[Transaction], bool]:
	"""Parse incoming request (JSON or CSV) into Transaction objects.

	Returns a tuple of (transactions, is_single_json), where is_single_json
	is True when the input was a single JSON object.
	"""

	content_type = request.headers.get("content-type", "").lower()

	# JSON payload path
	if content_type.startswith("application/json"):
		raw_body = await request.json()

		is_single = False
		if isinstance(raw_body, dict):
			is_single = True
			items = [raw_body]
		elif isinstance(raw_body, list):
			items = raw_body
		else:
			raise HTTPException(status_code=400, detail="Invalid JSON payload.")

		try:
			transactions = [Transaction.model_validate(item) for item in items]
		except ValidationError as exc:  # pragma: no cover - FastAPI will format this
			raise HTTPException(status_code=422, detail=exc.errors()) from exc

		if not transactions:
			raise HTTPException(status_code=400, detail="No valid transactions provided.")

		return transactions, is_single

	# CSV upload path
	if "multipart/form-data" in content_type:
		if not file:
			raise HTTPException(status_code=400, detail="No CSV files uploaded.")

		transactions: list[Transaction] = []

		for upload in file:
			filename = upload.filename

			if not filename:
				raise HTTPException(
					status_code=400,
					detail="Uploaded file has no filename.",
				)

			if not filename.lower().endswith(".csv"):
				raise HTTPException(
					status_code=400,
					detail=f"Unsupported file type for '{filename}'. Only .csv allowed.",
				)

			contents = await upload.read()
			try:
				df = pd.read_csv(io.BytesIO(contents))
			except Exception as exc:  # noqa: BLE001
				raise HTTPException(
					status_code=400,
					detail=f"Failed to parse CSV file '{upload.filename}': {exc}",
				) from exc

			if df.empty:
				continue

			column_map = {col.lower().strip(): col for col in df.columns}
			# We require date/description/amount. `type` is optional and will
			# default to "Expense" when missing. Extra columns like
			# `raw_category` or `category` are ignored.
			required_cols = ["date", "description", "amount"]
			missing = [col for col in required_cols if col not in column_map]
			if missing:
				raise HTTPException(
					status_code=400,
					detail=(
						f"CSV file '{upload.filename}' is missing required columns: "
						f"{', '.join(missing)}."
					),
				)

			for _, row in df.iterrows():
				tx_payload = {
					"date": row[column_map["date"]],
					"description": row[column_map["description"]],
					"amount": row[column_map["amount"]],
				}

				# Optional type column; default to Expense when not present.
				type_col = column_map.get("type")
				if type_col is not None:
					tx_payload["type"] = row[type_col]
				else:
					tx_payload["type"] = "Expense"

				try:
					transactions.append(Transaction.model_validate(tx_payload))
				except ValidationError as exc:  # noqa: BLE001
					raise HTTPException(
						status_code=422,
						detail=(
							"Validation error when parsing row from CSV file "
							f"'{upload.filename}': {exc}"
						),
					) from exc

		if not transactions:
			raise HTTPException(status_code=400, detail="No valid rows found in uploaded CSV files.")

		return transactions, False

	raise HTTPException(
		status_code=415,
		detail="Unsupported content type. Use application/json or multipart/form-data with CSV.",
	)


def _build_classification_response(
	transactions: list[Transaction],
	categories: list[str],
	is_single: bool,
) -> dict:
	"""Build response payload for per-transaction classifications."""

	if is_single:
		return {
			"date": str(transactions[0].date),
			"description": transactions[0].description,
			"amount": transactions[0].amount,
			"type": transactions[0].transaction_type,
			"category": categories[0],
		}

	rows = []
	for idx, (tx, category) in enumerate(zip(transactions, categories)):
		rows.append(
			{
				"index": idx,
				"date": str(tx.date),
				"description": tx.description,
				"amount": tx.amount,
				"type": tx.transaction_type,
				"category": category,
			}
		)

	return {
		"total_rows": len(rows),
		"rows": rows,
	}


def _build_monthly_summary(
	transactions: list[Transaction],
	categories: list[str],
) -> dict:
	"""Build monthly income/expense summary with per-category totals."""

	monthly: dict[str, MonthData] = {}

	for tx, category in zip(transactions, categories):
		month_key = f"{tx.date.year:04d}-{tx.date.month:02d}"
		month_data = monthly.setdefault(
			month_key,
			{"income": 0.0, "expenses": 0.0, "categories": {}},
		)

		amount = float(tx.amount)
		_type = (tx.transaction_type or "").strip().lower()

		if _type == "income":
			month_data["income"] = float(month_data["income"]) + amount
		else:
			month_data["expenses"] = float(month_data["expenses"]) + amount
			cat_map: dict = month_data["categories"]  # type: ignore[assignment]
			cat_map[category] = float(cat_map.get(category, 0.0)) + amount

	result_months: list[dict] = []
	for month_key in sorted(monthly.keys()):
		month_data = monthly[month_key]

		income = round(float(month_data["income"]), 2)
		expenses = round(float(month_data["expenses"]), 2)
	
		categories_map: dict = month_data["categories"]  # type: ignore[assignment]
		categories_list = [
			{"category": name, "amount": float(value)}
			for name, value in sorted(categories_map.items())
		]

		result_months.append(
			{
				"month": month_key,
				"income": income,
				"expenses": expenses,
				"net_balance": round(income - expenses, 2),
				"categories": categories_list,
			}
		)

	return {"months": result_months}


@app.post("/classify")
async def classify(request: Request, file: List[UploadFile] | None = File(None)) -> dict:
	"""Classify transactions and return both row-level and monthly summaries.

	Accepts the same payloads as before (JSON body or CSV upload) and returns
	a dictionary with two top-level keys:

	- ``classification`` – per-transaction classification result (single object
	  or list with ``total_rows``/``rows`` as before).
	- ``monthly_summary`` – aggregated income, expenses, net balance, and
	  per-category spending by month.
	"""

	transactions, is_single = await _parse_transactions(request, file)
	categories = classify_transactions(transactions)
	classification = _build_classification_response(transactions, categories, is_single)
	monthly = _build_monthly_summary(transactions, categories)

	return {
		"classification": classification,
		"monthly_summary": monthly,
	}


@app.post("/forecast")
async def forecast_spending(request: Request, file: List[UploadFile] | None = File(None)) -> dict:
	"""Forecast next month's total expenses for a single user.

	Accepts the same CSV/JSON payload as /classify and aggregates all
	non-income transactions into a monthly spending time series, then
	selects the best simple model (rolling average or weighted recent)
	via walk-forward validation and returns the forecast and trend.
	"""

	transactions, _ = await _parse_transactions(request, file)

	try:
		series = build_monthly_expense_series(transactions)
	except ValueError as exc:
		raise HTTPException(status_code=400, detail=str(exc)) from exc

	forecaster = SpendingForecaster()
	try:
		result = forecaster.forecast(series)
	except ValueError as exc:
		raise HTTPException(status_code=400, detail=str(exc)) from exc

	return result


@app.post("/insights")
async def financial_insights(request: Request, file: List[UploadFile] | None = File(None)) -> dict:
	"""Generate rule-based financial insights and overspending detection (Model 3).

	Uses the same input formats as /classify and /forecast, and combines:
	- per-transaction classification
	- monthly income/expense + category totals
	- simple forecast of next month's expenses
	- rule-based budget and savings evaluation with a narrative report
	"""

	transactions, _ = await _parse_transactions(request, file)

	# Reuse the classifier to determine per-transaction categories
	categories = classify_transactions(transactions)
	monthly_summary = _build_monthly_summary(transactions, categories)
	months = monthly_summary.get("months", [])
	if not months:
		raise HTTPException(status_code=400, detail="No monthly data could be computed.")

	latest_month = months[-1]
	total_income = float(latest_month["income"])
	total_expenses = float(latest_month["expenses"])
	net_balance = float(latest_month["net_balance"])
	month_label = latest_month["month"]
	cat_list: list[dict] = latest_month.get("categories", [])  # [{"category", "amount"}, ...]

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

	return {
		"month": month_label,
		"total_income": total_income,
		"total_expenses": total_expenses,
		"net_balance": net_balance,
		"forecast": forecast_result,
		"insights": insight_df.to_dict(orient="records"),
		"report": report,
	}

