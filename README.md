# AI-Budget-Model

## Overview
This project downloads a personal finance dataset from Kaggle, then copies only CSV files into the local `data/` folder for analysis.

## Project Structure
```
AI-Budget-Model/
├── main.py
├── cleaned_budget_data.csv
├── personal_expense.ipynb
├── pyproject.toml
├── README.md
└── data/
	 ├── expense_data_1.csv
	 ├── expenses_income_summary.csv
	 └── Personal_Finance_Dataset.csv
```

## Requirements
- Python 3.10+
- `kagglehub`

## Setup
1. Install dependencies:
	```bash
	pip install kagglehub
	```
2. Run the script:
	```bash
	python main.py
	```

## What `main.py` Does
1. Downloads dataset: `jg7fujhfydhgc/expenses-2024`
2. Recursively finds all `.csv` files in the downloaded dataset
3. Copies CSV files into local `./data/`

## Output
After running, the terminal shows:
- Dataset download location
- Each copied CSV file
- Total number of CSV files copied