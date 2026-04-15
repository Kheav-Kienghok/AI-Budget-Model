from __future__ import annotations

from datetime import date
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Transaction(BaseModel):
	"""Single transaction used for prediction.

	Supports both JSON with `transaction_type` and CSV/JSON with `type`.
	"""

	date: date
	description: str
	amount: float
	transaction_type: str = Field(alias="type")

	model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MonthData(TypedDict):
    income: float
    expenses: float
    categories: dict[str, float]