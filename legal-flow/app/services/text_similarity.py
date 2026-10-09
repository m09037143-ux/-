"""Насколько черновик похож на оригинал — подсказка редактору (ТЗ: самостоятельный текст, а не
пересказ «слово в слово»). Считается на лету по загруженному оригиналу, нигде не хранится."""

import difflib
import re

NGRAM = 4
WARN_SHARE = 0.35  # доля 4-словных фраз черновика, найденных в оригинале
WARN_RUN_WORDS = 25  # самая длинная дословная цепочка слов подряд


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", text.lower())


def text_overlap(draft: str, source: str) -> dict:
    dw, sw = _words(draft), _words(source)
    grams = lambda w: {tuple(w[i : i + NGRAM]) for i in range(len(w) - NGRAM + 1)}  # noqa: E731
    dg, sg = grams(dw), grams(sw)
    share = len(dg & sg) / len(dg) if dg else 0.0
    match = difflib.SequenceMatcher(None, dw, sw, autojunk=False).find_longest_match(0, len(dw), 0, len(sw))
    run_text = " ".join(dw[match.a : match.a + match.size])
    return {
        "share": round(share, 3),
        "longest_run_words": match.size,
        "longest_run_text": run_text[:300],
        "warning": share >= WARN_SHARE or match.size >= WARN_RUN_WORDS,
    }
