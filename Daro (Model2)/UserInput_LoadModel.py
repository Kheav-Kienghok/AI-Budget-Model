import pandas as pd
import numpy as np

def prepare_user_input(user_expenses_dict, category_name, prediction_date, last_5_amounts, category_cols, FEATURES):
    """
    Converts user monthly expenses input into model-ready features.
    Inputs:
      - user_expenses_dict: dict of categories and their amounts
      - category_name: the category to predict (string)
      - prediction_date: datetime object for prediction
      - last_5_amounts: list of last 5 amounts in the target category
      - category_cols: list of all category columns in the trained model
      - FEATURES: list of all feature columns used by model
    Returns:
      - DataFrame with a single row ready for model.predict()
    """

    if len(last_5_amounts) != 5:
        raise ValueError("Please provide exactly 5 last amounts for lag features.")

    # Log transform
    last_5_log = np.log1p(last_5_amounts)

    # Lag features
    feature_values = {f'lag_t{i+1}': float(val) for i, val in enumerate(last_5_log[::-1])}

    # Rolling stats
    feature_values['rolling_mean_5'] = float(np.mean(last_5_log))
    feature_values['rolling_std_5'] = float(np.std(last_5_log))

    # Time features
    day_of_week = prediction_date.dayofweek
    month = prediction_date.month
    feature_values['day_of_week'] = int(day_of_week)
    feature_values['month'] = int(month)
    feature_values['is_weekend'] = int(day_of_week >= 5)

    # One-hot category
    for col in category_cols:
        feature_values[col] = 0  # default 0
    if f'category_{category_name}' in category_cols:
        feature_values[f'category_{category_name}'] = 1

    # Convert to DataFrame
    input_df = pd.DataFrame([feature_values], columns=FEATURES)
    return input_df








# Example user input
user_input_expenses = {
    "Food": 200,
    "Transportation": 25,
    "Entertainment": 50,
    "Utilities": 60,
    "Rent": 260,
    "Shopping": 100,
    "Healthcare": 50,
    "Education": 0,
    "Other": 200
}

# Suppose we want to predict next 'Food' spending
category_to_predict = "Food"

# Last 5 amounts in that category (from your input or historical data)
last_5_food_amounts = [180, 200, 190, 210, 200]  # example

# Prediction date (next day after last known date)
import datetime
prediction_date = df_processed['date'].iloc[-1] + pd.Timedelta(days=1)

# Prepare input
user_input_df = prepare_user_input(user_input_expenses, category_to_predict,
                                   prediction_date, last_5_food_amounts,
                                   category_cols, FEATURES)

# Predict
predicted_log = model_optimized.predict(user_input_df)[0]
predicted_amount = np.expm1(predicted_log)
print(f"Predicted next {category_to_predict} spending: ${predicted_amount:.2f}")




