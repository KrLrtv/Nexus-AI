"""NEXUS R8: text-only Russian buzzer TTS; never pronounce Markdown markup.

Pure transform; unknown LaTeX/math is skipped instead of spoken symbol-by-symbol.
"""
from __future__ import annotations
import re
import unicodedata

MATH_BLOCK = re.compile(r'\$\$.*?\$\$|\\\[.*?\\\]', re.S)
INLINE_MATH = re.compile(r'\$[^$\n]+\$|\\\([^\n]*?\\\)', re.S)
LINK = re.compile(r'\[([^\]]+)\]\((?:[^()]*|\([^()]*\))*\)')


def clean_speech(text: str, max_chars: int = 180) -> str:
    value = str(text or '')
    value = MATH_BLOCK.sub(' ', value)
    value = INLINE_MATH.sub(' ', value)
    value = re.sub(r'```.*?```', ' ', value, flags=re.S)
    value = re.sub(r'`[^`]*`', ' ', value)
    value = LINK.sub(r'\1', value)
    value = re.sub(r'https?://\S+', ' ', value)
    # Remove TeX commands and mathematical expressions rather than making the
    # piezo say 'asterisk', 'open parenthesis', 'backslash' etc.
    value = re.sub(r'\\[A-Za-z]+\b', ' ', value)
    keep = []
    for char in value:
        category = unicodedata.category(char)
        if category[0] in ('L', 'N') or char in ' \n\t.,!?;:–-':
            keep.append(char)
        elif category[0] == 'M':
            keep.append(char)
        else:
            keep.append(' ')
    text = ' '.join(''.join(keep).split())
    # Truncate at a word boundary, avoiding an orphaned word fragment.
    if len(text) > max_chars:
        chunk = text[:max_chars + 1]
        text = chunk.rsplit(' ', 1)[0] or text[:max_chars]
    return text.strip(' .,;:-')
