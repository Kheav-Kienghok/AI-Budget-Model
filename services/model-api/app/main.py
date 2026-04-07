import os
import re
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from scipy.sparse import hstack


class PredictRequest(BaseModel):
    description: str = Field(..., min_length=1)
    amount: float = 0.0
    transaction_type: str = "Expense"
    date: str = "2026-01-01"


class PredictResponse(BaseModel):
    predicted_category: str


app = FastAPI(title="Expense Classifier API", version="0.1.0")


def clean_text(text: str) -> str:
    text = str(text).lower()
    text = re.sub(r"\b(gasoline|petrol|fuel|diesel)\b", "petrol", text)
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    return text.strip()


def get_model_path() -> Path:
    env_path = os.getenv("MODEL_ARTIFACT_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[3] / "models" / "expense_classifier.joblib"


def load_artifact() -> dict:
    model_path = get_model_path()
    if not model_path.exists():
        raise FileNotFoundError(
            f"Model artifact not found at {model_path}. Run notebooks/training/export_tfidf_model.py first."
        )
    return joblib.load(model_path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse)
def predict(payload: PredictRequest) -> PredictResponse:
    try:
        artifact = load_artifact()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    vectorizer = artifact["vectorizer"]
    scaler = artifact["scaler"]
    model = artifact["model"]
    label_encoder = artifact["label_encoder"]
    num_features = artifact["num_features"]

    cleaned = clean_text(payload.description)

    try:
        parsed_date = pd.to_datetime(payload.date)
    except (ValueError, TypeError):
        parsed_date = datetime(2026, 1, 1)

    type_value = 1 if str(payload.transaction_type).lower() == "income" else 0
    day_of_week = int(parsed_date.dayofweek)
    is_weekend = 1 if day_of_week in [5, 6] else 0

    input_num = pd.DataFrame(
        [{
            "Amount": float(payload.amount),
            "Type": type_value,
            "Month": int(parsed_date.month),
            "Day_of_Week": day_of_week,
            "Is_Weekend": is_weekend,
        }]
    )

    # Keep numerical input order aligned with training.
    input_num = input_num[num_features]
    input_num_scaled = scaler.transform(input_num)
    input_text = vectorizer.transform([cleaned])
    input_features = hstack([input_text, input_num_scaled])

    pred_idx = model.predict(input_features)[0]
    pred_label = label_encoder.inverse_transform(np.array([pred_idx]))[0]
    return PredictResponse(predicted_category=str(pred_label))
