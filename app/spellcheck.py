"""Red-squiggly spell-check for the Krea2 tab's free-text fields.

Catches genuine typos (non-dictionary words) - it can't catch a correctly
spelled but wrong word (e.g. "clock" instead of "cloak"), since "clock" is a
real word. That category needs a smarter, context-aware check; this is just
the standard dictionary-based kind, which is still worth having on its own.

Capitalized words are skipped entirely - character names and fantasy place
names would otherwise be flagged constantly as false positives.
"""

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit
from spellchecker import SpellChecker

_WORD_RE = re.compile(r"[A-Za-z']+")

# One shared dictionary instance - loading it has real startup cost, no
# reason to pay it once per field.
_checker = SpellChecker()


class SpellCheckHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self._format = QTextCharFormat()
        self._format.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        self._format.setUnderlineColor(Qt.red)

    def highlightBlock(self, text):
        words = _WORD_RE.findall(text)
        # Capitalized (incl. all-caps) words are assumed proper nouns/acronyms
        # and skipped - checking only the lowercase ones keeps false positives
        # on names like "Arya" or "Winterfell" from drowning out real typos.
        candidates = {w for w in words if w[:1].islower() and len(w) > 2}
        unknown = _checker.unknown(candidates) if candidates else set()
        if not unknown:
            return
        for match in _WORD_RE.finditer(text):
            word = match.group()
            if word in unknown:
                self.setFormat(match.start(), len(word), self._format)


class SpellCheckTextEdit(QPlainTextEdit):
    """QPlainTextEdit whose right-click menu offers spelling corrections for
    the word under the cursor, above the usual Cut/Copy/Paste - the missing
    piece SpellCheckHighlighter alone doesn't provide (it can only underline,
    it has no menu to hang suggestions off of)."""

    MAX_SUGGESTIONS = 8

    def contextMenuEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()

        menu = self.createStandardContextMenu()

        if word and word[:1].islower() and len(word) > 2 and _WORD_RE.fullmatch(word) \
                and _checker.unknown([word]):
            suggestions = sorted(_checker.candidates(word) or [])[: self.MAX_SUGGESTIONS]
            if suggestions:
                anchor = menu.actions()[0] if menu.actions() else None
                to_insert = []
                for suggestion in suggestions:
                    action = QAction(suggestion, menu)
                    action.triggered.connect(
                        lambda checked=False, s=suggestion, c=QTextCursor(cursor): self._replace_word(c, s)
                    )
                    to_insert.append(action)
                separator = QAction(menu)
                separator.setSeparator(True)
                to_insert.append(separator)
                menu.insertActions(anchor, to_insert)

        menu.exec(event.globalPos())

    def _replace_word(self, cursor, replacement):
        cursor.insertText(replacement)
