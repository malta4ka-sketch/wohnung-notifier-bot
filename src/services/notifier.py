"""
This module handles sending notifications via Telegram.
"""

import logging
import time
import urllib.parse
from typing import Dict, Any, Union

import requests

from src.core.constants import Colors, REQUEST_TIMEOUT_SECONDS
from src.core.listing import Listing

logger = logging.getLogger(__name__)

TELEGRAM_API_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"
MAX_RETRIES = 3


def escape_markdown_v2(text: Union[str, int, float]) -> str:
    """
    Escapes characters in a string to be compatible with Telegram's MarkdownV2 format.
    """
    text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(
        f'\\{char}' if char in escape_chars else char
        for char in text
    )


class TelegramNotifier:
    """Handles sending notifications via Telegram."""

    def __init__(self, telegram_config: Dict[str, Any]) -> None:
        """
        Initialize the TelegramNotifier.

        chat_id can contain one ID or multiple IDs separated by commas.

        Examples:
            "123456789"
            "123456789,987654321"
        """
        self.bot_token = telegram_config['bot_token']

        raw_chat_ids = str(telegram_config['chat_id'])

        self.chat_ids = [
            chat_id.strip()
            for chat_id in raw_chat_ids.split(',')
            if chat_id.strip()
        ]

        if not self.chat_ids:
            raise ValueError("No Telegram chat IDs configured")

        self.url = TELEGRAM_API_URL_TEMPLATE.format(
            token=self.bot_token
        )

        logger.info(
            "Telegram notifier configured for %s recipient(s)",
            len(self.chat_ids)
        )

    def send_message(self, message: str) -> None:
        """
        Sends the message to every configured Telegram chat.
        """
        for chat_id in self.chat_ids:
            self._send_to_chat(chat_id, message)

    def _send_to_chat(self, chat_id: str, message: str) -> None:
        """
        Sends a message to one Telegram chat with retry logic.
        """
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True
        }

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    self.url,
                    data=payload,
                    timeout=REQUEST_TIMEOUT_SECONDS
                )

                response.raise_for_status()

                logger.info(
                    "Telegram response for chat %s: %s",
                    chat_id,
                    response.json().get('ok', False)
                )

                return

            except requests.exceptions.HTTPError as http_err:
                if response.status_code == 429:
                    try:
                        retry_after = (
                            response.json()
                            .get('parameters', {})
                            .get('retry_after', 30)
                        )
                    except (ValueError, KeyError):
                        retry_after = 30

                    if attempt < MAX_RETRIES - 1:
                        logger.warning(
                            f"{Colors.YELLOW}"
                            f"Rate limited by Telegram for chat {chat_id}. "
                            f"Waiting {retry_after} seconds before retry "
                            f"{attempt + 2}/{MAX_RETRIES}..."
                            f"{Colors.RESET}"
                        )

                        time.sleep(retry_after)
                        continue

                    logger.error(
                        f"{Colors.RED}"
                        f"Rate limited by Telegram for chat {chat_id}. "
                        f"Max retries ({MAX_RETRIES}) exceeded."
                        f"{Colors.RESET}"
                    )

                else:
                    logger.error(
                        f"{Colors.RED}"
                        f"Telegram API Error for chat {chat_id}: "
                        f"{http_err} - {response.text}"
                        f"{Colors.RESET}"
                    )

                return

            except Exception:
                logger.exception(
                    f"{Colors.RED}"
                    f"Error sending Telegram message "
                    f"to chat {chat_id}"
                    f"{Colors.RESET}"
                )
                return

    def format_listing_message(self, listing: Listing) -> str:
        """
        Formats the details of a listing into a Telegram message.
        """
        if listing.url != 'N/A':
            escaped_link = escape_markdown_v2(listing.identifier)
            details_link = f"{escaped_link}"
        else:
            details_link = (
                f"Link not found, ID: "
                f"{escape_markdown_v2(listing.identifier)}"
            )

        google_maps_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote(listing.address)
        )

        escaped_maps_url = escape_markdown_v2(google_maps_url)

        address_line = (
            f"[{escape_markdown_v2(listing.address)}]"
            f"({escaped_maps_url})"
        )

        return (
            f"🏠 *New Listing*\n\n"
            f"📍 *Address:* {address_line}\n"
            f"🏙️ *Borough:* {escape_markdown_v2(listing.borough)}\n"
            f"📐 *Size:* {escape_markdown_v2(listing.sqm)} m²\n"
            f"💶 *Cold Rent:* {escape_markdown_v2(listing.price_cold)} €\n"
            f"💰 *Total Rent:* {escape_markdown_v2(listing.price_total)} €\n"
            f"🚪 *Rooms:* {escape_markdown_v2(listing.rooms)}\n\n"
            f"🔗 Details: {details_link}"
        )
