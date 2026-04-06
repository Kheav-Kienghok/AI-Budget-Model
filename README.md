# AI Budget Model Monorepo

## Overview
This repository is organized as a monorepo with three main workstreams:
1. Notebook-based training and experimentation
2. HTTP API service for model inference
3. Telegram chatbot that calls the API

## Project Structure
```text
AI-Budget-Model/
├── data/
├── models/
├── notebooks/
│   └── training/
│       ├── expense_classification_models.ipynb
│       ├── personal_expense.ipynb
│       ├── export_tfidf_model.py
│       └── README.md
├── services/
│   ├── model-api/
│   │   ├── app/
│   │   │   └── main.py
│   │   ├── pyproject.toml
│   │   └── README.md
│   └── telegram-bot/
│       ├── bot/
│       │   └── main.py
│       ├── pyproject.toml
│       └── README.md
├── pyproject.toml
└── README.md
```

## Quick Start

### 1. Train and Export Model Artifact
```bash
python notebooks/training/export_tfidf_model.py
```

Expected output artifact:
- models/expense_classifier.joblib

### 2. Start HTTP API
```bash
uvicorn app.main:app --app-dir services/model-api --reload
```

API endpoint:
- POST /predict

### 3. Start Telegram Bot
Set environment variables first:
- TELEGRAM_BOT_TOKEN
- MODEL_API_PREDICT_URL (optional, default: http://localhost:8000/predict)

Then run:
```bash
python services/telegram-bot/bot/main.py
```