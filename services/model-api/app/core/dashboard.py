from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import matplotlib.pyplot as plt
from matplotlib import gridspec
import numpy as np
import pandas as pd
import seaborn as sns


@dataclass
class BudgetDashboardRenderer:
    """Render a budget insights dashboard as text + PNG chart.

    This wraps the notebook-style visualization into a reusable class.

    Parameters
    ----------
    savings_rule:
        Savings rule percentage used for the savings target line.
    icon_map:
        Mapping from insight Status (OVERSPENDING/WARNING/OK) to display icon.
    output_filename:
        Default filename for the saved dashboard PNG when no explicit
        output_path is provided.
    """

    savings_rule: float = 20.0
    icon_map: Dict[str, str] = field(
        default_factory=lambda: {
            "OVERSPENDING": "[!]",
            "WARNING": "[~]",
            "OK": "[ok]",
        }
    )
    output_filename: str = "pipeline_dashboard.png"

    def render_dashboard(
        self,
        insight_df: pd.DataFrame,
        monthly: pd.DataFrame,
        monthly_cat: pd.DataFrame,
        cat_series: pd.Series,
        next_month: Any,
        next_pred: float,
        latest_month_str: str,
        output_path: str | Path | None = None,
    ) -> dict:
        """Build the dashboard and return table text + image path.

        Parameters
        ----------
        insight_df:
            DataFrame produced by BudgetInsightEngine.run_inference_engine.
            Must contain columns: Category, Amount, Pct_Income, Limit_Pct, Status.
        monthly:
            Monthly summary DataFrame with at least columns
            ["month_dt", "total_income", "total_expenses", "net_balance"].
        monthly_cat:
            Monthly category spend DataFrame indexed by month, columns are
            category names and values are amounts.
        cat_series:
            Series of latest-month category totals used for the pie chart.
        next_month:
            Datetime-like object for the predicted next month (x-position of
            the forecast marker).
        next_pred:
            Forecasted expenses value for next_month.
        latest_month_str:
            Human-friendly label for the latest month, used in titles.
        output_path:
            Optional explicit output path for the saved PNG. When omitted,
            output_filename in the current working directory is used.
        """

        icon = self.icon_map

        # Use a consistent Seaborn theme for nicer defaults.
        sns.set_theme(style="whitegrid")

        # Build the insight table as a formatted text block
        header = f"Insight Table - {latest_month_str}"
        lines: list[str] = [header]
        lines.append(
            f"  {'Category':<16} {'Amount':>9} {'% Income':>9} {'Limit':>7} {'Status':>8}"
        )
        lines.append("  " + "-" * 53)

        if not insight_df.empty:
            for _, r in insight_df.iterrows():
                status_icon = icon.get(str(r["Status"]), "")
                lines.append(
                    "  "
                    + f"{str(r['Category']):<16} "
                    + f"${float(r['Amount']):>8,.2f} "
                    + f"{float(r['Pct_Income']):>8.1f}% "
                    + f"{float(r['Limit_Pct']):>6.0f}%  "
                    + f"{status_icon:>5}"
                )

        table_text = "\n".join(lines) + "\n"

        # Prepare output path
        if output_path is None:
            output_path = Path(self.output_filename)
        else:
            output_path = Path(output_path)

        # Ensure parent directory exists (for static/public folders).
        if output_path.parent != Path(""):
            output_path.parent.mkdir(parents=True, exist_ok=True)

        # Figure + gridspec layout
        fig = plt.figure(figsize=(15, 10))
        fig.suptitle(
            "AI Budget Tracker - Personal Financial Dashboard",
            fontsize=14,
            fontweight="bold",
            y=0.98,
        )
        gs = gridspec.GridSpec(2, 2, figure=fig, hspace=0.45, wspace=0.35)
        ax1 = fig.add_subplot(gs[0, 0])
        ax2 = fig.add_subplot(gs[0, 1])
        ax3 = fig.add_subplot(gs[1, 0])
        ax4 = fig.add_subplot(gs[1, 1])

        months_dt = monthly["month_dt"]

        # Panel 1: Income vs Expenses + Forecast (Seaborn line plots)
        sns.lineplot(
            data=monthly,
            x="month_dt",
            y="total_income",
            ax=ax1,
            marker="o",
            color="#1D9E75",
            linewidth=2,
            label="Income",
        )
        sns.lineplot(
            data=monthly,
            x="month_dt",
            y="total_expenses",
            ax=ax1,
            marker="s",
            color="#E24B4A",
            linewidth=2,
            label="Expenses",
        )
        ax1.fill_between(
            months_dt,
            monthly["total_expenses"],
            monthly["total_income"],
            alpha=0.12,
            color="#1D9E75",
            label="Savings zone",
        )
        ax1.scatter(
            [next_month],
            [next_pred],
            color="#D85A30",
            s=140,
            zorder=5,
            marker="*",
            label=f"Forecast ${next_pred:.0f}",
        )
        ax1.set_title("Income vs Expenses", fontsize=11)
        ax1.set_ylabel("Amount ($)")
        ax1.legend(fontsize=8)
        ax1.grid(True, linestyle="--", alpha=0.4)
        ax1.tick_params(axis="x", rotation=35)

        # Panel 2: Category pie - latest month
        cat_vals = cat_series[cat_series > 0]
        cmap = plt.colormaps["Set3"]
        colors_pie = cmap(np.linspace(0, 1, len(cat_vals))).tolist()

        ax2.pie(
            cat_vals.to_numpy(dtype=float),
            labels=cat_vals.index.to_list(),
            autopct="%1.1f%%",
            colors=colors_pie,
            startangle=90,
            textprops={"fontsize": 8},
        )
        ax2.set_title(f"Spending by Category\n({latest_month_str})", fontsize=11)

        # Panel 3: Category stacked bar over time
        bottom = np.zeros(len(monthly_cat), dtype=float)

        cmap = plt.colormaps["tab10"]
        colors_bar = cmap(np.linspace(0, 1, len(monthly_cat.columns)))

        for j, cat in enumerate(monthly_cat.columns):

            vals = monthly_cat[cat].to_numpy(dtype=float)

            ax3.bar(
                monthly_cat.index,
                monthly_cat[cat],
                bottom=bottom,
                label=cat,
                color=colors_bar[j],
                alpha=0.85,
            )
            bottom += vals

        ax3.set_title("Category Spending Over Time", fontsize=11)
        ax3.set_ylabel("Amount ($)")
        ax3.legend(fontsize=7, ncol=2)
        ax3.grid(True, axis="y", linestyle="--", alpha=0.4)
        ax3.tick_params(axis="x", rotation=35)

        # Panel 4: Net Balance + savings target
        bar_colors = [
            "#1D9E75" if float(v) >= 0 else "#E24B4A" for v in monthly["net_balance"]
        ]
        ax4.bar(
            months_dt,
            monthly["net_balance"],
            color=bar_colors,
            alpha=0.85,
            width=20,
        )
        ax4.axhline(0, color="#888", lw=1.0, linestyle="--")
        savings_line = monthly["total_income"] * (self.savings_rule / 100.0)
        ax4.plot(
            months_dt,
            savings_line,
            "g--",
            lw=1.5,
            label=f"{self.savings_rule}% savings target",
        )
        ax4.set_title("Monthly Net Balance", fontsize=11)
        ax4.set_ylabel("Balance ($)")
        ax4.legend(fontsize=8)
        ax4.grid(True, axis="y", linestyle="--", alpha=0.4)
        ax4.tick_params(axis="x", rotation=35)

        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        return {
            "table_text": table_text,
            "image_path": str(output_path),
        }
