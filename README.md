# Khutbah Pipeline (ജുമുഅ ഖുതുബ ഓട്ടോമേഷൻ)

End-to-end automated pipeline for Friday Jumma Khutbah:
1. **Translation**: Arabic transcript -> Spoken Malayalam narration via `AIStudioToAPI` (`gemini-3.8-flash`).
2. **Sanitization**: Strictly strips brackets, digits, quotes, and non-Malayalam text to guarantee flawless Google Docs TTS narration.
3. **TTS**: Automates `google-docs-tts` to generate high quality OGG/Opus audio voice notes.
4. **Card Formatter**: Automatically calculates Gregorian date, Malayalam weekday, Hijri date, and exact audio duration.
5. **WhatsApp Dispatcher**: Sends voice notes with styled headers via `whatsapp-sender`.

---

## Quick Usage

### 1. Run Pipeline with Arabic Transcript
```bash
cd /Users/firozahmed/Desktop/firofame/khutbah-pipeline
.venv/bin/python pipeline.py --input path/to/arabic_transcript.txt
```

### 2. Preview Only (Dry-Run)
```bash
.venv/bin/python pipeline.py --input samples/sample_khutbah.txt --dry-run
```

### 3. Skip Translation or TTS if already generated
```bash
.venv/bin/python pipeline.py --input output/sample_malayalam.txt --skip-translate
```

### 4. Custom WhatsApp Phone Number
```bash
.venv/bin/python pipeline.py --input arabic.txt --phone 919895822141
```
