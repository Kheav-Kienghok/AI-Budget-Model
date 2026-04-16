from __future__ import annotations

from datetime import date, datetime
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Transaction(BaseModel):
    """Single transaction used for prediction.

    Supports both JSON with `transaction_type` and CSV/JSON with `type`.
    """

    date: date
    description: str
    amount: float
    transaction_type: str = Field(alias="type")

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @field_validator("date", mode="before")
    @classmethod
    def normalize_date(cls, value):
        """Accept common CSV date formats while preserving strict date output."""

        if isinstance(value, date):
            return value

        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return value

            # Fast path for common slash-separated CSV formats.
            for fmt in ("%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y"):
                try:
                    return datetime.strptime(cleaned, fmt).date()
                except ValueError:
                    continue

            return cleaned

        return value


class MonthData(TypedDict):
    income: float
    expenses: float
    categories: dict[str, float]
