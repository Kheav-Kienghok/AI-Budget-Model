from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd


_DEFAULT_BUDGET_RULES: Dict[str, float] = {
    "Food": 25.0,
    "Transportation": 15.0,
    "Entertainment": 10.0,
    "Utilities": 10.0,
    "Rent": 30.0,
    "Other": 10.0,
}


@dataclass
class BudgetInsightEngine:
    """Rule-based financial insights and overspending detection (Model 3).

    This implements the proposal rules from Section IV.B:
    - OVERSPENDING: actual % of income exceeds threshold
    - WARNING: actual % of income > 85% of threshold
    - OK: within budget
    - Savings should be at least ``savings_rule`` percent of income.
    """

    budget_rules: Dict[str, float] = field(default_factory=lambda: _DEFAULT_BUDGET_RULES.copy())
    savings_rule: float = 20.0

    def run_inference_engine(self, categories: List[dict], total_income: float) -> pd.DataFrame:
        """Run the rule-based inference engine for a single month.

        Parameters
        ----------
        categories:
            List of dicts with keys ``{"category": str, "amount": float}`` representing
            per-category totals for the month.
        total_income:
            Total income for the same month.

        Returns
        -------
        pd.DataFrame
            One row per category plus a final "Savings" row, with columns:
            ``Category, Amount, Pct_Income, Limit_Pct, Status, Insight``.
        """

        cat_df = pd.DataFrame(
            {
                "Category": [c["category"] for c in categories],
                "Total_Spent": [float(c["amount"]) for c in categories],
            }
        ) if categories else pd.DataFrame(columns=["Category", "Total_Spent"])

        insights: list[dict] = []
        total_expense = float(cat_df["Total_Spent"].sum()) if not cat_df.empty else 0.0
        savings = float(total_income) - total_expense
        savings_pct = (savings / total_income * 100.0) if total_income > 0 else 0.0

        for _, row in cat_df.iterrows():
            cat = str(row["Category"])
            spent = float(row["Total_Spent"])
            pct = (spent / total_income * 100.0) if total_income > 0 else 0.0
            limit = float(self.budget_rules.get(cat, 10.0))

            if pct > limit:
                status = "OVERSPENDING"
                overspend_amount = spent - (total_income * limit / 100.0) if total_income > 0 else spent
                insight = (
                    f"You spent {pct:.1f}% of your income on {cat}, "
                    f"which exceeds the recommended limit of {limit}%. "
                    f"Consider reducing {cat} by ${overspend_amount:,.2f}."
                )
            elif pct > limit * 0.85:
                status = "WARNING"
                insight = (
                    f"{cat} is at {pct:.1f}% of income, "
                    f"approaching the {limit}% limit. Monitor closely."
                )
            else:
                status = "OK"
                insight = (
                    f"{cat} spending is healthy at {pct:.1f}% of income "
                    f"(limit: {limit}%)."
                )

            insights.append(
                {
                    "Category": cat,
                    "Amount": round(spent, 2),
                    "Pct_Income": round(pct, 2),
                    "Limit_Pct": limit,
                    "Status": status,
                    "Insight": insight,
                }
            )

        # Savings rule evaluation
        savings_status = "OK" if savings_pct >= self.savings_rule else "OVERSPENDING"
        if savings_status == "OVERSPENDING":
            insight = (
                f"Savings rate is only {savings_pct:.1f}%, below the recommended {self.savings_rule}%. "
                f"Try to save at least ${total_income * self.savings_rule / 100.0:,.2f}/month."
            )
        else:
            insight = (
                f"Great! Saving {savings_pct:.1f}% of income, "
                f"meeting the recommended {self.savings_rule}% minimum."
            )

        insights.append(
            {
                "Category": "Savings",
                "Amount": round(savings, 2),
                "Pct_Income": round(savings_pct, 2),
                "Limit_Pct": float(self.savings_rule),
                "Status": savings_status,
                "Insight": insight,
            }
        )

        return pd.DataFrame(insights)

    def generate_nlp_report(
        self,
        insight_df: pd.DataFrame,
        total_income: float,
        total_expense: float,
        predicted_next: float,
        trend: str,
    ) -> dict:
        """Generate a structured (JSON-friendly) NLP report for Model 3.

        Instead of a single text blob, this returns a dictionary organised into
        summary metrics and per-section messages so the API can respond with
        JSON that is easy to consume on the frontend.
        """

        savings = float(total_income) - float(total_expense)
        savings_pct = (savings / total_income * 100.0) if total_income > 0 else 0.0

        summary = {
            "total_income": round(float(total_income), 2),
            "total_expenses": round(float(total_expense), 2),
            "net_balance": round(savings, 2),
            "net_balance_pct": round(savings_pct, 1),
            "next_month_estimate": round(float(predicted_next), 2),
            "trend": str(trend),
        }

        oversp = insight_df[insight_df["Status"] == "OVERSPENDING"] if not insight_df.empty else insight_df
        warnings = insight_df[insight_df["Status"] == "WARNING"] if not insight_df.empty else insight_df
        healthy = insight_df[insight_df["Status"] == "OK"] if not insight_df.empty else insight_df

        overspending_messages = [str(r["Insight"]) for _, r in oversp.iterrows()] if not oversp.empty else []
        warning_messages = [str(r["Insight"]) for _, r in warnings.iterrows()] if not warnings.empty else []
        healthy_messages = [str(r["Insight"]) for _, r in healthy.iterrows()] if not healthy.empty else []

        if savings_pct < self.savings_rule:
            recommendations = [
                "Predicted expenses are high. Consider adjusting your budget.",
                f"Target savings: ${total_income * self.savings_rule / 100.0:,.2f}/month",
                f"Current savings gap: ${total_income * self.savings_rule / 100.0 - savings:,.2f}",
            ]
            recommendation_status = "ADJUST_BUDGET"
        else:
            recommendations = [
                "Your budget is well managed. Keep maintaining your saving habits!",
            ]
            recommendation_status = "ON_TRACK"

        return {
            "summary": summary,
            "sections": {
                "overspending_warnings": overspending_messages,
                "near_limit_warnings": warning_messages,
                "healthy_categories": healthy_messages,
            },
            "budget_recommendations": {
                "status": recommendation_status,
                "target_savings_pct": float(self.savings_rule),
                "current_savings_pct": round(savings_pct, 1),
                "messages": recommendations,
            },
        }
