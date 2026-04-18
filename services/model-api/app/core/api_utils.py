from __future__ import annotations

import io
from typing import List, Tuple

import pandas as pd
from fastapi import HTTPException, Request, UploadFile
from pydantic import ValidationError

from ..schemas.transaction import MonthData, Transaction


async def parse_transactions(
    request: Request,
    file: List[UploadFile] | None,
) -> Tuple[list[Transaction], bool]:
    """Parse incoming request (JSON or CSV) into Transaction objects.

    Returns a tuple of (transactions, is_single_json), where is_single_json
    is True when the input was a single JSON object. JSON requests may also be
    wrapped in an object with a top-level ``transactions`` field.
    """

    content_type = request.headers.get("content-type", "").lower()

    # JSON payload path
    if content_type.startswith("application/json"):
        raw_body = await request.json()

        is_single = False
        if isinstance(raw_body, dict):
            if "transactions" in raw_body:
                items = raw_body["transactions"]
                if isinstance(items, dict):
                    items = [items]
                    is_single = True
                elif isinstance(items, list):
                    is_single = False
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid JSON payload. 'transactions' must be an object or a list.",
                    )
            else:
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
            raise HTTPException(
                status_code=400, detail="No valid transactions provided."
            )

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
            raise HTTPException(
                status_code=400, detail="No valid rows found in uploaded CSV files."
            )

        return transactions, False

    raise HTTPException(
        status_code=415,
        detail="Unsupported content type. Use application/json or multipart/form-data with CSV.",
    )


def build_classification_response(
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


def build_monthly_summary(
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
