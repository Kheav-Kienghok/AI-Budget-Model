from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from ..db_pkg import Database
from ..external import send_json_payload
from ..utils_pkg import get_user_identifiers

logger = logging.getLogger(__name__)


async def _safe_reply(message: Message | None, text: str, **kwargs) -> None:
    """Reply to a message only when it exists.

    This avoids repeated isinstance checks.
    """

    if message is None:
        return

    await message.reply_text(text, **kwargs)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("▶️ Begin now", callback_data="begin_now")],
            [InlineKeyboardButton("📂 Send file", callback_data="send_csv")],
            [InlineKeyboardButton("📊 See insights", callback_data="see_insights")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
    )


def _welcome_text() -> str:
    lines = [
        "👋 Hi! I'm Expense Buddy.",
        "",
        "I help you keep track of your money without any tech skills.",
        "",
        "Choose how you want to start:",
        "  • Tap '▶️ Begin now' to type your expenses one by one.",
        "  • Tap '📂 Send file' if you already downloaded a file",
        "    from your bank or spreadsheet app.",
        "  • Tap '📊 See insights' later to see what your data says.",
        "",
        "Don't worry about formats – I'll guide you step by step.",
    ]
    return "\n".join(lines)


def _help_text() -> str:
    lines = [
        "❓ Two simple ways to add your money:",
        "",
        "1) Type them in:",
        "   • Tap '▶️ Begin now'.",
        "   • Then send messages like:",
        "     'Coffee, 3.50, expense'",
        "     'Salary, 1500, income'",
        "     'Gift from mom, 100'  (no type = expense)",
        "   • If you don't write 'income' or 'expense',",
        "     I'll treat it as an expense by default.",
        "   • I'll save each message as a new record.",
        "",
        "2) Send a file:",
        "   • Open your bank or spreadsheet and export your",
        "     transactions as a file.",
        "   • Come back and tap '📂 Send file' to upload it.",
    ]
    return "\n".join(lines)


def _format_insights_markdown(response: dict[str, object]) -> str:
    """Turn the /insights JSON into a friendly Markdown summary."""

    summary = response.get("summary") if isinstance(response, dict) else None
    sections = response.get("sections") if isinstance(response, dict) else None
    budget = (
        response.get("budget_recommendations") if isinstance(response, dict) else None
    )

    def fmt_money(value: object | None) -> str:
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):  # noqa: TRY003
            return "-"

    def fmt_pct(value: object | None) -> str:
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):  # noqa: TRY003
            return "-"

    lines: list[str] = ["📊 *Your money summary*"]

    if isinstance(summary, dict):
        lines.append("")
        lines.append("*Totals*")
        lines.append(f"- Income: {fmt_money(summary.get('total_income'))}")
        lines.append(f"- Expenses: {fmt_money(summary.get('total_expenses'))}")
        lines.append(f"- Net balance: {fmt_money(summary.get('net_balance'))}")
        lines.append(
            f"- Next month estimate: {fmt_money(summary.get('next_month_estimate'))}"
        )
        trend = summary.get("trend") or "-"
        lines.append(f"- Trend: {trend}")

    if isinstance(sections, dict):
        overs = sections.get("overspending_warnings") or []
        near = sections.get("near_limit_warnings") or []
        healthy = sections.get("healthy_categories") or []

        if overs:
            lines.append("")
            lines.append("*Warnings*")
            for msg in overs:
                lines.append(f"- {msg}")

        if near:
            lines.append("")
            lines.append("*Near your limits*")
            for msg in near:
                lines.append(f"- {msg}")

        if healthy:
            lines.append("")
            lines.append("*Healthy spending*")
            for msg in healthy:
                lines.append(f"- {msg}")

    if isinstance(budget, dict):
        lines.append("")
        lines.append("*Budget recommendations*")
        status = budget.get("status") or "-"
        lines.append(f"- Status: {status}")
        lines.append(
            f"- Target savings: {fmt_pct(budget.get('target_savings_pct'))}"
        )
        lines.append(
            f"- Current savings: {fmt_pct(budget.get('current_savings_pct'))}"
        )
        msgs = budget.get("messages") or []
        if msgs:
            lines.append("- Messages:")
            for msg in msgs:
                lines.append(f"  - {msg}")

    if len(lines) == 1:
        # Fallback if structure is very different.
        return str(response)

    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id, username, first_name, last_name = get_user_identifiers(update)
    db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]

    if user_id is not None and db is not None:
        try:
            db.ensure_user(user_id, username, first_name, last_name)
        except Exception:
            logger.exception("Failed to ensure user in database")

    if update.message is None:
        return

    await _safe_reply(
        update.message, _welcome_text(), reply_markup=_main_menu_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    await _safe_reply(update.message, _help_text(), reply_markup=_main_menu_keyboard())


async def handle_manual_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle free-text messages when the user is in manual entry mode.

    Expected format per message: "description, amount, type" where
    type is "income" or "expense".
    """

    if update.message is None or update.message.text is None:
        return

    text = update.message.text.strip()

    # Only act if the user chose "Begin now".
    user_data = context.user_data or {}
    if not user_data.get("manual_entry_mode"):
        await _safe_reply(
            update.message,
            "To start tracking manually, tap '▶️ Begin now' below /start, "
            "then send messages like: Coffee, 3.50, expense.",
        )
        return

    parts = [p.strip() for p in text.split(",")]
    if len(parts) not in (2, 3):
        await _safe_reply(
            update.message,
            "I didn't understand that. Please use one of these:\n"
            "  description, amount, type\n"
            "  description, amount  (I'll assume expense)\n"
            "Examples:\n  Coffee, 3.50, expense\n  Salary, 1500, income\n  Gift from mom, 100",
        )
        return

    if len(parts) == 2:
        description, amount_str = parts
        type_str = "expense"  # default when not specified
    else:
        description, amount_str, type_str = parts

    try:
        amount = float(amount_str)
    except ValueError:
        await _safe_reply(
            update.message, "The amount should be a number. Example: 12.50 or 100."
        )
        return

    t = type_str.lower()
    if t.startswith("inc"):
        kind = "income"
    elif t.startswith("exp"):
        kind = "expense"
    else:
        await _safe_reply(
            update.message,
            "The type should be 'income' or 'expense'.\n"
            "Examples:\n  Coffee, 3.50, expense\n  Salary, 1500, income",
        )
        return

    # Make expenses negative, incomes positive.
    if kind == "expense" and amount > 0:
        signed_amount = -amount
    else:
        signed_amount = amount

    db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
    user_id, _, _, _ = get_user_identifiers(update)

    if db is None or user_id is None:
        await _safe_reply(
            update.message,
            "Sorry, I couldn't save this right now. Please try again later.",
        )
        return

    try:
        db.add_expense(
            user_id=user_id,
            amount=signed_amount,
            description=description,
            category=kind,
        )
    except Exception:
        logger.exception("Failed to store manual expense row")
        await _safe_reply(
            update.message, "Something went wrong while saving. Please try again."
        )
        return

    pretty_amount = f"{signed_amount:.2f}"
    await _safe_reply(
        update.message,
        f"Saved: {description} ({pretty_amount}, {kind}). ✅\n"
        "Send another one in the same format or use the buttons again.",
    )


async def handle_button_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.callback_query is None:
        return

    query = update.callback_query
    data = (query.data or "").lower()

    await query.answer()

    message = query.message
    if message is None:
        return

    if not isinstance(message, Message):
        return

    if data == "begin_now":

        if context.user_data is None:
            context.user_data = {}

        # Turn on manual entry mode for this user.
        context.user_data["manual_entry_mode"] = True

        message = update.message or query.message
        if isinstance(message, Message):
            await _safe_reply(
                message,
                "Great, let's start now! 🎉\n\n"
                "For each expense or income, send a message like:\n"
                "  Coffee, 3.50, expense\n"
                "  Salary, 1500, income\n\n"
                "Always use this order: description, amount, type (income or expense).",
            )
    elif data == "send_csv":

        await _safe_reply(
            message,
            "📂 To send your expenses:\n"
            "1) Tap the clip/paperclip icon in this chat.\n"
            "2) Choose the file you downloaded with your transactions\n"
            "   (from your bank, wallet app, or spreadsheet).\n"
            "3) Send it to me. I'll save it for you and show insights.",
        )

    elif data == "see_insights":
        db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
        user_id, _, _, _ = get_user_identifiers(update)

        if db is None or user_id is None:

            await _safe_reply(
                message,
                "Sorry, I couldn't access your data right now. Please try again later.",
            )
            return

        rows = db.get_expenses_for_user(user_id)
        if not rows:
            await _safe_reply(
                message,
                "I don't have any data for you yet.\n"
                "You can start by typing entries or sending a file.",
            )
            return

        # Build JSON array in the format expected by the insights API:
        # [{ "date": "YYYY-MM-DD", "description": "...", "amount": 123.45, "type": "Income"|"Expense" }, ...]
        items: list[dict[str, object]] = []
        for r in rows:
            raw_amount = float(r["amount"])  # may be negative for expenses
            category = (r["category"] or "").lower()

            if category == "income":
                entry_type = "Income"
                amount = abs(raw_amount)
            else:
                entry_type = "Expense"
                amount = abs(raw_amount)

            created = str(r["created_at"]) if r["created_at"] is not None else ""
            date_only = created.split(" ")[0] if created else ""

            items.append(
                {
                    "date": date_only,
                    "description": r["description"],
                    "amount": amount,
                    "type": entry_type,
                }
            )

        payload = items

        try:
            response = await send_json_payload(payload, endpoint="/insights")
        except Exception:
            logger.exception("Failed to fetch insights from backend")

            await _safe_reply(
                message, "Sorry, something went wrong while getting your insights."
            )
            return

        if not isinstance(response, dict):
            text = "I got a response from the server, but it wasn't in the expected format."
        else:
            text = _format_insights_markdown(response)

        await _safe_reply(message, text, parse_mode="Markdown")
    elif data == "help":
        await _safe_reply(message, _help_text(), reply_markup=_main_menu_keyboard())
    elif data == "start":
        await _safe_reply(message, _welcome_text(), reply_markup=_main_menu_keyboard())
