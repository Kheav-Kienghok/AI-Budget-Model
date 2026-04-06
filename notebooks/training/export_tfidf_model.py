import re
from pathlib import Path

import joblib
import pandas as pd
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import LabelEncoder, StandardScaler


def clean_text(text: str) -> str:
    text = str(text).lower()
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    return text.strip()


def resolve_dataset_path(repo_root: Path) -> Path:
    candidates = [
        repo_root / "data" / "Personal_Finance_Dataset.csv",
        repo_root / "clean_data" / "cleaned_budget_data.csv",
        repo_root / "cleaned_budget_data.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("No dataset found in expected locations.")


def train_and_export() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dataset_path = resolve_dataset_path(repo_root)

    df = pd.read_csv(dataset_path, encoding="utf-8")
    if "Transaction Description" not in df.columns or "Category" not in df.columns:
        raise ValueError("Dataset must contain 'Transaction Description' and 'Category' columns.")

    df["text"] = df["Transaction Description"].apply(clean_text)

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df["Month"] = df["Date"].dt.month.fillna(1).astype(int)
        df["Day_of_Week"] = df["Date"].dt.dayofweek.fillna(0).astype(int)
    else:
        df["Month"] = 1
        df["Day_of_Week"] = 0

    if "Type" in df.columns:
        df["Type"] = df["Type"].map({"Expense": 0, "Income": 1}).fillna(0).astype(int)
    else:
        df["Type"] = 0

    df["Is_Weekend"] = df["Day_of_Week"].isin([5, 6]).astype(int)
    df["Amount"] = pd.to_numeric(df.get("Amount", 0), errors="coerce").fillna(0)

    le = LabelEncoder()
    y = le.fit_transform(df["Category"])

    vectorizer = TfidfVectorizer()
    X_text = vectorizer.fit_transform(df["text"])

    num_features = ["Amount", "Type", "Month", "Day_of_Week", "Is_Weekend"]
    X_num_df = df[num_features].copy().fillna(0)
    scaler = StandardScaler()
    X_num = scaler.fit_transform(X_num_df)

    X = hstack([X_text, X_num])

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)

    artifact = {
        "vectorizer": vectorizer,
        "scaler": scaler,
        "model": model,
        "label_encoder": le,
        "num_features": num_features,
    }

    model_dir = repo_root / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    output_path = model_dir / "expense_classifier.joblib"
    joblib.dump(artifact, output_path)

    print(f"Saved model artifact to {output_path}")


if __name__ == "__main__":
    train_and_export()
