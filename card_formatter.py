"""Utilities for generating WhatsApp header cards with dates, Hijri calendar, and durations."""

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from hijri_converter import Gregorian
except ImportError:
    Gregorian = None

MALAYALAM_WEEKDAYS = {
    0: "തിങ്കൾ",
    1: "ചൊവ്വ",
    2: "ബുധൻ",
    3: "വ്യാഴം",
    4: "വെള്ളി",
    5: "ശനി",
    6: "ഞായർ",
}

HIJRI_MONTH_NAMES_EN = {
    1: "Muharram",
    2: "Safar",
    3: "Rabee-ul Awwal",
    4: "Rabee-ul Thani",
    5: "Jumada al-Ula",
    6: "Jumada al-Akhirah",
    7: "Rajab",
    8: "Sha'ban",
    9: "Ramadan",
    10: "Shawwal",
    11: "Dhu al-Qi'dah",
    12: "Dhu al-Hijjah",
}


def to_small_caps(text: str) -> str:
    """Convert ASCII text to unicode small caps where possible."""
    small_caps_map = {
        'A': 'ᴀ', 'B': 'ʙ', 'C': 'ᴄ', 'D': 'ᴅ', 'E': 'ᴇ', 'F': 'ғ', 'G': 'ɢ',
        'H': 'ʜ', 'I': 'ɪ', 'J': 'ᴊ', 'K': 'ᴋ', 'L': 'ʟ', 'M': 'ᴍ', 'N': 'ɴ',
        'O': 'ᴏ', 'P': 'ᴘ', 'Q': 'ǫ', 'R': 'ʀ', 'S': 's', 'T': 'ᴛ', 'U': 'ᴜ',
        'V': 'ᴠ', 'W': 'ᴡ', 'X': 'x', 'Y': 'ʏ', 'Z': 'ᴢ',
        'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ғ', 'g': 'ɢ',
        'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
        'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 's', 't': 'ᴛ', 'u': 'ᴜ',
        'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
    }
    return "".join(small_caps_map.get(ch, ch) for ch in text)

def format_whatsapp_card(
    title_malayalam: str,
    subtitle_malayalam: Optional[str] = None,
    khatib: str = "ഡോ. ശൈഖ് ഉസാമ ബിൻ അബ്ദുള്ള ഖയ്യാത്വ്",
    location: str = "മസ്ജിദുൽ ഹറാം, മക്ക",
    target_date: Optional[datetime] = None,
) -> str:
    """Format the full WhatsApp metadata card."""
    dt = target_date or datetime.now()
    gregorian_date_str = dt.strftime("%d-%m-%Y")
    weekday_ml = MALAYALAM_WEEKDAYS.get(dt.weekday(), "")

    if Gregorian:
        hijri = Gregorian(dt.year, dt.month, dt.day).to_hijri()
        hijri_year = hijri.year
        month_name = HIJRI_MONTH_NAMES_EN.get(hijri.month, f"Month {hijri.month}")
        hijri_month_str = f"{to_small_caps(month_name)} - {hijri.day:02d}"
    else:
        hijri_year = 1448
        hijri_month_str = "ʀᴀʙᴇᴇ-ᴜʟ ᴀᴡᴡᴀʟ - 22"

    card_lines = [
        "🕌 *മസ്ജിദുൽ ഹറാം ജുമുഅ ഖുതുബ* 📖",
        "🌹🌹🌹🌹",
        "",
        f"*{title_malayalam}*",
    ]
    if subtitle_malayalam:
        card_lines.append(f"_{subtitle_malayalam}_")

    card_lines.extend([
        "",
        "➖➖➖➖➖➖➖➖➖➖",
        "",
        f"_{gregorian_date_str}_  _{weekday_ml}_",
        f"ʜɪᴊʀɪ - {hijri_year}",
        hijri_month_str,
        "",
        f"⚡ ഖതീബ് : {khatib}",
        f"⚡ സ്ഥലം : {location}",
        "➖➖➖➖➖➖➖➖➖➖",
    ])

    return "\n".join(card_lines)
