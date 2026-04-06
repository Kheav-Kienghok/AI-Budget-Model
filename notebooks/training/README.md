# Training Workspace

This folder contains notebooks and training utilities.

## Contents
- expense_classification_models.ipynb: main modeling notebook
- personal_expense.ipynb: original exploration notebook
- export_tfidf_model.py: script to train and export a deployable model artifact

## Export Model Artifact
Run from repository root:

```bash
python notebooks/training/export_tfidf_model.py
```

The script writes a model artifact to models/expense_classifier.joblib.
