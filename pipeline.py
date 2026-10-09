#!/usr/bin/env python3
"""Translate, validate, and narrate khutbahs; send only with explicit --send."""

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from card_formatter import format_whatsapp_card

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SYSTEM_PROMPT = PROJECT_DIR / "system_prompt.txt"
GOOGLE_DOCS_TTS_DIR = Path("/Users/firozahmed/Desktop/firofame/google-docs-tts")
WHATSAPP_SENDER_DIR = Path("/Users/firozahmed/Desktop/firofame/whatsapp-sender")
DEFAULT_PHONE = "919895822141"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_API_BASE = "http://localhost:7860/v1"


@dataclass(frozen=True)
class Metadata:
    title_malayalam: str
    subtitle_malayalam: str
    khatib: str


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json_object(path: Path) -> Dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return value


def sanitize_for_tts(text: str) -> str:
    """Normalize whitespace without silently deleting or changing meaning."""
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def validate_narration(text: str, label: str = "Narration") -> str:
    text = sanitize_for_tts(text)
    if not text or not any("\u0d00" <= char <= "\u0d7f" for char in text):
        raise ValueError(f"{label} is empty or contains no Malayalam.")
    invalid = sorted({
        char for char in text
        if not (
            ("\u0d00" <= char <= "\u0d7f" and not char.isnumeric())
            or char.isspace()
            or char in ".,:?\u200c\u200d"
        )
    })
    if invalid:
        codes = ", ".join(f"U+{ord(char):04X}" for char in invalid)
        raise ValueError(
            f"{label} contains unsupported characters ({codes}). "
            "Correct or regenerate it; numbers must be written in Malayalam words."
        )
    return text


def parse_metadata(value: object) -> Metadata:
    if not isinstance(value, dict) or set(value) != set(Metadata.__annotations__):
        raise ValueError("Metadata must contain exactly title_malayalam, subtitle_malayalam, and khatib.")
    fields: Dict[str, str] = {}
    for key, text in value.items():
        if not isinstance(text, str):
            raise ValueError(f"Metadata field {key} must be a string.")
        fields[key] = validate_narration(text, f"Metadata field {key}")
    return Metadata(**fields)


def source_blocks(text: str) -> List[Dict[str, str]]:
    paragraphs = re.split(r"\n\s*\n", text.strip())
    if not text.strip():
        raise ValueError("The input transcript is empty.")
    return [
        {"id": f"p{index:04d}", "source": paragraph.strip()}
        for index, paragraph in enumerate(paragraphs, 1)
    ]


def parse_translation(
    response_text: str, blocks: List[Dict[str, str]]
) -> Tuple[Metadata, str, List[Dict[str, str]]]:
    content = response_text.strip()
    fence = re.fullmatch(r"```json\s*(.*?)\s*```", content, re.DOTALL)
    if fence:
        content = fence.group(1)
    response = json.loads(content)
    if not isinstance(response, dict) or set(response) != {"metadata", "segments"}:
        raise ValueError("Translation must contain exactly metadata and segments.")
    metadata = parse_metadata(response["metadata"])
    segments = response["segments"]
    if not isinstance(segments, list) or len(segments) != len(blocks):
        raise ValueError("Translation omitted or added source blocks; review and regenerate it.")

    narration = []
    review = []
    for block, segment in zip(blocks, segments):
        if not isinstance(segment, dict) or set(segment) != {"id", "kind", "malayalam"}:
            raise ValueError(f"Invalid translation segment for {block['id']}.")
        if segment["id"] != block["id"]:
            raise ValueError(f"Missing, duplicated, or reordered source block {block['id']}.")
        kind = segment["kind"]
        text = segment["malayalam"]
        if kind not in ("sermon", "editorial", "prayer") or not isinstance(text, str):
            raise ValueError(f"Invalid classification or narration for {block['id']}.")
        if kind == "sermon":
            text = validate_narration(text, f"Source block {block['id']}")
            narration.append(text)
        elif text:
            raise ValueError(f"Excluded source block {block['id']} must have empty narration.")
        review.append({**block, "kind": kind, "malayalam": text})
    body = validate_narration("\n\n".join(narration))
    return metadata, body, review


def call_llm_translate(
    arabic_text: str,
    api_base: str,
    api_key: str,
    model: str,
    system_prompt_path: Path,
) -> Tuple[Metadata, str, List[Dict[str, str]]]:
    blocks = source_blocks(arabic_text)
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": system_prompt_path.read_text(encoding="utf-8")},
            {"role": "user", "content": json.dumps({"source_blocks": blocks}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    print(f"Translating {len(blocks)} source blocks via {api_base} ({model})...")
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        raise ValueError("Translation API returned no completion.")
    choice = data["choices"][0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise ValueError("Translation did not finish normally; it may be truncated or blocked.")
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("Translation API returned no text.")
    return parse_translation(message["content"], blocks)


def validate_audio(path: Path) -> float:
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Audio is missing or empty: {path}")
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=format_name,duration:stream=codec_name,codec_type,channels,sample_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid audio probe result for {path}.")
    streams = data.get("streams", [])
    if not isinstance(streams, list) or len(streams) != 1 or not isinstance(streams[0], dict):
        raise ValueError(f"Expected one audio stream in {path}.")
    audio_format = data.get("format")
    if not isinstance(audio_format, dict):
        raise ValueError(f"Audio format information is missing: {path}")
    stream = streams[0]
    if (
        stream.get("codec_type") != "audio"
        or stream.get("codec_name") != "opus"
        or stream.get("channels") != 1
        or stream.get("sample_rate") != "48000"
        or audio_format.get("format_name") != "ogg"
    ):
        raise ValueError(f"Audio must be mono 48 kHz OGG/Opus: {path}")
    duration_value = audio_format.get("duration")
    if not isinstance(duration_value, (str, float, int)):
        raise ValueError(f"Audio duration is missing: {path}")
    duration = float(duration_value)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Audio has no valid duration: {path}")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    return duration


def run_google_docs_tts(clean_text_path: Path, output_ogg_path: Path) -> float:
    if output_ogg_path.exists():
        raise ValueError(f"Refusing to let TTS silently reuse an existing file: {output_ogg_path}")
    tts_python = GOOGLE_DOCS_TTS_DIR / ".venv/bin/python"
    if not tts_python.is_file():
        raise FileNotFoundError(f"TTS interpreter is missing: {tts_python}")
    subprocess.run(
        [str(tts_python), str(GOOGLE_DOCS_TTS_DIR / "tts.py"),
         str(clean_text_path), str(output_ogg_path)],
        cwd=GOOGLE_DOCS_TTS_DIR, check=True,
    )
    duration = validate_audio(output_ogg_path)
    print(f"Verified generated audio: {output_ogg_path}")
    return duration


def prepare_audio(
    narration_path: Path, run_dir: Path, state: Dict[str, object], rebuild: bool = False
) -> Tuple[Path, float]:
    narration_sha = file_hash(narration_path)
    cached = state.get("audio")
    if not rebuild and isinstance(cached, dict) and cached.get("narration_sha256") == narration_sha:
        filename = cached.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("Invalid cached audio filename in state.json.")
        audio_path = run_dir / filename
        if audio_path.is_file() and file_hash(audio_path) == cached.get("sha256"):
            duration = validate_audio(audio_path)
            print(f"Reusing verified audio: {audio_path}")
            return audio_path, duration
        print("Cached audio is missing or changed; generating a new artifact.", file=sys.stderr)

    audio_path = run_dir / f"audio_{narration_sha[:16]}.ogg"
    if rebuild or audio_path.exists():
        audio_path = run_dir / f"audio_{narration_sha[:16]}_{uuid4().hex[:12]}.ogg"
    duration = run_google_docs_tts(narration_path, audio_path)
    state["audio"] = {
        "filename": audio_path.name,
        "narration_sha256": narration_sha,
        "sha256": file_hash(audio_path),
    }
    return audio_path, duration


def send_to_whatsapp(
    phone: str, audio_path: Path, card_text: str, dispatch_dir: Path, resend: bool = False
) -> None:
    key = text_hash(json.dumps([phone, file_hash(audio_path), text_hash(card_text)]))
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = dispatch_dir / f"{key}.json"
    with (dispatch_dir / f"{key}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("This WhatsApp dispatch is already running.") from error
        if receipt_path.exists() and not resend:
            receipt = read_json_object(receipt_path)
            if receipt.get("card") == "sent" and receipt.get("audio") == "sent":
                print("Already sent this audio and card to this recipient; skipping duplicate.")
                return
            raise RuntimeError(
                "A previous send is failed, partial, or unconfirmed. Check WhatsApp before "
                "using --resend; retrying can duplicate messages."
            )
        wa_python = WHATSAPP_SENDER_DIR / ".venv/bin/python"
        if not wa_python.is_file():
            raise FileNotFoundError(f"WhatsApp interpreter is missing: {wa_python}")
        receipt = {
            "phone": phone, "audio_sha256": file_hash(audio_path),
            "card_sha256": text_hash(card_text), "card": "unconfirmed", "audio": "unconfirmed",
            "attempted_at": datetime.now().isoformat(),
        }
        write_json(receipt_path, receipt)
        try:
            result = subprocess.run(
                [str(wa_python), "-u", str(WHATSAPP_SENDER_DIR / "send_whatsapp.py"),
                 phone, str(audio_path), "--title", card_text],
                cwd=WHATSAPP_SENDER_DIR, capture_output=True, text=True,
            )
        except OSError as error:
            receipt["error"] = str(error)
            write_json(receipt_path, receipt)
            raise
        print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)

        # The existing sender exposes confirmations through stdout, not message IDs.
        label_failed = "Failed to send label:" in result.stderr
        split = re.search(r"Split audio into (\d+) segment\(s\)\.", result.stdout)
        filenames = (
            [f"{audio_path.stem}_part_{index:02d}.ogg" for index in range(int(split.group(1)))]
            if split else [audio_path.name]
        )
        cards_sent = sum(f"Sending {name} to {phone}..." in result.stdout for name in filenames)
        audios_sent = sum(f"Sent as voice note: {name}" in result.stdout for name in filenames)
        receipt["card"] = (
            "failed" if label_failed else
            "sent" if filenames and cards_sent == len(filenames) else
            "partial" if cards_sent else "unconfirmed"
        )
        receipt["audio"] = (
            "sent" if filenames and audios_sent == len(filenames) else
            "partial" if audios_sent else "unconfirmed"
        )
        receipt["confirmed_audio_files"] = [
            name for name in filenames if f"Sent as voice note: {name}" in result.stdout
        ]
        receipt["returncode"] = result.returncode
        write_json(receipt_path, receipt)
        if result.returncode != 0 or receipt["card"] != "sent" or receipt["audio"] != "sent":
            raise RuntimeError(
                "WhatsApp did not confirm both the card and voice note. "
                f"Check the chat and receipt: {receipt_path}"
            )
        print(f"WhatsApp sender confirmed the card and voice note to {phone}.")


def metadata_path_for(narration_path: Path, explicit: Optional[Path]) -> Path:
    if explicit is not None:
        return explicit
    if narration_path.name == "malayalam.txt":
        return narration_path.with_name("metadata.json")
    stem = narration_path.stem.removesuffix("_malayalam")
    return narration_path.with_name(f"{stem}_meta.json")


def parse_date(value: str) -> date:
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise argparse.ArgumentTypeError("Use an ISO sermon date, YYYY-MM-DD.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use an ISO sermon date, YYYY-MM-DD.") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", "-i", type=Path, help="Arabic transcript")
    inputs.add_argument("--translated-input", type=Path, help="Existing Malayalam narration")
    inputs.add_argument("--audio-input", type=Path, help="Existing verified OGG/Opus, without TTS")
    parser.add_argument("--metadata", type=Path, help="JSON title, subtitle, and khatib for existing inputs")
    parser.add_argument("--phone", "-p", default=DEFAULT_PHONE)
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--api-key", default=os.environ.get("KHUTBAH_API_KEY", ""))
    parser.add_argument("--sermon-date", type=parse_date, default=date.today())
    parser.add_argument("--output-dir", type=Path, default=PROJECT_DIR / "output")
    parser.add_argument("--skip-translate", action="store_true", help="Treat --input as existing Malayalam")
    parser.add_argument("--skip-tts", action="store_true", help="Do not generate audio; --send requires cached audio")
    parser.add_argument("--retranslate", action="store_true", help="Replace the cached translation")
    parser.add_argument("--rebuild-audio", action="store_true", help="Generate a new audio file, preserving older files")
    sending = parser.add_mutually_exclusive_group()
    sending.add_argument("--send", action="store_true", help="Explicitly send the card and audio to WhatsApp")
    sending.add_argument("--skip-send", action="store_true", help="Compatibility flag; sending is off by default")
    parser.add_argument("--resend", action="store_true", help="Allow retry or duplicate sending after checking the chat")
    parser.add_argument("--dry-run", action="store_true", help="Read-only execution plan; no APIs, TTS, or sending")
    args = parser.parse_args()

    if args.skip_translate and args.input is None:
        parser.error("--skip-translate requires --input; prefer --translated-input.")
    is_audio = args.audio_input is not None
    is_translated = args.translated_input is not None or args.skip_translate
    if args.metadata and not (is_audio or is_translated):
        parser.error("--metadata is for existing Malayalam or audio inputs.")
    if args.retranslate and (is_audio or is_translated):
        parser.error("--retranslate requires an Arabic --input.")
    if args.rebuild_audio and (args.skip_tts or is_audio):
        parser.error("--rebuild-audio cannot be combined with --skip-tts or --audio-input.")
    if args.resend and not args.send:
        parser.error("--resend requires --send.")
    phone = args.phone
    if len(phone) == 10 and phone.isdigit():
        phone = f"91{phone}"
    if args.send and (not phone.isascii() or not phone.isdigit() or not 11 <= len(phone) <= 15):
        parser.error("--phone must contain country code and ASCII digits, without '+'.")

    source = (args.audio_input or args.translated_input or args.input).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Input does not exist: {source}")
    source_sha = file_hash(source)
    run_dir = args.output_dir.resolve() / f"{args.sermon_date.isoformat()}_{source_sha[:16]}"
    narration_path = run_dir / "malayalam.txt"
    meta_path = run_dir / "metadata.json"
    state_path = run_dir / "state.json"
    if args.dry_run:
        print(f"Input: {source}")
        print(f"Run directory: {run_dir}")
        print(f"Sermon date: {args.sermon_date}")
        print(f"Translation: {'not needed' if is_audio or is_translated else 'cached or API translation'}")
        print(f"Audio: {'existing input' if is_audio else 'cached only' if args.skip_tts else 'verified cache or new TTS'}")
        print(f"WhatsApp: {'send to ' + phone if args.send else 'disabled (use --send)'}")
        return

    metadata = None
    if is_audio or is_translated:
        if is_audio and args.metadata is None:
            parser.error("--audio-input requires --metadata.")
        input_meta_path = metadata_path_for(source, args.metadata)
        if not input_meta_path.is_file():
            raise FileNotFoundError(f"Metadata is missing; provide --metadata: {input_meta_path}")
        metadata = parse_metadata(read_json_object(input_meta_path))

    state = read_json_object(state_path) if state_path.exists() else {}
    body = None
    if is_translated:
        body = validate_narration(source.read_text(encoding="utf-8"))
    elif not is_audio:
        arabic_text = source.read_text(encoding="utf-8")
        source_blocks(arabic_text)
        context_sha = text_hash(json.dumps([
            source_sha, args.model, args.api_base.rstrip("/"),
            DEFAULT_SYSTEM_PROMPT.read_text(encoding="utf-8"),
        ]))
        if (
            not args.retranslate
            and state.get("translation_context_sha256") == context_sha
            and narration_path.is_file() and meta_path.is_file()
        ):
            metadata = parse_metadata(read_json_object(meta_path))
            body = validate_narration(narration_path.read_text(encoding="utf-8"))
            if file_hash(narration_path) != state.get("narration_sha256"):
                print("Saved narration was edited; reusing the edits and invalidating old audio.", file=sys.stderr)
            print(f"Reusing saved Malayalam narration: {narration_path}")
        else:
            if not args.api_key:
                raise ValueError("Translation requires KHUTBAH_API_KEY or --api-key.")
            metadata, body, review = call_llm_translate(
                arabic_text, args.api_base, args.api_key, args.model, DEFAULT_SYSTEM_PROMPT
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            write_json(run_dir / "review.json", review)
            excluded = sum(segment["kind"] != "sermon" for segment in review)
            print(f"Validated {len(review)} source blocks; {excluded} marked editorial/prayer.")
            print(f"Review classifications and translation details: {run_dir / 'review.json'}")
            state["translation_context_sha256"] = context_sha
    if metadata is None:
        raise ValueError("No valid metadata is available.")
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(meta_path, asdict(metadata))
    if body is not None:
        narration_path.write_text(body + "\n", encoding="utf-8")
        state["narration_sha256"] = file_hash(narration_path)
    state["source_sha256"] = source_sha
    state["sermon_date"] = args.sermon_date.isoformat()
    write_json(state_path, state)

    audio_path = None
    duration = None
    if is_audio:
        audio_path = source
        duration = validate_audio(audio_path)
    elif not args.skip_tts:
        audio_path, duration = prepare_audio(narration_path, run_dir, state, args.rebuild_audio)
        write_json(state_path, state)
    elif args.send:
        cached = state.get("audio")
        if not isinstance(cached, dict) or cached.get("narration_sha256") != file_hash(narration_path):
            raise ValueError("--skip-tts --send requires verified audio for this exact narration.")
        filename = cached.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("Invalid cached audio filename.")
        audio_path = run_dir / filename
        if not audio_path.is_file() or file_hash(audio_path) != cached.get("sha256"):
            raise ValueError("Cached audio is missing or changed; regenerate it before sending.")
        duration = validate_audio(audio_path)

    card = format_whatsapp_card(
        title_malayalam=metadata.title_malayalam,
        subtitle_malayalam=metadata.subtitle_malayalam,
        khatib=metadata.khatib,
        target_date=datetime.combine(args.sermon_date, datetime.min.time()),
        duration_seconds=duration,
    )
    (run_dir / "card.txt").write_text(card + "\n", encoding="utf-8")
    print(f"\n--- Card preview ---\n{card}\n--------------------")
    if audio_path is not None:
        print(f"Audio: {audio_path}")
    if args.send:
        if audio_path is None:
            raise ValueError("No audio is available to send.")
        send_to_whatsapp(phone, audio_path, card, args.output_dir.resolve() / "dispatch", args.resend)
    else:
        print("Nothing sent to WhatsApp. Use --send explicitly when ready.")


def run() -> None:
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, urllib.error.URLError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
