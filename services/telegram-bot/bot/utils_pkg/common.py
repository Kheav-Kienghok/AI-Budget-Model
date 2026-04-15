from __future__ import annotations

from telegram import Update


def get_user_identifiers(update: Update) -> tuple[int | None, str | None, str | None, str | None]:
    user = update.effective_user
    if user is None:
        return None, None, None, None
    return user.id, user.username, user.first_name, user.last_name
