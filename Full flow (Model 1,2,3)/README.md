# AI Budget Tracker with Smart Financial Insights

A personal finance AI system that automatically classifies your expenses, predicts next month's spending, and gives you smart budget warnings — all from a simple CSV file.

Built for **ITM-360 Artificial Intelligence** at the American University of Phnom Penh.

---

## What It Does

You upload your transactions (income + expenses). The pipeline runs three AI models automatically:

| Step | Model | What it does |
|------|-------|--------------|
| 1 | — | You import your CSV (income + expenses) |
| 2 | **Model 1** | Classifies each expense into a category (Food, Transportation, etc.) |
| 3 | — | Aggregates spending by category and month |
| 4 | **Model 2** | Predicts how much you will spend next month |
| 5 | **Model 3** | Checks if you are overspending and gives advice |
| 6 | — | Shows a dashboard with charts and a full report |

---

## The Three AI Models

### Model 1 — Expense Classifier
- **Type:** TF-IDF + Logistic Regression (supervised machine learning)
- **Input:** Transaction description text (e.g. "Bought rice at market")
- **Output:** One of 6 categories: `Food`, `Transportation`, `Entertainment`, `Utilities`, `Rent`, `Other`
- **How:** The pre-trained model (`tfidf_expense_classifier.joblib`) reads the text and assigns a category automatically. No manual tagging needed.

### Model 2 — Spending Predictor
- **Type:** Rolling Average / Weighted Recent (time-series forecasting)
- **Input:** Your own monthly spending history
- **Output:** Predicted total spending for next month
- **Important:** This model has **no pre-trained weights**. It trains fresh on your data every time you run it. This makes it personal — it learns your own spending patterns, not someone else's.

### Model 3 — Financial Insight Engine
- **Type:** Rule-Based Inference System (expert system)
- **Input:** Your category spending totals + your income
- **Output:** Warnings, advice, and a financial report
- **How:** Compares each category's % of income against recommended budget limits:

| Category | Recommended Limit |
|----------|------------------|
| Food | ≤ 25% of income |
| Transportation | ≤ 15% of income |
| Entertainment | ≤ 10% of income |
| Utilities | ≤ 10% of income |
| Rent | ≤ 30% of income |
| Other | ≤ 10% of income |
| Savings | ≥ 20% of income |

---

## Folder Structure

```
budget_tracker_pipeline/
├── pipeline.ipynb                  ← main notebook (run this)
├── tfidf_expense_classifier.joblib ← pre-trained Model 1
├── build_pipeline.py               ← script that builds the notebook
└── README.md                       ← this file
```

---

## Requirements

```
pandas
numpy
matplotlib
scikit-learn
scipy
joblib
```

Install all at once:
```bash
pip install pandas numpy matplotlib scikit-learn scipy joblib
```

---

## How to Run

### Step 1 — Prepare your CSV

Create a file called `user_transactions.csv` with these columns:

| date | description | amount | type |
|------|-------------|--------|------|
| 2024-01-01 | Monthly salary | 500 | Income |
| 2024-01-05 | Bought rice at market | 4.50 | Expense |
| 2024-01-08 | Grab ride to work | 3.00 | Expense |
| 2024-01-12 | Netflix subscription | 15.99 | Expense |

- `date` — format: `YYYY-MM-DD`
- `description` — plain text description of the transaction
- `amount` — positive number (no negative values)
- `type` — either `Income` or `Expense`

> If you don't have a CSV yet, the notebook has built-in demo data so you can run it immediately.

### Step 2 — Open the notebook

Open `pipeline.ipynb` in Jupyter Notebook or VS Code.

### Step 3 — Point to your CSV (optional)

In **Cell 5**, comment out the demo data block and uncomment this line:
```python
df_raw = pd.read_csv(CSV_PATH)
```
Make sure `CSV_PATH = 'user_transactions.csv'` matches your file name.

### Step 4 — Run all cells

Click **Kernel → Restart & Run All** (Jupyter) or **Run All** (VS Code).

That's it. The pipeline runs automatically from top to bottom.

---

## What You Get

After running, you will see:

**1. Classified transaction table**
```
date        description                         amount   raw_category   category
2024-01-05  Bought rice and vegetables          4.50     Food           Food
2024-01-08  Grab ride to work                   3.00     Transport      Transportation
2024-01-12  Netflix subscription                15.99    Entertainment  Entertainment
```

**2. Monthly financial summary**
```
Month       Income    Expenses   Net Balance
2024-01    $500.00    $101.49      $398.51
2024-02    $500.00     $34.50      $465.50
```

**3. Spending prediction**
```
Next month forecast (2024-07): $253.74
Algorithm: Rolling Average  |  Window: 2 months  |  Trend: Stable
```

**4. Financial insight report**
```
[SUMMARY]
  Total Income   :    $500.00
  Total Expenses :    $237.99
  Net Balance    :    $262.01  (52.4%)
  Next Month Est.:    $253.74  (Stable)

[OVERSPENDING WARNINGS]
  - You spent 40.0% of your income on Rent, which exceeds the 30% limit.

[HEALTHY CATEGORIES]
  - Food spending is healthy at 5.2% of income (limit: 25%).

[BUDGET RECOMMENDATIONS]
  - Your budget is well managed. Keep maintaining your saving habits!
```

**5. Dashboard image** (`pipeline_dashboard.png`) — 4 charts:
- Income vs Expenses over time (with forecast star)
- Spending breakdown by category (pie chart, latest month)
- Category spending over time (stacked bar)
- Monthly net balance vs savings target

---

## Customizing Budget Rules

In **Cell 14**, you can override any threshold to fit your own budget:

```python
CUSTOM_RULES = {
    'Food': 20,       # tighten food budget to 20%
    'Rent': 40,       # allow more for rent
}
```

Leave it empty `{}` to use the default proposal thresholds.

---

## Key Design Decision: Why Model 2 Has No Pre-Training

Everyone's spending is different. A model trained on someone else's data would give wrong predictions for you. Model 2 is designed to:

- Train only on **your** historical data
- Run in under 1 second (no GPU needed)
- Automatically find the best window size (2–5 months) using walk-forward validation
- Re-train instantly whenever you add new data

This is called **on-demand, per-user training**.

---

## Project Info

| Field | Detail |
|-------|--------|
| Course | ITM-360 Artificial Intelligence |
| University | American University of Phnom Penh |
| Team Leader | Chandaro HEN |
| Members | Kienghok KHEAV, Vatanautdom KHENG, Tunvatnak TAING |
| Advisor | Kuntha PIN |
