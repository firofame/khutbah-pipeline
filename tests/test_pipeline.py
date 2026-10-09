import io
import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import card_formatter
import pipeline


METADATA = pipeline.Metadata("വാക്കിന്റെ ഉത്തരവാദിത്തം", "മാധ്യമങ്ങളിലെ മര്യാദകൾ", "ഖതീബ് വ്യക്തമാക്കിയിട്ടില്ല")
NARRATION = "അല്ലാഹുവിന് സർവ്വസ്തുതിയും.\n\nനിങ്ങൾ നന്മ ചെയ്യുക."
PROBE = {
    "streams": [{"codec_type": "audio", "codec_name": "opus", "channels": 1, "sample_rate": "48000"}],
    "format": {"format_name": "ogg", "duration": "920.4"},
}


class QuietTestCase(unittest.TestCase):
    def setUp(self):
        for stream in ("stdout", "stderr"):
            capture = patch(f"sys.{stream}", new_callable=io.StringIO)
            setattr(self, stream, capture.start())
            self.addCleanup(capture.stop)


class TranslationTests(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.blocks = pipeline.source_blocks("مقدمة\n\nالحمد لله\n\nاللهم اغفر لنا\n\nإقامة الصلاة")
        self.response = {
            "metadata": asdict(METADATA),
            "segments": [
                {"id": "p0001", "kind": "editorial", "malayalam": ""},
                {"id": "p0002", "kind": "sermon", "malayalam": "അല്ലാഹുവിന് സർവ്വസ്തുതിയും."},
                {"id": "p0003", "kind": "sermon", "malayalam": "അല്ലാഹുവേ, ഞങ്ങൾക്ക് പൊറുത്തുതരേണമേ."},
                {"id": "p0004", "kind": "prayer", "malayalam": ""},
            ],
        }

    def parse(self):
        return pipeline.parse_translation(json.dumps(self.response), self.blocks)

    def test_source_alignment_and_exclusion(self):
        metadata, narration, review = self.parse()
        self.assertEqual(metadata, METADATA)
        self.assertEqual(narration, "അല്ലാഹുവിന് സർവ്വസ്തുതിയും.\n\nഅല്ലാഹുവേ, ഞങ്ങൾക്ക് പൊറുത്തുതരേണമേ.")
        self.assertEqual([item["source"] for item in review], [item["source"] for item in self.blocks])
        self.assertEqual(len(review), 4)

    def test_fenced_json(self):
        result = pipeline.parse_translation(f"```json\n{json.dumps(self.response)}\n```", self.blocks)
        self.assertEqual(result[0], METADATA)

    def test_missing_extra_duplicate_and_reordered_blocks_fail(self):
        original = self.response["segments"]
        for segments in (original[:-1], original + original[-1:], original[:1] * 4, original[::-1]):
            with self.subTest(segments=segments):
                self.response["segments"] = segments
                with self.assertRaises(ValueError):
                    self.parse()

    def test_empty_sermon_fails(self):
        self.response["segments"][1]["malayalam"] = ""
        with self.assertRaisesRegex(ValueError, "empty"):
            self.parse()

    def test_nonempty_excluded_segment_fails(self):
        self.response["segments"][3]["malayalam"] = NARRATION
        with self.assertRaisesRegex(ValueError, "empty narration"):
            self.parse()

    def test_no_sermon_fails(self):
        for segment in self.response["segments"]:
            segment.update(kind="editorial", malayalam="")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.parse()

    def test_metadata_has_no_previous_sermon_fallback(self):
        for metadata in ({}, {"title_malayalam": METADATA.title_malayalam}, None):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                pipeline.parse_metadata(metadata)

    def test_invalid_metadata_types_and_script_fail(self):
        for value in ("Sudais", 123, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                pipeline.parse_metadata({**asdict(METADATA), "khatib": value})

    def test_invalid_response_shapes_fail(self):
        for response in ([], {"metadata": asdict(METADATA)}, {"metadata": asdict(METADATA), "segments": None}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                pipeline.parse_translation(json.dumps(response), self.blocks)

    def test_empty_source_fails(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            pipeline.source_blocks(" \n\n ")

    def test_api_checks_finish_reason_and_authentication(self):
        choice = {"finish_reason": "stop", "message": {"content": json.dumps(self.response)}}
        with patch("pipeline.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({"choices": [choice]}).encode()
            result = pipeline.call_llm_translate(
                "\n\n".join(block["source"] for block in self.blocks),
                "http://localhost/v1", "test-key", "test-model", pipeline.DEFAULT_SYSTEM_PROMPT,
            )
        self.assertEqual(result[0], METADATA)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        sent = json.loads(request.data)
        self.assertEqual(json.loads(sent["messages"][1]["content"])["source_blocks"], self.blocks)

    def test_api_rejects_truncation_filtering_and_missing_content(self):
        choices = [
            {"finish_reason": reason, "message": {"content": json.dumps(self.response)}}
            for reason in ("length", "content_filter", None)
        ] + [{"finish_reason": "stop", "message": {}}]
        for choice in choices:
            with self.subTest(choice=choice), patch("pipeline.urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({"choices": [choice]}).encode()
                with self.assertRaises(ValueError):
                    pipeline.call_llm_translate(
                        "\n\n".join(block["source"] for block in self.blocks),
                        "http://localhost/v1", "test-key", "test-model", pipeline.DEFAULT_SYSTEM_PROMPT,
                    )

    def test_api_rejects_empty_choices(self):
        with patch("pipeline.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.read.return_value = b'{"choices":[]}'
            with self.assertRaisesRegex(ValueError, "no completion"):
                pipeline.call_llm_translate(
                    "الحمد لله", "http://localhost/v1", "test-key", "test-model", pipeline.DEFAULT_SYSTEM_PROMPT
                )

    def test_narration_accepts_joiners_and_normalizes_whitespace(self):
        self.assertEqual(pipeline.validate_narration("  തഖ്‌വ. \n\n\nനന്മ.  "), "തഖ്‌വ. \n\nനന്മ.")

    def test_narration_rejects_instead_of_silently_changing_meaning(self):
        for suffix in ("English", "عربي", "123", "١٢٣", "൧", '"', "#", "🕌", "!", ""):
            text = f"നന്മ {suffix}" if suffix else ""
            with self.subTest(text=text), self.assertRaises(ValueError):
                pipeline.validate_narration(text)
        self.assertIn("123", pipeline.sanitize_for_tts("നന്മ 123"))


class AudioTests(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.narration = self.root / "malayalam.txt"
        self.narration.write_text(NARRATION, encoding="utf-8")
        self.audio = self.root / "audio.ogg"
        self.audio.write_bytes(b"test-audio")

    def test_probe_and_full_decode(self):
        with patch("pipeline.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(PROBE))) as run:
            self.assertAlmostEqual(pipeline.validate_audio(self.audio), 920.4)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0][0], "ffprobe")
        self.assertIn("-xerror", run.call_args_list[1].args[0])

    def test_missing_and_empty_audio_fail_before_probe(self):
        for path in (self.root / "missing.ogg", self.root / "empty.ogg"):
            path.touch()
            with self.subTest(path=path), patch("pipeline.subprocess.run") as run, self.assertRaises(ValueError):
                pipeline.validate_audio(path)
            run.assert_not_called()

    def test_invalid_codec_channels_format_and_duration_fail(self):
        invalid = [
            {**PROBE, "streams": [{**PROBE["streams"][0], "codec_name": "mp3"}]},
            {**PROBE, "streams": [{**PROBE["streams"][0], "channels": 2}]},
            {**PROBE, "format": {**PROBE["format"], "format_name": "mp3"}},
            *[{**PROBE, "format": {**PROBE["format"], "duration": duration}} for duration in ("0", "-1", "nan", "inf")],
        ]
        for probe in invalid:
            with self.subTest(probe=probe), patch(
                "pipeline.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(probe))
            ), self.assertRaises(ValueError):
                pipeline.validate_audio(self.audio)

    def test_invalid_probe_shapes_fail_explicitly(self):
        for probe in ([], {}, {"streams": None}, {**PROBE, "format": None}):
            with self.subTest(probe=probe), patch(
                "pipeline.subprocess.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(probe))
            ), self.assertRaises(ValueError):
                pipeline.validate_audio(self.audio)

    def test_decode_failure_propagates(self):
        with patch("pipeline.subprocess.run", side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps(PROBE)),
            subprocess.CalledProcessError(1, ["ffmpeg"], stderr="corrupt audio"),
        ]), self.assertRaises(subprocess.CalledProcessError):
            pipeline.validate_audio(self.audio)

    def cache(self):
        return {"audio": {
            "filename": self.audio.name,
            "narration_sha256": pipeline.file_hash(self.narration),
            "sha256": pipeline.file_hash(self.audio),
        }}

    def generate(self, source, output):
        self.assertEqual(source, self.narration)
        self.assertFalse(output.exists())
        output.write_bytes(b"new-audio")
        return 10.0

    def test_verified_audio_is_reused(self):
        with patch("pipeline.validate_audio", return_value=10.0), patch("pipeline.run_google_docs_tts") as tts:
            path, duration = pipeline.prepare_audio(self.narration, self.root, self.cache())
        self.assertEqual((path, duration), (self.audio, 10.0))
        tts.assert_not_called()

    def test_edited_narration_and_changed_audio_are_regenerated(self):
        for changed in ("narration", "audio"):
            with self.subTest(changed=changed):
                state = self.cache()
                target = self.narration if changed == "narration" else self.audio
                target.write_text("മാറ്റിയ ഉള്ളടക്കം.", encoding="utf-8")
                with patch("pipeline.run_google_docs_tts", side_effect=self.generate) as tts:
                    path, _ = pipeline.prepare_audio(self.narration, self.root, state)
                self.assertNotEqual(path, self.audio)
                self.assertEqual(state["audio"]["narration_sha256"], pipeline.file_hash(self.narration))
                tts.assert_called_once()

    def test_orphan_audio_and_force_rebuild_preserve_old_files(self):
        orphan = self.root / f"audio_{pipeline.file_hash(self.narration)[:16]}.ogg"
        orphan.write_bytes(b"old-unverified-audio")
        for state, rebuild in (({}, False), (self.cache(), True)):
            with self.subTest(rebuild=rebuild), patch("pipeline.run_google_docs_tts", side_effect=self.generate):
                path, _ = pipeline.prepare_audio(self.narration, self.root, state, rebuild)
            self.assertNotEqual(path, orphan)
            self.assertEqual(orphan.read_bytes(), b"old-unverified-audio")
            self.assertEqual(self.audio.read_bytes(), b"test-audio")

    def test_tts_refuses_existing_file(self):
        with patch("pipeline.subprocess.run") as run, self.assertRaisesRegex(ValueError, "silently reuse"):
            pipeline.run_google_docs_tts(self.narration, self.audio)
        run.assert_not_called()

    def test_tts_exit_zero_without_audio_is_not_success(self):
        interpreter = self.root / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        with patch("pipeline.GOOGLE_DOCS_TTS_DIR", self.root), patch(
            "pipeline.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        ), self.assertRaisesRegex(ValueError, "missing or empty"):
            pipeline.run_google_docs_tts(self.narration, self.root / "new.ogg")


class DispatchTests(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        interpreter = self.root / ".venv/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        self.audio = self.root / "audio.ogg"
        self.audio.write_bytes(b"test-audio")
        self.receipts = self.root / "dispatch"
        self.phone = "919895822141"
        self.patch = patch("pipeline.WHATSAPP_SENDER_DIR", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.success = subprocess.CompletedProcess(
            [], 0, f"Sending audio.ogg to {self.phone}...\nSent as voice note: audio.ogg\n", ""
        )

    def send(self, **kwargs):
        pipeline.send_to_whatsapp(self.phone, self.audio, "കാർഡ്", self.receipts, **kwargs)

    def receipt(self):
        return pipeline.read_json_object(next(self.receipts.glob("*.json")))

    def test_success_and_duplicate_prevention(self):
        with patch("pipeline.subprocess.run", return_value=self.success) as run:
            self.send()
            self.send()
        run.assert_called_once()
        self.assertEqual(self.receipt()["card"], "sent")
        self.assertEqual(self.receipt()["audio"], "sent")

    def test_explicit_resend(self):
        with patch("pipeline.subprocess.run", return_value=self.success) as run:
            self.send()
            self.send(resend=True)
        self.assertEqual(run.call_count, 2)

    def test_label_failure_is_not_success_or_silently_retried(self):
        failure = subprocess.CompletedProcess([], 0, self.success.stdout, "Warning: Failed to send label: error\n")
        with patch("pipeline.subprocess.run", return_value=failure) as run:
            with self.assertRaisesRegex(RuntimeError, "both"):
                self.send()
            with self.assertRaisesRegex(RuntimeError, "previous send"):
                self.send()
        run.assert_called_once()
        self.assertEqual((self.receipt()["card"], self.receipt()["audio"]), ("failed", "sent"))

    def test_failure_and_success_shaped_output_are_rejected(self):
        for result in (
            subprocess.CompletedProcess([], 1, f"Sending audio.ogg to {self.phone}...\n", "error"),
            subprocess.CompletedProcess([], 0, "All voice notes sent!", ""),
        ):
            with self.subTest(result=result), patch("pipeline.subprocess.run", return_value=result), self.assertRaises(RuntimeError):
                self.send(resend=True)
            self.assertEqual(self.receipt()["audio"], "unconfirmed")

    def test_crash_receipt_blocks_automatic_retry(self):
        with patch("pipeline.subprocess.run", side_effect=OSError("could not start")) as run:
            with self.assertRaises(OSError):
                self.send()
            with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
                self.send()
        run.assert_called_once()
        self.assertEqual(self.receipt()["audio"], "unconfirmed")

    def test_split_audio_requires_confirmation_for_every_part(self):
        output = "Split audio into 2 segment(s).\n"
        for index in range(2):
            name = f"audio_part_{index:02d}.ogg"
            output += f"Sending {name} to {self.phone}...\nSent as voice note: {name}\n"
        with patch("pipeline.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.send()
        self.assertEqual(self.receipt()["audio"], "sent")
        self.assertEqual(len(self.receipt()["confirmed_audio_files"]), 2)
        incomplete = output.replace("Sent as voice note: audio_part_01.ogg", "")
        with patch("pipeline.subprocess.run", return_value=subprocess.CompletedProcess([], 0, incomplete, "")):
            with self.assertRaises(RuntimeError):
                self.send(resend=True)
        self.assertEqual(self.receipt()["audio"], "partial")

    def test_parallel_dispatch_is_blocked(self):
        key = pipeline.text_hash(json.dumps([self.phone, pipeline.file_hash(self.audio), pipeline.text_hash("കാർഡ്")]))
        self.receipts.mkdir()
        with (self.receipts / f"{key}.lock").open("a") as lock:
            pipeline.fcntl.flock(lock, pipeline.fcntl.LOCK_EX | pipeline.fcntl.LOCK_NB)
            with patch("pipeline.subprocess.run") as run, self.assertRaisesRegex(RuntimeError, "already running"):
                self.send()
            run.assert_not_called()


class MainTests(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source.txt"
        self.source.write_text("الحمد لله", encoding="utf-8")
        self.translated = self.root / "existing_malayalam.txt"
        self.translated.write_text(NARRATION, encoding="utf-8")
        self.meta = self.root / "existing_meta.json"
        self.meta.write_text(json.dumps(asdict(METADATA)), encoding="utf-8")
        self.output = self.root / "output"

    def main(self, *args):
        with patch("sys.argv", [
            "pipeline.py", "--output-dir", str(self.output), "--sermon-date", "2026-10-16", *args
        ]):
            pipeline.main()

    def translation(self):
        return METADATA, NARRATION, [{"id": "p0001", "source": "الحمد لله", "kind": "sermon", "malayalam": NARRATION}]

    def test_dry_run_has_no_writes_or_external_calls(self):
        with patch("pipeline.call_llm_translate") as translate, patch(
            "pipeline.prepare_audio"
        ) as audio, patch("pipeline.send_to_whatsapp") as send:
            self.main("--input", str(self.source), "--dry-run")
        self.assertFalse(self.output.exists())
        translate.assert_not_called()
        audio.assert_not_called()
        send.assert_not_called()

    def test_skip_translate_uses_supplied_file_without_double_suffix(self):
        with patch("pipeline.call_llm_translate") as translate, patch("pipeline.prepare_audio") as audio:
            self.main("--input", str(self.translated), "--skip-translate", "--skip-tts", "--skip-send")
        saved = next(self.output.glob("*/malayalam.txt"))
        self.assertEqual(saved.read_text(encoding="utf-8").strip(), NARRATION)
        translate.assert_not_called()
        audio.assert_not_called()

    def test_existing_text_requires_metadata(self):
        self.meta.unlink()
        with self.assertRaisesRegex(FileNotFoundError, "Metadata"):
            self.main("--translated-input", str(self.translated), "--skip-tts")
        self.assertFalse(self.output.exists())

    def test_no_send_by_default_and_explicit_send_uses_correct_audio(self):
        audio = self.root / "new.ogg"
        audio.write_bytes(b"test")
        with patch("pipeline.prepare_audio", return_value=(audio, 920.4)), patch(
            "pipeline.send_to_whatsapp"
        ) as send:
            self.main("--translated-input", str(self.translated))
            send.assert_not_called()
            self.main("--translated-input", str(self.translated), "--send")
        self.assertEqual(send.call_args.args[1], audio)
        self.assertIn("16-10-2026", send.call_args.args[2])
        self.assertIn("15:20", send.call_args.args[2])

    def test_skip_tts_send_rejects_missing_audio(self):
        with patch("pipeline.send_to_whatsapp") as send:
            with self.assertRaisesRegex(ValueError, "exact narration"):
                self.main("--translated-input", str(self.translated), "--skip-tts", "--send")
        send.assert_not_called()

    def test_skip_tts_send_uses_only_bound_verified_audio(self):
        self.main("--translated-input", str(self.translated), "--skip-tts")
        run_dir = next(self.output.glob("*/state.json")).parent
        narration = run_dir / "malayalam.txt"
        audio = run_dir / "cached.ogg"
        audio.write_bytes(b"test-audio")
        state = pipeline.read_json_object(run_dir / "state.json")
        state["audio"] = {
            "filename": audio.name, "narration_sha256": pipeline.file_hash(narration),
            "sha256": pipeline.file_hash(audio),
        }
        pipeline.write_json(run_dir / "state.json", state)
        with patch("pipeline.validate_audio", return_value=920.4), patch("pipeline.send_to_whatsapp") as send:
            self.main("--translated-input", str(self.translated), "--skip-tts", "--send")
        self.assertEqual(send.call_args.args[1], audio)
        for damaged in ("binding", "file"):
            with self.subTest(damaged=damaged):
                if damaged == "binding":
                    state["audio"]["narration_sha256"] = "old-narration"
                else:
                    state["audio"]["narration_sha256"] = pipeline.file_hash(narration)
                    audio.write_bytes(b"changed-audio")
                pipeline.write_json(run_dir / "state.json", state)
                with patch("pipeline.send_to_whatsapp") as send, self.assertRaises(ValueError):
                    self.main("--translated-input", str(self.translated), "--skip-tts", "--send")
                send.assert_not_called()

    def test_audio_only_uses_explicit_file_and_metadata(self):
        audio = self.root / "new.ogg"
        audio.write_bytes(b"test")
        with patch("pipeline.validate_audio", return_value=920.4), patch(
            "pipeline.prepare_audio"
        ) as tts, patch("pipeline.call_llm_translate") as translate, patch("pipeline.send_to_whatsapp") as send:
            self.main("--audio-input", str(audio), "--metadata", str(self.meta), "--send")
        self.assertEqual(send.call_args.args[1], audio.resolve())
        tts.assert_not_called()
        translate.assert_not_called()

    def test_translation_cache_preserves_manual_edits(self):
        args = ("--input", str(self.source), "--api-key", "test-key", "--skip-tts")
        with patch("pipeline.call_llm_translate", return_value=self.translation()) as translate:
            self.main(*args)
            narration = next(self.output.glob("*/malayalam.txt"))
            edited = NARRATION + "\n\nകൂടുതൽ വിശദാംശങ്ങൾ."
            narration.write_text(edited, encoding="utf-8")
            self.main(*args)
        translate.assert_called_once()
        self.assertEqual(narration.read_text(encoding="utf-8").strip(), edited)

    def test_same_filename_different_source_creates_distinct_runs(self):
        with patch("pipeline.call_llm_translate", return_value=self.translation()) as translate:
            self.main("--input", str(self.source), "--api-key", "test-key", "--skip-tts")
            self.source.write_text("الحمد لله رب العالمين", encoding="utf-8")
            self.main("--input", str(self.source), "--api-key", "test-key", "--skip-tts")
        self.assertEqual(translate.call_count, 2)
        self.assertEqual(len(list(self.output.glob("*/malayalam.txt"))), 2)

    def test_model_and_prompt_changes_invalidate_translation_cache(self):
        prompt = self.root / "prompt.txt"
        prompt.write_text("first prompt")
        args = ("--input", str(self.source), "--api-key", "test-key", "--skip-tts")
        with patch("pipeline.DEFAULT_SYSTEM_PROMPT", prompt), patch(
            "pipeline.call_llm_translate", return_value=self.translation()
        ) as translate:
            self.main(*args)
            self.main(*args, "--model", "new-model")
            prompt.write_text("changed prompt")
            self.main(*args, "--model", "new-model")
        self.assertEqual(translate.call_count, 3)

    def test_no_key_fails_without_artifacts(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaisesRegex(ValueError, "KHUTBAH_API_KEY"):
            self.main("--input", str(self.source), "--skip-tts")
        self.assertFalse(self.output.exists())

    def test_retranslate_is_explicit(self):
        args = ("--input", str(self.source), "--api-key", "test-key", "--skip-tts")
        with patch("pipeline.call_llm_translate", return_value=self.translation()) as translate:
            self.main(*args)
            self.main(*args, "--retranslate")
        self.assertEqual(translate.call_count, 2)

    def test_invalid_flag_combinations_and_phone_fail(self):
        cases = [
            ["--translated-input", str(self.translated), "--retranslate"],
            ["--translated-input", str(self.translated), "--rebuild-audio", "--skip-tts"],
            ["--translated-input", str(self.translated), "--resend"],
            ["--translated-input", str(self.translated), "--skip-translate"],
            ["--translated-input", str(self.translated), "--send", "--skip-send"],
            ["--translated-input", str(self.translated), "--send", "--phone", "bad"],
        ]
        for flags in cases:
            with self.subTest(flags=flags), patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.main(*flags)

    def test_invalid_sermon_dates_fail(self):
        for value in ("20261016", "16-10-2026", "2026-02-30"):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                self.main("--input", str(self.source), "--sermon-date", value, "--dry-run")

    def test_cli_reports_errors_and_exits_nonzero(self):
        with patch("pipeline.main", side_effect=RuntimeError("explicit failure")), self.assertRaises(SystemExit) as error:
            pipeline.run()
        self.assertEqual(error.exception.code, 1)
        self.assertIn("Error: explicit failure", self.stderr.getvalue())


class CardTests(QuietTestCase):
    def test_explicit_sermon_date_weekday_and_duration(self):
        card = card_formatter.format_whatsapp_card(
            **asdict(METADATA), target_date=datetime(2026, 10, 16), duration_seconds=920.4
        )
        self.assertIn("16-10-2026", card)
        self.assertIn("വെള്ളി", card)
        self.assertIn("15:20", card)
        self.assertIn("ʜɪᴊʀɪ - 1448", card)
        self.assertIn("ᴊᴜᴍᴀᴅᴀ ᴀʟ-ᴜʟᴀ - 05", card)

    def test_missing_hijri_dependency_does_not_fabricate_date(self):
        with patch("card_formatter.Gregorian", None), self.assertRaisesRegex(RuntimeError, "unavailable"):
            card_formatter.format_whatsapp_card(**asdict(METADATA))

    def test_invalid_duration_fails(self):
        for duration in (0, -1, float("nan"), float("inf")):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                card_formatter.format_whatsapp_card(**asdict(METADATA), duration_seconds=duration)


if __name__ == "__main__":
    unittest.main()
