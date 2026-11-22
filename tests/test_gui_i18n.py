"""Portable checks for the desktop assistant's translation catalogs and saved preferences."""
from collections import Counter
from pathlib import Path
from string import Formatter
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from gui.i18n import LANGUAGES, Translator, lines_of
from gui.wizard import Saved, STATE_VERSION
from messages import joined, message, phrase


class CatalogTests(unittest.TestCase):
    def test_catalogs_cover_the_same_messages_and_retain_named_fields(self):
        def fields(s):
            return Counter((name, fmt, conversion) for _, name, fmt, conversion in Formatter().parse(s)
                           if name is not None)

        reference = None
        for language in LANGUAGES:
            if language == "en":
                continue
            catalog = Translator(language).catalog
            if reference is None:
                reference = set(catalog)
            self.assertEqual(set(catalog), reference)
            for source, translation in catalog.items():
                with self.subTest(language=language, source=source):
                    self.assertTrue(translation.strip())
                    self.assertEqual(fields(source), fields(translation))

    def test_compound_messages_translate_prose_and_keep_data_literal(self):
        spanish = Translator("es")
        # The device is literally named “Next”. Only the status is a translatable field.
        source = joined("\n", [
            message("Selected: {card}", card="Next"),
            message("Wait: {busy}", busy=phrase("Converting...")),
        ])
        headline, body = lines_of(source)
        self.assertIn("Next", spanish.render(headline))
        self.assertIn(spanish.render("Converting..."), spanish.render(body))
        self.assertEqual(Translator().render(source), "Selected: Next\nWait: Converting...")

    def test_old_unknown_or_malformed_language_uses_english(self):
        for extra in ({}, {"language": "unknown"}, {"language": []}):
            saved = Saved.from_json({"version": STATE_VERSION, "camera_confirmed": True, **extra})
            self.assertEqual(saved.language, "en")
            self.assertTrue(saved.camera_confirmed)
