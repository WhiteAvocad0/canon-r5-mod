"""English-compatible messages that retain fields for the desktop assistant’s translations."""
from __future__ import annotations


class Message(str):
    """A string in English for existing callers, with the information to render it again."""

    def __new__(cls, english, translate):
        value = super().__new__(cls, english)
        value.translate_with = translate
        return value


def phrase(value):
    """Identify a field as interface text. Unmarked formatting fields are user or technical data."""
    if isinstance(value, Message):
        return value
    return Message(value, lambda catalog: catalog.get(value, value))


def error_text(error):
    """Keep the message carried by an application error, or the OS's full diagnostic."""
    if len(error.args) == 1 and isinstance(error.args[0], str):
        return phrase(error.args[0])
    return str(error)


def message(template, /, **fields):
    fields = {key: error_text(value) if isinstance(value, Exception) else value for key, value in fields.items()}

    def translate(catalog):
        values = {key: value.translate_with(catalog) if isinstance(value, Message) else value
                  for key, value in fields.items()}
        return catalog.get(template, template).format_map(values)

    return Message(template.format_map(fields), translate)


def joined(separator, values):
    parts = tuple(phrase(v) for v in values)
    return Message(separator.join(parts), lambda catalog: separator.join(p.translate_with(catalog) for p in parts))
