from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import SupportsFloat

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

_ALLOWED_RULES_ORDER = (
    "Food",
    "Transportation",
    "Entertainment",
    "Utilities",
    "Rent",
    "Other",
)

_RULE_KEY_MAP = {
    "food": "Food",
    "transportation": "Transportation",
    "entertainment": "Entertainment",
    "utilities": "Utilities",
    "rent": "Rent",
    "other": "Other",
}


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


def _customization_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for asking if user wants to customize budget & saving rules."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Yes, let's customize!", callback_data="customize_yes"
                )
            ],
            [InlineKeyboardButton("⏭️ Skip for now", callback_data="customize_no")],
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
        "- Copy this template and replace the values:",
        "  `[description], [amount], [type]`",
        "- `type` is optional (defaults to `expense`).",
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
        "",
        "*3) Update budget and savings rules*",
        "- Use */rules* to view your current rules.",
        "- Use this format to update all rules:",
        "  `/rules food=20 transportation=8 entertainment=5 utilities=8 rent=25 other=7 savings=20`",
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


def _manual_entry_template_text() -> str:
    return (
        "*Template (copy, fill, send)*\n"
        "`description, amount, type`\n"
        "_Type is optional. If omitted, it defaults to expense._\n\n"
        "*Examples*\n"
        "- `Coffee, 3.50, expense`\n"
        "- `Salary, 1500, income`\n"
        "- `Lunch, 8.25`"
    )


def _get_default_budget_rules() -> tuple[dict[str, float], float]:
    """Return default budget rules and savings rule.

    Returns:
        A tuple of (budget_rules_dict, savings_rule_percentage)
    """
    default_budget_rules = {
        "Food": 20.0,
        "Transportation": 8.0,
        "Entertainment": 5.0,
        "Utilities": 8.0,
        "Rent": 25.0,
        "Other": 7.0,
    }
    default_savings_rule = 20.0
    return default_budget_rules, default_savings_rule


def _format_rules_text(budget_rules: dict[str, float], savings_rule: float) -> str:
    total_budget = 0.0
    lines = [
        "📐 *Your Rules*",
        "",
        "*Budget Rules (%)*",
    ]

    for key in _ALLOWED_RULES_ORDER:
        value = float(budget_rules.get(key, 0.0))
        total_budget += value
        lines.append(f"• {key}: {value:.1f}%")

    lines.extend(
        [
            "",
            f"*Savings Rule*: {float(savings_rule):.1f}%",
            f"*Total (Budget + Savings)*: {total_budget + float(savings_rule):.1f}%",
            "",
            "*Update Template (copy, fill, send)*",
            "`/rules food=[%] transportation=[%] entertainment=[%] utilities=[%] rent=[%] other=[%] savings=[%]`",
            "",
            "*Example*",
            "`/rules food=25 transportation=8 entertainment=10 utilities=10 rent=25 other=10 savings=12`",
        ]
    )

    return "\n".join(lines)


def _parse_rules_args(args: list[str]) -> tuple[dict[str, float], float] | str:
    parsed_budget: dict[str, float] = {}
    savings_rule: float | None = None

    for raw in args:
        token = raw.strip().rstrip(",")
        if not token:
            continue

        if "=" in token:
            key_raw, value_raw = token.split("=", 1)
        elif ":" in token:
            key_raw, value_raw = token.split(":", 1)
        else:
            return (
                "Invalid format. Use key=value pairs, for example: "
                "food=20 transportation=8 ... savings=20"
            )

        key = key_raw.strip().strip("\"'").lower()
        value_text = value_raw.strip().strip("\"'").rstrip("%")

        try:
            value = float(value_text)
        except ValueError:
            return (
                f"Invalid numeric value for '{key_raw.strip()}': '{value_raw.strip()}'."
            )

        if value < 0 or value > 100:
            return f"Value for '{key_raw.strip()}' must be between 0 and 100."

        if key in ("saving", "savings", "savings_rule"):
            savings_rule = value
            continue

        canonical = _RULE_KEY_MAP.get(key)
        if canonical is None:
            allowed = ", ".join(_RULE_KEY_MAP.keys())
            return f"Unsupported rule key '{key_raw.strip()}'. Allowed keys: {allowed}, savings."

        parsed_budget[canonical] = value

    missing_rules = [name for name in _ALLOWED_RULES_ORDER if name not in parsed_budget]
    if missing_rules:
        return (
            "You must provide all budget categories: "
            + ", ".join(_ALLOWED_RULES_ORDER)
            + "."
        )

    if savings_rule is None:
        return "You must provide savings, e.g. savings=20."

    total = sum(parsed_budget.values()) + savings_rule
    if total > 100:
        return f"Total budget + savings is {total:.1f}%. It must not exceed 100%."

    if total < 0:
        return "Total budget + savings must be 0 or greater."

    return parsed_budget, savings_rule


def _customization_offer_text(first_name: str | None = None) -> str:
    """Text offering first-time users to customize budget & saving rules."""
    name = first_name or "there"
    lines = [
        "🎯 *Personalize Your Budget & Savings Goals?*",
        "",
        f"Welcome, {name}! I'm excited to help you manage your finances. 💪",
        "",
        "Before we get started, would you like to customize your:",
        "• *Budget Rules* - Set spending percentages per category (e.g., 'Food = 25%')",
        "• *Saving Rules* - Define savings goals and targets (e.g., 'Save 20% of income')",
        "",
        "*Quick setup* (2-3 minutes) → Tailored insights just for you ✨",
        "",
        "Or skip for now and use *default settings* → You can customize anytime!",
    ]
    return "\n".join(lines)


def _customization_started_text() -> str:
    """Text shown when user chooses to customize."""
    lines = [
        "✅ *Great! Let's personalize your rules*",
        "",
        "I'll guide you through a few quick questions:",
        "",
        "**Step 1: Budget Rules** 💰",
        "Tell me your budget percentages by category:",
        "• Food",
        "• Transportation",
        "• Entertainment",
        "• Rent",
        "• Utilities",
        "• Other",
        "",
        "*Template*",
        "`Category Percent%`",
        "",
        "*Copy-ready example (all categories)*",
        "`Food 25%`",
        "`Transportation 8%`",
        "`Entertainment 10%`",
        "`Rent 25%`",
        "`Utilities 10%`",
        "`Other 10%`",
        "",
        "_I will store only the number (for example, `25` from `Food 25%`)._",
        "_You can also use: `category, percent` (e.g., `Food, 25`)._",
        "_Or type 'skip' to use defaults_",
        "",
        "*What category would you like to set a limit for?*",
        "_(Or type 'done' to move to savings rules)_",
    ]
    return "\n".join(lines)


def _customization_savings_prompt_text(budget_rules: dict[str, float]) -> str:
    total_budget = sum(float(v) for v in budget_rules.values())
    remaining = max(0.0, 100.0 - total_budget)
    lines = [
        "✅ *Budget rules captured.*",
        "",
        "**Step 2: Savings Rule** 🎯",
        f"Current budget total: *{total_budget:.1f}%*",
        f"Remaining available (max savings): *{remaining:.1f}%*",
        "",
        "Reply with savings percentage, for example:",
        "- `20`",
        "- `savings, 20`",
        "- `rest` (auto-use the remaining percentage)",
    ]
    return "\n".join(lines)


def _parse_category_percent_input(text: str) -> tuple[str, float] | str:
    cleaned = text.strip()
    category_raw = ""
    value_raw = ""

    # Support both "Food, 25" and "Food 25%".
    if "," in cleaned:
        parts = [p.strip() for p in cleaned.split(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return "Invalid format. Use `Food 25%` or `Food, 25`."
        category_raw, value_raw = parts
    else:
        match = re.match(r"^(.+?)\s+(-?\d+(?:\.\d+)?)\s*%?$", cleaned)
        if match is None:
            return "Invalid format. Use `Food 25%` or `Food, 25`."
        category_raw = match.group(1).strip()
        value_raw = match.group(2).strip()

    category_key = category_raw.strip().lower()
    category = _RULE_KEY_MAP.get(category_key)
    if category is None:
        return (
            "Invalid category. Use one of: Food, Transportation, "
            "Entertainment, Utilities, Rent, Other."
        )

    try:
        value = float(value_raw.strip().rstrip("%"))
    except ValueError:
        return "Percent must be a number, for example `Food 25%`."

    if value < 0 or value > 100:
        return "Percent must be between 0 and 100."

    return category, value


def _parse_budget_entries_input(text: str) -> list[tuple[str, float]] | str:
    """Parse one or many budget entries from a user message.

    Supported examples:
    - Food 25%
    - Food, 25
    - Food 25\nTransportation 8\nRent 25
    """

    entries: list[tuple[str, float]] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "Please send at least one category and percentage."

    for line_no, line in enumerate(lines, start=1):
        parsed = _parse_category_percent_input(line)
        if isinstance(parsed, str):
            return f"Line {line_no}: {parsed}"
        entries.append(parsed)

    return entries


def _budget_progress_text(
    budget_rules: dict[str, float], touched_categories: set[str]
) -> str:
    entered_total = sum(float(budget_rules.get(k, 0.0)) for k in touched_categories)
    full_total = sum(float(budget_rules.get(k, 0.0)) for k in _ALLOWED_RULES_ORDER)
    return (
        f"Your entered categories total: *{entered_total:.1f}%*.\n"
        f"\nFull budget total (including defaults for untouched categories): *{full_total:.1f}%*."
    )


def _parse_savings_input(text: str) -> float | str:
    value_text = text.strip()
    if "," in value_text:
        parts = [p.strip() for p in value_text.split(",")]
        if len(parts) == 2 and parts[0].lower() in {
            "saving",
            "savings",
            "savings_rule",
        }:
            value_text = parts[1]

    try:
        value = float(value_text.rstrip("%"))
    except ValueError:
        return "Savings must be a number, for example `20` or `savings, 20`."

    if value < 0 or value > 100:
        return "Savings must be between 0 and 100."

    return value


def _default_rules_text() -> str:
    """Text shown when user skips customization and uses defaults."""
    lines = [
        "✅ *No problem!*",
        "",
        "I'll start you with **smart default settings**:",
        "",
        "**Default Budget Rules (Percentages)** 📊",
        "• Food: 20%",
        "• Transportation: 8%",
        "• Entertainment: 5%",
        "• Utilities: 8%",
        "• Rent: 25%",
        "• Other: 7%",
        "",
        "**Default Saving Rules** 🎯",
        "• Savings Target: 20% of monthly income",
        "",
        "💡 *You can adjust these anytime!* Just ask me to update your rules.",
        "",
        "Ready to start? Let's go! 🚀",
    ]
    return "\n".join(lines)


def _build_insights_payload(
    transactions: list[dict],
    expenses: list[dict],
    budget_rules: dict[str, float] | None = None,
    savings_rule: float | None = None,
) -> dict[str, object]:
    """Build insights payload from transactions, expenses, and budget rules.

    Returns a dictionary with:
    - transactions: list of transaction items
    - budget_rules: dictionary of category budgets
    - savings_rule: savings percentage target

    Rules:
    - From transactions: use date, description, amount, type (lowercase).
    - From expenses: map category -> type (lowercase), and use created_at date.
    """

    items: list[dict[str, object]] = []

    for t in transactions:
        raw_type = str(t.get("type") or "expense").strip().lower()
        entry_type = raw_type if raw_type else "expense"

        amount_val = t.get("amount")
        amount = float(amount_val) if amount_val not in (None, "") else 0.0

        items.append(
            {
                "date": str(t.get("date") or ""),
                "description": t.get("description") or "",
                "amount": abs(amount),
                "type": entry_type,
            }
        )

    for r in expenses:
        category = str(r.get("category") or "").strip().lower()

        amount_val = r.get("amount")
        raw_amount = float(amount_val) if amount_val not in (None, "") else 0.0

        if category:
            entry_type = category
        else:
            entry_type = "income" if raw_amount > 0 else "expense"

        created = str(r.get("created_at") or "")
        date_only = created.split(" ")[0] if created else ""

        items.append(
            {
                "date": date_only,
                "description": r.get("description") or "",
                "amount": abs(raw_amount),
                "type": entry_type,
            }
        )

    # Build the complete payload
    default_budget_rules, default_savings_rule = _get_default_budget_rules()
    payload: dict[str, object] = {
        "transactions": items,
        "budget_rules": budget_rules or default_budget_rules,
        "savings_rule": (
            savings_rule if savings_rule is not None else default_savings_rule
        ),
    }

    return payload


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

    def fmt_money(value: SupportsFloat | str | None) -> str:
        if value is None:
            return "-"
        try:
            return f"${float(value):,.2f}"
        except (TypeError, ValueError):
            return "-"

    def fmt_pct(value: SupportsFloat | str | None) -> str:
        if value is None:
            return "-"
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
                income = float(total_income) if total_income is not None else 0.0
                net = float(net_balance) if net_balance is not None else 0.0
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

    if context.user_data is None:
        context.user_data = {}

    if not context.user_data.get("customization_handled"):
        context.user_data["customization_handled"] = True
        await _safe_reply(
            update.message,
            _customization_offer_text(first_name),
            reply_markup=_customization_keyboard(),
            parse_mode="Markdown",
        )
    else:
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

    # Get both transactions (from CSV) and expenses (manual entries).
    transactions = db.get_transactions_for_user(user_id)
    expenses = db.get_expenses_for_user(user_id)

    if not transactions and not expenses:
        await _safe_reply(
            update.message,
            "You don't have any data yet.\n"
            "Use /import to upload a CSV file or tap 🚀 Start now to add entries manually.\n\n"
            "Here is the quick help guide:",
        )
        await help_command(update, context)
        return

    # Fetch user's budget rules
    budget_rules_data = db.get_budget_rules(user_id)
    budget_rules, savings_rule = _get_default_budget_rules()
    if budget_rules_data:
        budget_rules, savings_rule = budget_rules_data

    items = _build_insights_payload(transactions, expenses, budget_rules, savings_rule)

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


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
    user_id, _, first_name, last_name = get_user_identifiers(update)

    if db is None or user_id is None:
        await _safe_reply(
            update.message,
            "Sorry, I couldn't access your data right now. Please try again later.",
        )
        return

    # Ensure user exists so rules can always be inserted.
    try:
        username = update.effective_user.username if update.effective_user else None
        db.ensure_user(user_id, username, first_name, last_name)
    except Exception:
        logger.exception("Failed to ensure user before updating rules")

    args = context.args or []
    if not args:
        rules = db.get_budget_rules(user_id)
        if rules is None:
            budget_rules, savings_rule = _get_default_budget_rules()
            db.set_budget_rules(user_id, budget_rules, savings_rule)
        else:
            budget_rules, savings_rule = rules

        await _safe_reply(
            update.message,
            _format_rules_text(budget_rules, savings_rule),
            parse_mode="Markdown",
        )
        return

    parsed = _parse_rules_args(args)
    if isinstance(parsed, str):
        await _safe_reply(
            update.message,
            "❌ "
            + parsed
            + "\n\n"
            + "Use this format:\n"
            + "`/rules food=20 transportation=8 entertainment=5 utilities=8 rent=25 other=7 savings=20`",
            parse_mode="Markdown",
        )
        return

    budget_rules, savings_rule = parsed

    try:
        db.set_budget_rules(user_id, budget_rules, savings_rule)
    except Exception:
        logger.exception("Failed to save rules")
        await _safe_reply(
            update.message,
            "Sorry, something went wrong while saving your rules.",
        )
        return

    await _safe_reply(
        update.message,
        "✅ Rules updated successfully.\n\n"
        + _format_rules_text(budget_rules, savings_rule),
        parse_mode="Markdown",
    )


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

    # Fallback: sometimes slash commands arrive as plain text (no command entity).
    # Route /rules manually so users still get the correct behavior.
    tokens = text.split()
    if tokens and tokens[0].startswith("/"):
        command_token = tokens[0].split("@", 1)[0].lower()
        if command_token == "/rules":
            previous_args = context.args
            context.args = tokens[1:]
            try:
                await rules_command(update, context)
            finally:
                context.args = previous_args
            return

        # For any other slash command text, do not treat it as manual transaction input.
        return

    user_data = context.user_data or {}
    if user_data.get("customization_in_progress"):
        db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
        user_id, _, first_name, last_name = get_user_identifiers(update)

        if db is None or user_id is None:
            await _safe_reply(
                update.message,
                "Sorry, I couldn't update your rules right now. Please try again later.",
            )
            return

        mode = str(user_data.get("customization_stage") or "budget")
        current_budget = user_data.get("custom_budget_rules")
        if not isinstance(current_budget, dict):
            defaults, _ = _get_default_budget_rules()
            current_budget = defaults.copy()
            user_data["custom_budget_rules"] = current_budget

        lowered = text.lower().strip()
        if lowered == "skip":
            default_budget, default_savings = _get_default_budget_rules()
            try:
                db.ensure_user(
                    user_id,
                    update.effective_user.username if update.effective_user else None,
                    first_name,
                    last_name,
                )
                db.set_budget_rules(user_id, default_budget, default_savings)
            except Exception:
                logger.exception("Failed to set default rules from customization flow")
                await _safe_reply(
                    update.message,
                    "Sorry, something went wrong while saving defaults.",
                )
                return

            user_data.pop("customization_in_progress", None)
            user_data.pop("customization_stage", None)
            user_data.pop("custom_budget_rules", None)
            user_data.pop("custom_budget_touched_categories", None)
            user_data.pop("awaiting_missing_budget_defaults_confirmation", None)
            await _safe_reply(
                update.message, _default_rules_text(), parse_mode="Markdown"
            )
            await _safe_reply(
                update.message,
                _welcome_text(),
                reply_markup=_main_menu_keyboard(),
                parse_mode="Markdown",
            )
            return

        if mode == "budget":
            if user_data.get("awaiting_missing_budget_defaults_confirmation"):
                if lowered in {"yes", "y", "default", "use default", "ok", "okay"}:
                    user_data.pop("awaiting_missing_budget_defaults_confirmation", None)
                    user_data["customization_stage"] = "savings"
                    remaining = max(
                        0.0,
                        100.0
                        - sum(
                            float(current_budget.get(k, 0.0))
                            for k in _ALLOWED_RULES_ORDER
                        ),
                    )
                    await _safe_reply(
                        update.message,
                        "✅ Great, I will keep default values for the missing categories.\n"
                        f"The remaining *{remaining:.1f}%* can go to savings (type `rest`).\n\n"
                        + _customization_savings_prompt_text(current_budget),
                        parse_mode="Markdown",
                    )
                    return

                # If user sends categories instead of "yes", accept them directly.
                pending_entries = _parse_budget_entries_input(text)
                if not isinstance(pending_entries, str):
                    updated_budget = {
                        key: float(current_budget.get(key, 0.0))
                        for key in _ALLOWED_RULES_ORDER
                    }
                    for category, percent in pending_entries:
                        updated_budget[category] = percent

                    budget_total = sum(updated_budget.values())
                    if budget_total > 100:
                        await _safe_reply(
                            update.message,
                            (
                                f"❌ Budget total becomes *{budget_total:.1f}%* which exceeds 100%. "
                                "Please lower some categories."
                            ),
                            parse_mode="Markdown",
                        )
                        return

                    user_data["custom_budget_rules"] = updated_budget
                    touched_raw = user_data.get(
                        "custom_budget_touched_categories", set()
                    )
                    touched = (
                        set(touched_raw) if isinstance(touched_raw, set) else set()
                    )
                    touched.update(category for category, _ in pending_entries)
                    user_data["custom_budget_touched_categories"] = touched
                    user_data.pop("awaiting_missing_budget_defaults_confirmation", None)
                    progress_text = _budget_progress_text(updated_budget, touched)

                    if len(pending_entries) == 1:
                        category, percent = pending_entries[0]
                        reply_text = (
                            f"✅ *{category}* set to *{percent:.1f}%*.\n"
                            + progress_text
                            + "\n"
                            "Send another entry like `Food 25%`, or type `done` for savings."
                        )
                    else:
                        updated_lines = [
                            f"- *{category}*: *{percent:.1f}%*"
                            for category, percent in pending_entries
                        ]
                        reply_text = (
                            "✅ *Budget categories updated:*\n\n"
                            + "\n".join(updated_lines)
                            + "\n"
                            + progress_text
                            + "\n\n"
                            + "Send more entries (you can send multiple lines), or type `done` for savings."
                        )

                    await _safe_reply(update.message, reply_text, parse_mode="Markdown")
                    return

                user_data.pop("awaiting_missing_budget_defaults_confirmation", None)
                await _safe_reply(
                    update.message,
                    "No problem. Please send the missing categories now.\n"
                    "Format: `Food 25%` or `Food, 25`\n"
                    "You can send multiple lines in one message.",
                    parse_mode="Markdown",
                )
                return

            if lowered == "done":
                touched_raw = user_data.get("custom_budget_touched_categories", set())
                touched = set(touched_raw) if isinstance(touched_raw, set) else set()
                missing_categories = [
                    category
                    for category in _ALLOWED_RULES_ORDER
                    if category not in touched
                ]

                if missing_categories:
                    user_data["awaiting_missing_budget_defaults_confirmation"] = True
                    remaining = max(
                        0.0,
                        100.0
                        - sum(
                            float(current_budget.get(k, 0.0))
                            for k in _ALLOWED_RULES_ORDER
                        ),
                    )
                    await _safe_reply(
                        update.message,
                        "⚠️ You have not set all categories yet.\n"
                        f"Missing: *{', '.join(missing_categories)}*\n\n"
                        "Reply `yes` to use default values for missing categories,\n"
                        "or send the missing categories now.\n"
                        f"If you use defaults, the remaining *{remaining:.1f}%* can go to savings.",
                        parse_mode="Markdown",
                    )
                    return

                user_data["customization_stage"] = "savings"
                await _safe_reply(
                    update.message,
                    _customization_savings_prompt_text(current_budget),
                    parse_mode="Markdown",
                )
                return

            parsed_budget_entries = _parse_budget_entries_input(text)
            if isinstance(parsed_budget_entries, str):
                await _safe_reply(
                    update.message,
                    f"❌ {parsed_budget_entries}",
                    parse_mode="Markdown",
                )
                return

            updated_budget = {
                key: float(current_budget.get(key, 0.0)) for key in _ALLOWED_RULES_ORDER
            }
            for category, percent in parsed_budget_entries:
                updated_budget[category] = percent

            budget_total = sum(updated_budget.values())
            if budget_total > 100:
                await _safe_reply(
                    update.message,
                    (
                        f"❌ Budget total becomes *{budget_total:.1f}%* which exceeds 100%. "
                        "Please lower some categories."
                    ),
                    parse_mode="Markdown",
                )
                return

            user_data["custom_budget_rules"] = updated_budget
            touched_raw = user_data.get("custom_budget_touched_categories", set())
            touched = set(touched_raw) if isinstance(touched_raw, set) else set()
            touched.update(category for category, _ in parsed_budget_entries)
            user_data["custom_budget_touched_categories"] = touched
            user_data.pop("awaiting_missing_budget_defaults_confirmation", None)
            progress_text = _budget_progress_text(updated_budget, touched)

            if len(parsed_budget_entries) == 1:
                category, percent = parsed_budget_entries[0]
                reply_text = (
                    f"✅ *{category}* set to *{percent:.1f}%*.\n" + progress_text + "\n"
                    "Send another entry like `Food 25%`, or type `done` for savings."
                )
            else:
                updated_lines = [
                    f"- *{category}*: *{percent:.1f}%*"
                    for category, percent in parsed_budget_entries
                ]
                reply_text = (
                    "✅ *Budget categories updated:*\n\n"
                    + "\n".join(updated_lines)
                    + "\n"
                    + progress_text
                    + "\n\n"
                    + "Send more entries (you can send multiple lines), or type `done` for savings."
                )

            await _safe_reply(update.message, reply_text, parse_mode="Markdown")
            return

        if mode == "savings":
            budget_total = sum(
                float(current_budget.get(k, 0.0)) for k in _ALLOWED_RULES_ORDER
            )
            remaining_savings = max(0.0, 100.0 - budget_total)

            normalized_savings_text = lowered.replace("%", "").strip()
            if normalized_savings_text in {
                "rest",
                "remaining",
                "the rest",
                "all",
                "max",
            }:
                savings_value = remaining_savings
            else:
                parsed_savings = _parse_savings_input(text)
                if isinstance(parsed_savings, str):
                    await _safe_reply(
                        update.message,
                        f"❌ {parsed_savings}",
                        parse_mode="Markdown",
                    )
                    return
                savings_value = parsed_savings

            grand_total = budget_total + savings_value
            if grand_total > 100:
                await _safe_reply(
                    update.message,
                    (
                        f"❌ Budget + savings is *{grand_total:.1f}%* (max 100%).\n"
                        f"Please enter savings <= *{max(0.0, 100.0 - budget_total):.1f}%*."
                    ),
                    parse_mode="Markdown",
                )
                return

            try:
                db.ensure_user(
                    user_id,
                    update.effective_user.username if update.effective_user else None,
                    first_name,
                    last_name,
                )
                db.set_budget_rules(user_id, current_budget, savings_value)
            except Exception:
                logger.exception("Failed to save customized rules")
                await _safe_reply(
                    update.message,
                    "Sorry, something went wrong while saving your rules.",
                )
                return

            user_data.pop("customization_in_progress", None)
            user_data.pop("customization_stage", None)
            user_data.pop("custom_budget_rules", None)
            user_data.pop("custom_budget_touched_categories", None)
            user_data.pop("awaiting_missing_budget_defaults_confirmation", None)

            await _safe_reply(
                update.message,
                "✅ *Your rules are saved successfully!*\n\n"
                + _format_rules_text(current_budget, savings_value),
                parse_mode="Markdown",
            )
            await _safe_reply(
                update.message,
                _welcome_text(),
                reply_markup=_main_menu_keyboard(),
                parse_mode="Markdown",
            )
            return

    # Only act if the user chose "Start now".
    if not user_data.get("manual_entry_mode"):
        await _safe_reply(
            update.message,
            "To start tracking manually, tap '🚀 Start now' below /start, "
            "then send messages using this format:\n\n" + _manual_entry_template_text(),
        )
        return

    parts = [p.strip() for p in text.split(",")]
    if len(parts) not in (2, 3):
        await _safe_reply(
            update.message,
            "I didn't understand that.\n\n" + _manual_entry_template_text(),
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
            + _manual_entry_template_text(),
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
    if context.user_data is not None and not context.user_data.get(
        "save_success_notice_sent"
    ):
        if context.user_data is not None:
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

        if data == "customize_yes":
            context.user_data["customization_in_progress"] = True
            context.user_data["customization_stage"] = "budget"
            context.user_data["manual_entry_mode"] = False
            context.user_data["custom_budget_touched_categories"] = set()
            context.user_data.pop("awaiting_missing_budget_defaults_confirmation", None)
            user_id, _, _, _ = get_user_identifiers(update)
            db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
            if user_id is not None and db is not None:
                default_budget_rules, default_savings_rule = _get_default_budget_rules()
                context.user_data["custom_budget_rules"] = default_budget_rules.copy()
                try:
                    db.set_budget_rules(
                        user_id, default_budget_rules, default_savings_rule
                    )
                except Exception:
                    logger.exception("Failed to set budget rules during customization")
            await _safe_edit(
                status_message,
                _customization_started_text(),
                parse_mode="Markdown",
            )
        elif data == "customize_no":
            user_id, _, _, _ = get_user_identifiers(update)
            db: Database | None = context.application.bot_data.get("db")  # type: ignore[assignment]
            if user_id is not None and db is not None:
                default_budget_rules, default_savings_rule = _get_default_budget_rules()
                try:
                    db.set_budget_rules(
                        user_id, default_budget_rules, default_savings_rule
                    )
                except Exception:
                    logger.exception("Failed to set default budget rules")
            await _safe_edit(
                status_message,
                _default_rules_text(),
                parse_mode="Markdown",
            )
            await message.reply_text(
                _welcome_text(),
                reply_markup=_main_menu_keyboard(),
                parse_mode="Markdown",
            )
        elif data == "begin_now":

            # Turn on manual entry mode for this user.
            context.user_data["manual_entry_mode"] = True

            await _safe_edit(
                status_message,
                "🚀 *Awesome, you're all set!*\n\n"
                "🧾 *One transaction per message*\n"
                "Use this format:\n"
                "`description, amount, type`\n"
                "_or my tiny robot brain will panic and pretend it's Monday all over again 🤖_\n\n"
                + _manual_entry_template_text()
                + "\n\n"
                "*Send your first entry now.*",
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

            # Get both transactions (from CSV) and expenses (manual entries)
            transactions = db.get_transactions_for_user(user_id)
            expenses = db.get_expenses_for_user(user_id)

            if not transactions and not expenses:
                await _safe_edit(
                    status_message,
                    "I don't have any data for you yet.\n"
                    "You can start by typing entries or sending a file.",
                )
                return

            # Fetch user's budget rules
            budget_rules_data = db.get_budget_rules(user_id)
            budget_rules, savings_rule = _get_default_budget_rules()
            if budget_rules_data:
                budget_rules, savings_rule = budget_rules_data

            payload = _build_insights_payload(
                transactions, expenses, budget_rules, savings_rule
            )

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
            if context.user_data is not None:
                context.user_data.pop("pending_csv_insights", None)

            await _safe_edit(
                status_message,
                "*Nice, you are all set for another import*\n"
                "Send your next CSV file when you are ready.\n\n"
                "*Supported format:* CSV (.csv)",
                parse_mode="Markdown",
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
