from __future__ import annotations

import io
from typing import List

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from pydantic import ValidationError

from .core.model import classify_transactions, load_model
from .schemas.transaction import Transaction


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


@app.post("/classify")
@app.post("/predict")
async def classify(request: Request, file: List[UploadFile] | None = File(None)) -> dict:
	"""Predict expense categories from JSON or CSV upload.

	- For JSON (`application/json`):
	  * Accepts either a single object or a list of objects.
	  * Object fields: `date`, `description`, `amount`, and either
		`transaction_type` or `type` ("Income"/"Expense").

	- For multipart form-data (`multipart/form-data`):
	  * Accepts one or more `.csv` files via `file` field.
	  * Required CSV columns (case-insensitive):
		`date`, `description`, `amount`, `type`.
	"""

	content_type = request.headers.get("content-type", "").lower()

	# JSON payload path (also used by the Telegram bot)
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

		categories = classify_transactions(transactions)

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

	# CSV upload path
	if "multipart/form-data" in content_type:
		if not file:
			raise HTTPException(status_code=400, detail="No CSV files uploaded.")

		transactions: list[Transaction] = []

		for upload in file:
			if not upload.filename.lower().endswith(".csv"):
				raise HTTPException(
					status_code=400,
					detail=f"Unsupported file type for '{upload.filename}'. Only .csv allowed.",
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

		categories = classify_transactions(transactions)

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

	# Unsupported content type
	raise HTTPException(
		status_code=415,
		detail="Unsupported content type. Use application/json or multipart/form-data with CSV.",
	)

