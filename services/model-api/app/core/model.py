from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import List, Any
from urllib.request import urlopen

import joblib
import pandas as pd
from scipy.sparse import hstack

from ..schemas.transaction import Transaction
from ..schemas.category_map import CATEGORY_MAP
from ..utils.text import clean_text

_MODEL_ARTIFACT: dict[str, Any] | None = None


def load_model() -> dict[str, Any]:
    """Load and cache the trained model artifact."""

    global _MODEL_ARTIFACT
    if _MODEL_ARTIFACT is not None:
        return _MODEL_ARTIFACT

    file_path = Path(__file__).resolve()
    parents = list(file_path.parents)

    # Derive service_root and project_root in a way that works both in the
    # local monorepo layout and inside Docker, without assuming a fixed
    # directory depth.
    # Example layouts:
    #   /home/.../services/model-api/app/core/model.py
    #   /app/app/core/model.py  (inside container)
    if len(parents) >= 3:
        service_root = parents[2]
    else:
        service_root = parents[0]

    # Prefer the nearest ancestor containing pyproject.toml as project_root.
    project_root_candidate = next(
        (p for p in parents if (p / "pyproject.toml").is_file()),
        None,
    )
    if project_root_candidate is not None:
        project_root = project_root_candidate
    else:
        # Fallback: parent of service_root, or service_root itself if at filesystem root.
        project_root = (
            service_root.parent if service_root.parent != service_root else service_root
        )
    env_rel_path = os.getenv("MODEL_ARTIFACT_PATH", "models/expense_classifier.joblib")

    env_path = Path(env_rel_path)
    if env_path.is_absolute():
        env_candidates = [env_path]
    else:
        # Support env paths relative to either the service root or project root.
        env_candidates = [service_root / env_path, project_root / env_path]

    # Compatibility shim: some older artifacts were pickled with a reference
    # to a top-level `clean_text` function in the `__mp_main__` module.
    # Ensure that module exists and exposes `clean_text`, even if uvicorn has
    # already registered its own __mp_main__ entry point.
    compat_module = sys.modules.get("__mp_main__") or types.ModuleType("__mp_main__")
    setattr(compat_module, "clean_text", clean_text)
    sys.modules["__mp_main__"] = compat_module

    candidates = [
        *env_candidates,
        project_root / "models" / "expense_classifier.joblib",
        project_root / "models" / "tfidf_expense_classifier.joblib",
    ]

    for path in candidates:
        if path.is_file():
            artifact = joblib.load(path)
            _MODEL_ARTIFACT = artifact
            return artifact

    # If no local artifact is found, fall back to downloading a default
    # model artifact from GitHub into the project_root/models directory.
    fallback_url = (
        "https://raw.githubusercontent.com/"
        "Kheav-Kienghok/AI-Budget-Model/main/models/"
        "tfidf_expense_classifier.joblib"
    )
    fallback_dir = project_root / "models"
    fallback_path = fallback_dir / "tfidf_expense_classifier.joblib"

    try:
        fallback_dir.mkdir(parents=True, exist_ok=True)
        with urlopen(fallback_url) as response:
            data = response.read()
        fallback_path.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Model artifact not found locally and automatic download failed. "
            "Expected at one of: "
            f"{', '.join(str(p) for p in candidates)}. "
            "Also attempted to download from "
            f"{fallback_url} but got error: {exc}. "
            "Train and export the model or ensure the file is accessible.",
        ) from exc

    artifact = joblib.load(fallback_path)
    _MODEL_ARTIFACT = artifact
    return artifact


def classify_transactions(transactions: List[Transaction]) -> list[str]:
    """Convert incoming transactions into predicted categories using the model.

    Each Transaction is transformed into the same numeric/text feature space that
    was used during training, then passed through the loaded model.
    """

    if not transactions:
        raise ValueError("No transactions provided for classification.")

    artifact = load_model()
    vectorizer = artifact["vectorizer"]
    scaler = artifact["scaler"]
    model = artifact["model"]
    label_encoder = artifact["label_encoder"]
    num_features = artifact.get(
        "num_features", ["Amount", "Type", "Month", "Day_of_Week", "Is_Weekend"]
    )

    texts: list[str] = []
    amounts: list[float] = []
    types_num: list[int] = []
    months: list[int] = []
    days_of_week: list[int] = []
    is_weekend_flags: list[int] = []

    for tx in transactions:
        texts.append(clean_text(tx.description))
        amounts.append(float(tx.amount))

        type_str = (tx.transaction_type or "").strip().lower()
        types_num.append(1 if type_str == "income" else 0)

        dt = tx.date
        dow = dt.weekday()
        month = dt.month
        months.append(month)
        days_of_week.append(dow)
        is_weekend_flags.append(1 if dow in (5, 6) else 0)

    df_num = pd.DataFrame(
        {
            "Amount": amounts,
            "Type": types_num,
            "Month": months,
            "Day_of_Week": days_of_week,
            "Is_Weekend": is_weekend_flags,
        }
    )

    X_text = vectorizer.transform(texts)
    X_num = scaler.transform(df_num[num_features])
    X = hstack([X_text, X_num])

    encoded_labels = model.predict(X)
    raw_categories = label_encoder.inverse_transform(encoded_labels)

    # Remap model output categories into the normalized CATEGORY_MAP space.
    categories = [CATEGORY_MAP.get(str(cat), str(cat)) for cat in raw_categories]
    return list(categories)
