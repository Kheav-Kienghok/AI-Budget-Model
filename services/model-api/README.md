# Model API Service

This service exposes an HTTP endpoint for predicting expense categories from transaction input.

## Endpoints
- GET /health
- POST /predict

## Run
Start server from repository root:

```bash
uvicorn app.main:app --app-dir services/model-api --reload
```

## Request Example
```json
{
  "description": "Bought groceries",
  "amount": 29.5,
  "transaction_type": "Expense",
  "date": "2026-04-06"
}
```
