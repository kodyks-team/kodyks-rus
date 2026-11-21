import os
import json
import random

from config import BASE_DIR, NO_REPEAT_WINDOW

_pairs_cache = None


def load_pairs():
    """Загружает true.json и false.json в список словарей вида
    {"word": str, "false_variants": [str, ...]}.
    """
    global _pairs_cache
    if _pairs_cache is None:
        with open(os.path.join(BASE_DIR, "true.json"), encoding="utf-8") as f:
            true_words = json.load(f)
        with open(os.path.join(BASE_DIR, "false.json"), encoding="utf-8") as f:
            false_data = json.load(f)

        if len(true_words) != len(false_data):
            raise RuntimeError(
                f"true.json ({len(true_words)}) и false.json ({len(false_data)}) "
                f"должны иметь одинаковую длину"
            )

        correct_words = {norm(word) for word in true_words if word}
        pairs = []
        for idx, (t, f_variants) in enumerate(zip(true_words, false_data)):
            t = (t or "").strip()
            if not t:
                continue
            if isinstance(f_variants, str):
                variants = [f_variants]
            elif isinstance(f_variants, list):
                variants = f_variants
            else:
                raise RuntimeError(
                    f"false.json[{idx}] должен быть строкой или массивом строк"
                )
            clean = []
            seen = set()
            target_norm = norm(t)
            for v in variants:
                v = "".join((v or "").split())
                variant_norm = norm(v)
                if not variant_norm:
                    continue
                if variant_norm == target_norm:
                    continue
                if variant_norm in correct_words:
                    continue
                if variant_norm in seen:
                    continue
                seen.add(variant_norm)
                clean.append(v)
            if not clean:
                raise RuntimeError(
                    f"false.json[{idx}] для слова «{t}» не содержит "
                    "допустимых ошибочных вариантов"
                )
            pairs.append({"word": t, "false_variants": clean})

        _pairs_cache = pairs
    return _pairs_cache


def norm(s):
    """Нормализация для сравнения: ё→е, нижний регистр, только буквы."""
    s = (s or "").lower().replace("ё", "е")
    return "".join(ch for ch in s if ch.isalpha())


def _pick_rounds(state, count):
    all_pairs = load_pairs()
    total = len(all_pairs)

    recent = list(state.get("recent", []))
    recent_set = set(recent)

    pool = [i for i in range(total) if i not in recent_set]

    if len(pool) < count:
        hard_block = set(recent[-10:])
        pool = [i for i in range(total) if i not in hard_block]

    random.shuffle(pool)
    chosen = pool[:count]

    recent.extend(chosen)
    state["recent"] = recent[-NO_REPEAT_WINDOW:]

    return chosen


def _make_round(word_idx):
    pair = load_pairs()[word_idx]
    correct = pair["word"]
    variants = pair["false_variants"]

    if variants and random.random() < 0.5:
        shown = random.choice(variants)
        shown_is_correct = False
    else:
        shown = correct
        shown_is_correct = True

    return {
        "word_idx": word_idx,
        "shown": shown,
        "correct": correct,
        "shown_is_correct": shown_is_correct,
        "guessed": None,
        "fix_attempted": False,
        "fix_ok": None,
        "actions_correct": 0,
        "actions_wrong": 0,
    }


def new_game_state(prev_state, num_rounds):
    base = {
        "rounds": [],
        "current": 0,
        "score": 0,
        "finished": False,
        "recent": list(prev_state.get("recent", [])) if prev_state else [],
        "errors": [],
    }
    chosen = _pick_rounds(base, num_rounds)
    base["rounds"] = [_make_round(i) for i in chosen]
    return base