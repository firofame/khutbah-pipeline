#!/usr/bin/env python3
"""Unified Friday Jumma Khutbah Translation, TTS, and WhatsApp Dispatcher.

Usage:
    python pipeline.py --input khutbah_arabic.txt [OPTIONS]

Options:
    --input, -i PATH         Path to raw Arabic text/transcript file (Required)
    --phone, -p PHONE        Target WhatsApp phone number (default: 919895822141)
    --model, -m MODEL        LLM model name (default: gemini-3.8-flash)
    --api-base URL           OpenAI-compatible endpoint (default: http://localhost:7860/v1)
    --skip-translate         Skip translation step (use existing translated text)
    --skip-tts               Skip TTS generation step (use existing audio)
    --skip-send              Run translation and TTS without sending to WhatsApp
    --dry-run                Print metadata card and execution plan without executing TTS/WhatsApp
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Tuple

from card_formatter import format_whatsapp_card

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SYSTEM_PROMPT = PROJECT_DIR / "system_prompt.txt"
GOOGLE_DOCS_TTS_DIR = Path("/Users/firozahmed/Desktop/firofame/google-docs-tts")
WHATSAPP_SENDER_DIR = Path("/Users/firozahmed/Desktop/firofame/whatsapp-sender")
DEFAULT_PHONE = "919895822141"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_API_BASE = "http://localhost:7860/v1"
DEFAULT_API_KEY = "123456"


def sanitize_for_tts(text: str) -> str:
    """Ensure text is 100% compliant with Google Docs Malayalam TTS."""
    # Convert digits to Malayalam words if any leaked
    digit_words = {
        '0': ' പൂജ്യം ', '1': ' ഒന്ന് ', '2': ' രണ്ട് ', '3': ' മൂന്ന് ',
        '4': ' നാല് ', '5': ' അഞ്ച് ', '6': ' ആറ് ', '7': ' ഏഴ് ',
        '8': ' എട്ട് ', '9': ' ഒൻപത് '
    }
    for d, w in digit_words.items():
        text = text.replace(d, w)

    # Strip brackets, quotes, and punctuation that might cause TTS stuttering
    text = re.sub(r'[()[\]{}«»"\'“”‘’`*#_~]', ' ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def parse_metadata_and_body(response_text: str) -> Tuple[Dict[str, str], str]:
    """Parse YAML metadata block at top of translation response if present."""
    metadata = {
        "title_malayalam": "ഉപജീവനവും അല്ലാഹുവിലുള്ള ഭരമേല്പിക്കലും",
        "subtitle_malayalam": "തവക്കുലും അനുവദനീയ സമ്പാദ്യവും",
        "khatib": "ഡോ. ശൈഖ് ഉസാമ ബിൻ അബ്ദുള്ള ഖയ്യാത്വ്",
    }
    body = response_text.strip()

    match = re.search(r'```(?:yaml)?\s*(.*?)\s*```(.*)', response_text, re.DOTALL | re.IGNORECASE)
    if match:
        raw_meta = match.group(1)
        body = match.group(2).strip()
        for line in raw_meta.splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip().strip('"\'')
                if key in metadata and val:
                    metadata[key] = val

    return metadata, body


def call_llm_translate(
    arabic_text: str,
    api_base: str,
    api_key: str,
    model: str,
    system_prompt_path: Path
) -> Tuple[Dict[str, str], str]:
    """Send Arabic Khutbah to AIStudioToAPI proxy for translation."""
    print(f"Translating via {api_base} with model {model}...")
    system_prompt = system_prompt_path.read_text(encoding="utf-8")

    endpoint = f"{api_base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"ഇനി പറയുന്ന അറബി ജുമുഅ ഖുതുബ പൂർണ്ണമായി മലയാളത്തിലേക്ക് വിവർത്തനം ചെയ്യുക:\n\n{arabic_text}"}
        ]
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            metadata, body = parse_metadata_and_body(content)
            clean_body = sanitize_for_tts(body)
            print("✅ Translation complete and sanitized for TTS.")
            return metadata, clean_body
    except Exception as e:
        print(f"❌ Translation failed: {e}", file=sys.stderr)
        raise


def run_google_docs_tts(clean_text_path: Path, output_ogg_path: Path) -> Path:
    """Run google-docs-tts Playwright script using its virtualenv."""
    print(f"\n🎙️ Starting Google Docs TTS -> {output_ogg_path}...")
    tts_script = GOOGLE_DOCS_TTS_DIR / "tts.py"
    tts_python = GOOGLE_DOCS_TTS_DIR / ".venv/bin/python"
    if not tts_python.exists():
        tts_python = Path(sys.executable)

    cmd = [
        str(tts_python),
        str(tts_script),
        str(clean_text_path),
        str(output_ogg_path),
    ]

    res = subprocess.run(cmd, cwd=str(GOOGLE_DOCS_TTS_DIR))
    if res.returncode != 0:
        raise RuntimeError("TTS conversion failed.")
    print(f"✅ Audio generated successfully: {output_ogg_path}")
    return output_ogg_path


def send_to_whatsapp(phone: str, audio_path: Path, card_text: str) -> None:
    """Send voice note and card to WhatsApp using whatsapp-sender."""
    print(f"\n📲 Dispatching to WhatsApp ({phone})...")
    wa_script = WHATSAPP_SENDER_DIR / "send_whatsapp.py"
    wa_python = WHATSAPP_SENDER_DIR / ".venv/bin/python"
    if not wa_python.exists():
        wa_python = Path(sys.executable)

    cmd = [
        str(wa_python),
        str(wa_script),
        phone,
        str(audio_path),
        "--title",
        card_text,
    ]

    res = subprocess.run(cmd, cwd=str(WHATSAPP_SENDER_DIR))
    if res.returncode != 0:
        raise RuntimeError("WhatsApp dispatch failed.")
    print("🎉 Voice note & card sent successfully to WhatsApp!")


def main():
    parser = argparse.ArgumentParser(description="Automated Friday Khutbah Pipeline")
    parser.add_argument("--input", "-i", type=Path, required=True, help="Path to input Arabic transcript")
    parser.add_argument("--phone", "-p", default=DEFAULT_PHONE, help="Recipient WhatsApp number")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, help="LLM Model name")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="API Base URL")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API Key")
    parser.add_argument("--skip-translate", action="store_true", help="Skip translation step")
    parser.add_argument("--skip-tts", action="store_true", help="Skip TTS generation")
    parser.add_argument("--skip-send", action="store_true", help="Skip WhatsApp sending")
    parser.add_argument("--dry-run", action="store_true", help="Preview metadata card and steps without executing")

    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: Input file {args.input} does not exist.", file=sys.stderr)
        sys.exit(1)

    work_dir = PROJECT_DIR / "output"
    work_dir.mkdir(exist_ok=True)

    translated_txt = work_dir / f"{args.input.stem}_malayalam.txt"
    output_ogg = work_dir / f"{args.input.stem}_malayalam.ogg"
    meta_json = work_dir / f"{args.input.stem}_meta.json"

    # Step 1: Translate
    if not args.skip_translate and not args.dry_run:
        arabic_text = args.input.read_text(encoding="utf-8")
        metadata, clean_body = call_llm_translate(
            arabic_text=arabic_text,
            api_base=args.api_base,
            api_key=args.api_key,
            model=args.model,
            system_prompt_path=DEFAULT_SYSTEM_PROMPT,
        )
        translated_txt.write_text(clean_body, encoding="utf-8")
        meta_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        if meta_json.exists():
            metadata = json.loads(meta_json.read_text(encoding="utf-8"))
        else:
            metadata = {
                "title_malayalam": "ഉപജീവനവും അല്ലാഹുവിലുള്ള ഭരമേല്പിക്കലും",
                "subtitle_malayalam": "തവക്കുലും അനുവദനീയ സമ്പാദ്യവും",
                "khatib": "ഡോ. ശൈഖ് ഉസാമ ബിൻ അബ്ദുള്ള ഖയ്യാത്വ്",
            }

    # Step 2: TTS
    if not args.skip_tts and not args.dry_run:
        run_google_docs_tts(translated_txt, output_ogg)

    # Step 3: Format Card
    card_text = format_whatsapp_card(
        title_malayalam=metadata.get("title_malayalam", "ജുമുഅ ഖുതുബ"),
        subtitle_malayalam=metadata.get("subtitle_malayalam"),
        khatib=metadata.get("khatib", "ഖതീബ്"),
    )

    print("\n--- [WhatsApp Card Preview] ---")
    print(card_text)
    print("--------------------------------\n")

    # Step 4: WhatsApp
    if not args.skip_send and not args.dry_run:
        send_to_whatsapp(args.phone, output_ogg, card_text)


if __name__ == "__main__":
    main()
