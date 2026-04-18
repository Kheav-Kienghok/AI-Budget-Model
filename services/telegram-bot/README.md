# Telegram Chatbot Service

This bot forwards user text to the model API and returns predicted category.

## Environment Variables

- TELEGRAM_BOT_TOKEN: bot token from BotFather
- MODEL_API_PREDICT_URL: default `http://localhost:8000/predict`

## Run

From repository root after installing dependencies:

```bash
python services/telegram-bot/bot/main.py
```
