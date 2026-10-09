# Khutbah Pipeline (ജുമുഅ ഖുതുബ ഓട്ടോമേഷൻ)

Translate Arabic Friday khutbahs into spoken Malayalam, generate verified
OGG/Opus audio with `google-docs-tts`, and optionally send the voice note and
metadata card with `whatsapp-sender`.

**Sending is now off by default. Use `--send` explicitly.** `--skip-send` remains
accepted for compatibility. Generation and previews never send messages unless
`--send` is present.

## Prerequisites

- Project dependencies in `.venv`, plus `ffprobe` and `ffmpeg` on `PATH`.
- The configured local `AIStudioToAPI` endpoint for translation.
- `KHUTBAH_API_KEY` set to the endpoint's key, or an explicit `--api-key`.
  Keys are not stored in artifacts. No key is needed when reusing a cached
  translation or supplying existing Malayalam/audio.
- Configured `google-docs-tts` and `whatsapp-sender` sibling projects with their
  own virtual environments and authenticated browser profiles.

## Next Friday

```bash
cd /Users/firozahmed/Desktop/firofame/khutbah-pipeline

# Preview the plan. This does not write files or contact external services.
.venv/bin/python pipeline.py --input next_week.txt --sermon-date 2026-10-16 --dry-run

# Translate only, then inspect the narration, metadata and source-aligned review.
.venv/bin/python pipeline.py --input next_week.txt --sermon-date 2026-10-16 --skip-tts

# Reuse the reviewed narration and generate audio. No WhatsApp messages.
.venv/bin/python pipeline.py --input next_week.txt --sermon-date 2026-10-16

# After listening, send only the verified cached audio and card.
.venv/bin/python pipeline.py --input next_week.txt --sermon-date 2026-10-16 --skip-tts --send
```

Use the actual sermon date, not the date you happen to process an older recording.
`--sermon-date` defaults to today's local date. Cards include the specified
Gregorian date, Malayalam weekday, calculated Hijri date, khatib, and measured
audio duration. Missing Hijri dependencies are an error, not a fabricated date.

## Existing narration or audio

```bash
# Use this exact Malayalam text, rather than deriving a second filename.
.venv/bin/python pipeline.py --translated-input reviewed_malayalam.txt \
  --metadata reviewed_meta.json --sermon-date 2026-10-16

# Send this exact audio file, without translating or generating audio.
.venv/bin/python pipeline.py --audio-input reviewed_audio.ogg \
  --metadata reviewed_meta.json --sermon-date 2026-10-16 --send
```

Metadata JSON must have exactly these three nonempty Malayalam values:

```json
{
  "title_malayalam": "പ്രസംഗത്തിന്റെ പ്രധാന ആശയം",
  "subtitle_malayalam": "പ്രത്യേക ഉപവിഷയം",
  "khatib": "ഖതീബ് വ്യക്തമാക്കിയിട്ടില്ല"
}
```

Use the real khatib when identified. Missing metadata never falls back to a
previous sermon. For Malayalam input, `--metadata` can be omitted when a matching
`<name>_meta.json` accompanies `<name>_malayalam.txt`, or when `metadata.json`
accompanies a generated `malayalam.txt`. Audio-only input requires `--metadata`.

The old `--input reviewed_malayalam.txt --skip-translate` syntax now reads the
supplied text directly and follows the same metadata rules. Prefer
`--translated-input` for clarity. `--skip-tts` without `--send` is translation-only;
with `--send`, it requires a verified cache matching the exact narration.

## Artifacts and validation

Each run is stored in `output/<sermon-date>_<source-content-hash>/`:

- `malayalam.txt`: editable narration.
- `metadata.json`: title, subtitle, and khatib.
- `review.json`: every Arabic source block, its classification, and its Malayalam
  translation. Created for API translations.
- `state.json`: translation context and verified audio hashes.
- `audio_<narration-content-hash>.ogg`: content-aware audio output.
- `card.txt`: the generated card.

The model must return one ordered segment per source block, with an explicit
classification of sermon, editorial, or prayer-service material. Missing,
duplicate, reordered, empty sermon segments and truncated API responses fail
validation. Non-Malayalam script, numerals, and unsupported narration punctuation
also fail explicitly instead of being silently deleted or mispronounced.

**Structural validation cannot prove translation accuracy.** Review omitted-block
classifications, quotations, rhetorical details, and supplications before TTS.
Both khutbahs and their supplications belong in narration; iqamah and salah do not.

Saved narration is reused when source, model, API endpoint, and prompt match.
You can edit `malayalam.txt` before rerunning the same Arabic command; those edits
are preserved, and changed narration invalidates old audio. After manual edits,
`review.json` still shows the original model response, not your corrected prose.
Use `--retranslate` only when you intend to replace the saved translation.

Audio is reused only when both its content hash and narration binding match.
New or edited narration gets new audio. Unverified existing filenames are never
silently reused. `--rebuild-audio` creates a fresh file while preserving old audio.
Before sending, audio must probe as mono 48 kHz OGG/Opus with a positive duration
and decode completely without errors.

## Sending and retries

```bash
.venv/bin/python pipeline.py --input next_week.txt --sermon-date 2026-10-16 \
  --send --phone 919895822141
```

The existing configured default recipient is unchanged. Use a country-code phone
number without `+`; ten-digit numbers receive the existing `91` prefix.

Receipts under `output/dispatch/` identify the recipient, audio hash, and card
hash. Repeating a confirmed identical send is skipped. Concurrent duplicate sends
are blocked. Card and audio confirmations are recorded separately, including
split voice-note parts for larger files.

If an attempt fails, partially succeeds, or is interrupted, **check the WhatsApp
chat before retrying**. A pending/unconfirmed receipt prevents automatic retries.
`--resend --send` permits an intentional retry or duplicate, and may repeat
messages already sent. The existing sender exposes textual success confirmations,
not message IDs or recipient delivery/read acknowledgements; receipts record
sender confirmation only.

## Offline regression tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary artifacts and mocked API/TTS/WhatsApp calls. They do not
contact Google, invoke a real TTS session, or send WhatsApp messages.
