from __future__ import annotations

import logging
from io import BytesIO

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ..db_pkg import Database
from ..external import fetch_binary_from_external, send_json_payload
from ..utils_pkg import get_user_identifiers

logger = logging.getLogger(__name__)


async def _safe_reply(message: Message | None, text: str, **kwargs) -> None:
    """Reply to a message only when it exists.

    This avoids repeated isinstance checks.
    """

    if message is None:
        return

    await message.reply_text(text, **kwargs)


async def _safe_edit(message: Message | None, text: str, **kwargs) -> None:
    """Edit a message safely when it exists."""

    if message is None:
        return

    await message.edit_text(text, **kwargs)


async def _safe_success_reaction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """React with a success emoji when supported by the client/API."""

    if update.effective_chat is None or update.message is None:
        return

    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji="✍️")],
        )
    except TelegramError as exc:
        # Some chats reject specific emojis for reactions. Try a common fallback.
        try:
            await context.bot.set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reaction=[ReactionTypeEmoji(emoji="👍")],
            )
        except TelegramError:
            logger.warning(
                "Could not add reaction to message %s in chat %s: %s",
                update.message.message_id,
                update.effective_chat.id,
                exc,
            )
    except Exception:
        logger.exception("Unexpected error while setting message reaction")


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 Start now", callback_data="begin_now")],
            [
                InlineKeyboardButton("📂 Upload a file", callback_data="send_csv"),
                InlineKeyboardButton("📊 View insights", callback_data="see_insights"),
            ],
            [
                InlineKeyboardButton(
                    "❓ Need help getting started?", callback_data="help"
                )
            ],
        ]
    )


def _csv_followup_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📊 Show insights", callback_data="csv_show_insights"
                )
            ],
            [
                InlineKeyboardButton(
                    "📂 Import another CSV", callback_data="csv_import_more"
                ),
                InlineKeyboardButton(
                    "🏠 Back to start", callback_data="csv_back_start"
                ),
            ],
        ]
    )


def _welcome_text() -> str:
    lines = [
        "💰 *Expense Buddy*",
        "_Your personal finance tracker_",
        "",
        "*Track your money, effortlessly.*",
        "No spreadsheet skills needed. Tell me your expenses or upload a file and I'll handle the rest.",
        "",
        "*How would you like to start?*",
        "• *🚀 Start now*: Start today and build the habit one entry at a time.",
        "• *Upload a file*: Import data from your bank (currently CSV only).",
        "• *View insights*: Charts, trends, and summaries.",
        "",
        "Tap an option below to continue.",
    ]
    return "\n".join(lines)


def _help_text() -> str:
    lines = [
        "❓ *Two simple ways to add your money*",
        "",
        "*1) Start now*",
        '- Tap *"🚀 Start now"*.',
        "- Send one record per message in this format:",
        "  `description, amount, type`",
        "",
        "*Examples*",
        "- `Coffee, 3.50, expense`",
        "- `Salary, 1500, income`",
        "- `Gift from mom, 100` (no type = expense)",
        "",
        "*Tips*",
        "- If you do not write `income` or `expense`, it defaults to `expense`.",
        "- Each message is saved as a new record.",
        "",
        "*2) Upload a file*",
        "- Export your transactions from your bank/spreadsheet.",
        '- Tap *"📂 Upload a file"* and send it here.',
        "- Supported right now: *CSV only*.",
    ]
    return "\n".join(lines)


def _csv_import_instructions_text() -> str:
    return (
        "📂 *Import your expenses from CSV*\n\n"
        "*Steps*\n"
        "1. Tap the clip/paperclip icon in this chat.\n"
        "2. Choose your transaction export file\n"
        "   (from your bank, wallet app, or spreadsheet).\n"
        "3. Send the file here.\n\n"
        "I will import your records, then ask whether you want to view insights or continue importing."
    )


def _format_insights_markdown(response: dict[str, object]) -> str:
    """Turn the /insights JSON into a polished Markdown insights report."""

    # Some APIs wrap the payload under `report`; use that as the canonical source.
    report: dict[str, object] | None = None
    if isinstance(response, dict):
        wrapped = response.get("report")
        if isinstance(wrapped, dict):
            report = wrapped
        else:
            report = response

    summary = report.get("summary") if isinstance(report, dict) else None
    sections = report.get("sections") if isinstance(report, dict) else None
    budget = report.get("budget_recommendations") if isinstance(report, dict) else None

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

    def esc_md(value: object | None) -> str:
        text = "" if value is None else str(value)
        # Escape dynamic values for Telegram Markdown parse mode.
        for ch in ("_", "*", "`", "["):
            text = text.replace(ch, f"\\{ch}")
        return text

    total_income = None
    total_expenses = None
    net_balance = None
    next_month_estimate = None
    net_balance_pct = "-"
    trend = "-"

    if isinstance(summary, dict):
        total_income = summary.get("total_income")
        total_expenses = summary.get("total_expenses")
        net_balance = summary.get("net_balance")
        next_month_estimate = summary.get("next_month_estimate")
        trend = str(summary.get("trend") or "-")

        # Try backend-provided savings %, otherwise derive from totals.
        summary_pct = (
            summary.get("net_balance_pct")
            or summary.get("savings_rate_pct")
            or summary.get("savings_pct")
        )
        if summary_pct is not None:
            net_balance_pct = fmt_pct(summary_pct)
        else:
            try:
                income = float(total_income)
                net = float(net_balance)
                net_balance_pct = f"{(net / income) * 100:.1f}%" if income > 0 else "-"
            except (TypeError, ValueError, ZeroDivisionError):  # noqa: TRY003
                net_balance_pct = "-"

    overs: list[str] = []
    near: list[str] = []
    healthy: list[str] = []

    if isinstance(sections, dict):
        overs = [str(msg) for msg in (sections.get("overspending_warnings") or [])]
        near = [str(msg) for msg in (sections.get("near_limit_warnings") or [])]
        healthy = [str(msg) for msg in (sections.get("healthy_categories") or [])]

    budget_msgs: list[str] = []
    budget_status = "-"
    target_savings = "-"
    current_savings = "-"
    if isinstance(budget, dict):
        budget_status = str(budget.get("status") or "-")
        target_savings = fmt_pct(budget.get("target_savings_pct"))
        current_savings = fmt_pct(budget.get("current_savings_pct"))
        budget_msgs = [str(msg) for msg in (budget.get("messages") or [])]

    lines: list[str] = ["📊 *SMART FINANCIAL INSIGHTS REPORT*"]

    lines.append("")
    lines.append("```bash")
    lines.append("=====================================================")
    lines.append("[SUMMARY]")
    lines.append(f"  Total Income   : {fmt_money(total_income):>12}")
    lines.append(f"  Total Expenses : {fmt_money(total_expenses):>12}")
    lines.append(
        f"  Net Balance    : {fmt_money(net_balance):>12}  ({net_balance_pct})"
    )
    lines.append(
        f"  Next Month Est.: {fmt_money(next_month_estimate):>12}  ({esc_md(trend)})"
    )
    lines.append("=====================================================")
    lines.append("```")

    if overs:
        lines.append("")
        lines.append("*🚨 Overspending Warnings*")
        for msg in overs:
            lines.append(f"- {esc_md(msg)}")

    if near:
        lines.append("")
        lines.append("*⚠️ Near Limit Warnings*")
        for msg in near:
            lines.append(f"- {esc_md(msg)}")

    if healthy:
        lines.append("")
        lines.append("*✅ Healthy Categories*")
        for msg in healthy:
            lines.append(f"- {esc_md(msg)}")
    elif not overs and not near:
        lines.append("")
        lines.append("*✅ Healthy Categories*")
        lines.append("- No category insights available yet.")

    lines.append("")
    lines.append("*💡 Budget Recommendations*")
    lines.append(f"- Status: {esc_md(budget_status)}")
    lines.append(f"- Target savings: {target_savings}")
    lines.append(f"- Current savings: {current_savings}")
    if budget_msgs:
        for msg in budget_msgs:
            lines.append(f"- {esc_md(msg)}")
    else:
        lines.append("- Keep tracking consistently for sharper recommendations.")

    lines.append("")
    lines.append("```bash")
    lines.append(
        f"Summary: {len(overs)} overspending | {len(near)} warnings | {len(healthy)} healthy"
    )
    lines.append("```")

    if not isinstance(summary, dict) and not isinstance(sections, dict):
        # Fallback if structure is very different.
        return esc_md(str(response))

    return "\n".join(lines)


def _extract_dashboard_image_url(response: dict[str, object]) -> str | None:
    """Read dashboard image URL from common backend response shapes."""

    if not isinstance(response, dict):
        return None

    dashboard = response.get("dashboard")
    if isinstance(dashboard, dict):
        image_url = dashboard.get("image_url")
        if isinstance(image_url, str) and image_url.strip():
            return image_url.strip()

    report = response.get("report")
    if isinstance(report, dict):
        report_dashboard = report.get("dashboard")
        if isinstance(report_dashboard, dict):
            image_url = report_dashboard.get("image_url")
            if isinstance(image_url, str) and image_url.strip():
                return image_url.strip()

    return None


async def _send_dashboard_image(
    message: Message | None, response: dict[str, object]
) -> None:
    """Send dashboard image attachment when present in response."""

    if message is None:
        return

    image_url = _extract_dashboard_image_url(response)
    if not image_url:
        return

    filename = image_url.strip("/").split("/")[-1] or "dashboard.png"

    try:
        image_bytes = await fetch_binary_from_external(image_url)
        image_buffer = BytesIO(image_bytes)
        image_buffer.name = filename
        await message.reply_photo(photo=image_buffer)
    except Exception:
        logger.exception("Failed to send dashboard image attachment")


async def _send_dashboard_image_with_caption(
    message: Message | None,
    response: dict[str, object],
    caption_text: str,
    parse_mode: str = "Markdown",
) -> bool:
    """Send dashboard image with report text as caption when possible.

    Returns True when image was sent with caption, False otherwise.
    """

    if message is None:
        return False

    image_url = _extract_dashboard_image_url(response)
    if not image_url:
        return False

    filename = image_url.strip("/").split("/")[-1] or "dashboard.png"
    caption = caption_text.strip()

    # Telegram caption limit is 1024 characters.
    if len(caption) > 1024:
        return False

    try:
        image_bytes = await fetch_binary_from_external(image_url)
        image_buffer = BytesIO(image_bytes)
        image_buffer.name = filename
        await message.reply_photo(
            photo=image_buffer,
            caption=caption,
            parse_mode=parse_mode,
        )
        return True
    except Exception:
        logger.exception("Failed to send dashboard image with caption")
        return False


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
        update.message,
        _welcome_text(),
        reply_markup=_main_menu_keyboard(),
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    await _safe_reply(
        update.message,
        _help_text(),
        reply_markup=_main_menu_keyboard(),
        parse_mode="Markdown",
    )


async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    await _safe_reply(
        update.message,
        _csv_import_instructions_text(),
        parse_mode="Markdown",
    )


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
    user_id, _, _, _ = get_user_identifiers(update)

    if db is None or user_id is None:
        await _safe_reply(
            update.message,
            "Sorry, I couldn't access your data right now. Please try again later.",
        )
        return

    rows = db.get_expenses_for_user(user_id)
    if not rows:
        await _safe_reply(
            update.message,
            "You don't have any data yet.\n"
            "Use /import to upload a CSV file or tap 🚀 Start now to add entries manually.\n\n"
            "Here is the quick help guide:",
        )
        await help_command(update, context)
        return

    items: list[dict[str, object]] = []
    for r in rows:
        raw_amount = float(r["amount"])
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

    try:
        response = await send_json_payload(items, endpoint="/insights")
    except Exception:
        logger.exception("Failed to fetch insights from backend")
        await _safe_reply(
            update.message,
            "Sorry, something went wrong while getting your summary.",
        )
        return

    if not isinstance(response, dict):
        text = "I got a response from the server, but it wasn't in the expected format."
    else:
        text = _format_insights_markdown(response)

    sent_with_caption = False
    if isinstance(response, dict):
        sent_with_caption = await _send_dashboard_image_with_caption(
            update.message,
            response,
            text,
            parse_mode="Markdown",
        )

    if not sent_with_caption:
        if isinstance(response, dict):
            await _send_dashboard_image(update.message, response)
        await _safe_reply(update.message, text, parse_mode="Markdown")


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

    # Only act if the user chose "Start now".
    user_data = context.user_data or {}
    if not user_data.get("manual_entry_mode"):
        await _safe_reply(
            update.message,
            "To start tracking manually, tap '🚀 Start now' below /start, "
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

    await _safe_success_reaction(update, context)

    # Keep the chat clean: show this guidance once, then use reactions only.
    if not context.user_data.get("save_success_notice_sent"):
        context.user_data["save_success_notice_sent"] = True
        await _safe_reply(
            update.message,
            "🎉 Nice one! Your transaction is safely recorded.\n\n"
            "Want to see your insights? Type /summary, or tap the *View insights* button.\n"
            "Need to add more? Tap *Start now* or *Upload a file*.\n\n"
            "From here on, I’ll keep things clean with a quick reaction for each save.",
            parse_mode="Markdown",
        )


async def handle_button_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.callback_query is None:
        return

    query = update.callback_query
    data = (query.data or "").lower()
    message = query.message
    if message is None:
        return

    if not isinstance(message, Message):
        return

    if context.user_data is None:
        context.user_data = {}

    if context.user_data.get("action_in_progress"):
        await query.answer(
            "⏳ The app is processing your previous action. Please wait."
        )
        return

    # Lock immediately before any await to prevent double-trigger on rapid taps.
    context.user_data["action_in_progress"] = True
    status_message: Message | None = None

    try:
        await query.answer()
        status_message = await message.reply_text(
            "⏳ The app is processing your request..."
        )

        if data == "begin_now":

            # Turn on manual entry mode for this user.
            context.user_data["manual_entry_mode"] = True

            await _safe_edit(
                status_message,
                "🚀 *Awesome, you're all set!*\n\n"
                "Send one transaction per message using:\n"
                "`description, amount, type`\n"
                "(or else my tiny robot brain will panic and pretend it's Monday all over again 🤖)\n\n"
                "*Examples*\n"
                "- `Coffee, 3.50, expense`\n"
                "- `Salary, 1500, income`\n"
                "- `Lunch, 8.25` (type is optional; defaults to `expense`)\n\n"
                "Send your first entry now.",
                parse_mode="Markdown",
            )
        elif data == "send_csv":

            await _safe_edit(
                status_message,
                _csv_import_instructions_text(),
                parse_mode="Markdown",
            )

        elif data == "see_insights":
            db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
            user_id, _, _, _ = get_user_identifiers(update)

            if db is None or user_id is None:

                await _safe_edit(
                    status_message,
                    "Sorry, I couldn't access your data right now. Please try again later.",
                )
                return

            rows = db.get_expenses_for_user(user_id)
            if not rows:
                await _safe_edit(
                    status_message,
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

                await _safe_edit(
                    status_message,
                    "Sorry, something went wrong while getting your insights.",
                )
                return

            if not isinstance(response, dict):
                text = "I got a response from the server, but it wasn't in the expected format."
            else:
                text = _format_insights_markdown(response)

            sent_with_caption = False
            if isinstance(response, dict):
                sent_with_caption = await _send_dashboard_image_with_caption(
                    message,
                    response,
                    text,
                    parse_mode="Markdown",
                )

            if not sent_with_caption:
                if isinstance(response, dict):
                    await _send_dashboard_image(message, response)
                await _safe_reply(message, text, parse_mode="Markdown")

            await _safe_edit(status_message, "✅ Done. Your insights are ready.")
        elif data == "csv_show_insights":
            response = None
            if context.user_data is not None:
                response = context.user_data.get("pending_csv_insights")

            if not isinstance(response, dict):
                await _safe_edit(
                    status_message,
                    "No recent CSV insights found. Please upload a CSV file first.",
                    reply_markup=_main_menu_keyboard(),
                )
                return

            text = _format_insights_markdown(response)
            sent_with_caption = await _send_dashboard_image_with_caption(
                message,
                response,
                text,
                parse_mode="Markdown",
            )
            if not sent_with_caption:
                await _send_dashboard_image(message, response)
                await _safe_reply(message, text, parse_mode="Markdown")

            await _safe_edit(
                status_message,
                "✅ Done. CSV insights are ready. What would you like to do next?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "📂 Import another CSV", callback_data="csv_import_more"
                            ),
                            InlineKeyboardButton(
                                "🏠 Back to start", callback_data="csv_back_start"
                            ),
                        ]
                    ]
                ),
            )
        elif data == "csv_import_more":
            await _safe_edit(
                status_message,
                "Great, send your next CSV file now.\n"
                "Supported format at the moment: .csv",
            )
        elif data == "csv_back_start":
            if context.user_data is not None:
                context.user_data.pop("pending_csv_insights", None)
            await _safe_edit(
                status_message,
                _welcome_text(),
                reply_markup=_main_menu_keyboard(),
                parse_mode="Markdown",
            )
        elif data == "help":
            await _safe_edit(
                status_message,
                _help_text(),
                reply_markup=_main_menu_keyboard(),
                parse_mode="Markdown",
            )
        elif data == "start":
            await _safe_edit(
                status_message,
                _welcome_text(),
                reply_markup=_main_menu_keyboard(),
                parse_mode="Markdown",
            )
        else:
            await _safe_edit(status_message, "I couldn't recognize that action.")
    finally:
        if context.user_data is not None:
            context.user_data["action_in_progress"] = False
