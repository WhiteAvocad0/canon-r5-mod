"""Localize assistant text at display time, keeping paths, IDs and command output verbatim."""
from __future__ import annotations

import json
from pathlib import Path

from messages import Message, phrase


LANGUAGES = {"en": "English", "zh_CN": "简体中文", "es": "Español"}


def lines_of(value):
    """Keep a multiline report's heading and body independently renderable."""
    value = phrase(value)

    def split(text):
        parts = [line for line in text.splitlines() if line.strip()]
        return (parts[0], "\n".join(parts[1:])) if parts else ("", "")

    english = split(value)
    return tuple(Message(english[i], lambda catalog, i=i: split(value.translate_with(catalog))[i]) for i in (0, 1))


class Translator:
    """Keep source text alongside each widget so a language change only repaints it."""

    def __init__(self, language="en"):
        self.labels = {}
        self.select(language)

    def select(self, language):
        self.language = language if language in LANGUAGES else "en"
        self.catalog = {}
        if self.language != "en":
            path = Path(__file__).with_name("locales") / (self.language + ".json")
            self.catalog = json.loads(path.read_text(encoding="utf-8"))
        for widget, source in self.labels.items():
            widget.configure(text=self.render(source))

    def render(self, source):
        return phrase(source).translate_with(self.catalog)

    def widget(self, factory, *args, text, **options):
        widget = factory(*args, text=self.render(text), **options)
        self.labels[widget] = text
        return widget

    def configure(self, widget, *, text, **options):
        self.labels[widget] = text
        widget.configure(text=self.render(text), **options)
