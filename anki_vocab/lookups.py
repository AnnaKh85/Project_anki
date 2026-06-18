# -*- coding: utf-8 -*-
"""
Бесплатные источники данных для карточек (без оплаты, без подписки, без ключей):

- перевод            -> deep-translator (Google Translate без API-ключа)
- род существительного -> de.wiktionary.org (MediaWiki API)
- пример предложения  -> tatoeba.org (api_v0)
- картинка            -> Openverse API (только CC-лицензии, без ключа)
- озвучка             -> edge-tts (голоса Microsoft Edge, без ключа)

Все функции — best-effort: при сбое возвращают None / "", а не бросают
исключение наверх. Так один неудачный запрос не обрывает обработку всего
списка слов — такие места просто помечаются тегом needs-review.
"""
import asyncio
import base64
import html
import logging
import os
import re
import socket
import tempfile
from typing import Optional

import requests

from . import config

HEADERS = {"User-Agent": "anki-vocab-pipeline/1.0 (личный учебный проект)"}
logger = logging.getLogger(__name__)

# Некоторые библиотеки (deep-translator, edge-tts) не дают задать свой
# таймаут — если сервер не отвечает, вызов может зависнуть навсегда.
# Этот глобальный таймаут на уровне сокетов — страховка от такого зависания.
socket.setdefaulttimeout(15)


def bare_word(word: str) -> str:
    """Убирает артикль der/die/das, если он есть."""
    parts = word.split()
    if len(parts) > 1 and parts[0].lower() in ("der", "die", "das"):
        return " ".join(parts[1:])
    return word


# ---------- Перевод ----------

def translate(text: str, target: str = config.TARGET_LANG) -> str:
    if not text:
        return ""
    logger.debug("Перевод (%s): %r", target, text[:60])
    from deep_translator import GoogleTranslator
    try:
        result = GoogleTranslator(source=config.SOURCE_LANG, target=target).translate(text)
        logger.debug("Перевод готов: %r", (result or "")[:60])
        return result
    except Exception as e:
        logger.debug("Перевод не удался для %r: %s", text[:60], e)
        return ""


# ---------- Род существительного ----------

_GENUS_MAP = {"m": "der", "f": "die", "n": "das"}


def lookup_genus(word: str) -> Optional[str]:
    """Возвращает 'der'/'die'/'das' или None, если не удалось определить."""
    logger.debug("Wiktionary: ищу род для %r", word)
    try:
        resp = requests.get(
            "https://de.wiktionary.org/w/api.php",
            params={
                "action": "query",
                "titles": word,
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "format": "json",
            },
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        pages = resp.json()["query"]["pages"]
        page = next(iter(pages.values()))
        if "missing" in page:
            logger.debug("Wiktionary: статья %r не найдена", word)
            return None
        wikitext = page["revisions"][0]["slots"]["main"]["*"]
        m = re.search(r"Substantiv[^\n}]*Genus=([mfn])", wikitext)
        if not m:
            m = re.search(r"\bGenus=([mfn])\b", wikitext)
        if m:
            genus = _GENUS_MAP[m.group(1)]
            logger.debug("Wiktionary: %r -> %s", word, genus)
            return genus
        logger.debug("Wiktionary: род для %r не распознан в тексте статьи", word)
    except Exception as e:
        logger.debug("Wiktionary: ошибка для %r: %s", word, e)
    return None


def ensure_article(word: str) -> str:
    """
    Если word — существительное без артикля (с большой буквы, одно слово),
    пытается определить род и приставить der/die/das.
    Если артикль уже есть, слово многословное или это не существительное —
    возвращает word без изменений.
    """
    word = word.strip()
    if not word:
        return word
    parts = word.split()
    if parts[0].lower() in ("der", "die", "das"):
        return word  # артикль уже есть
    if len(parts) > 1:
        return word  # многословное выражение — не трогаем
    if not word[0].isupper():
        return word  # по орфографии немецкого — не существительное
    genus = lookup_genus(word)
    if genus:
        return f"{genus} {word}"
    return word  # не удалось определить — оставляем как есть (на ревью)


# ---------- Пример предложения (Tatoeba) ----------

def find_example_sentence(word: str) -> Optional[str]:
    """Возвращает немецкое предложение, содержащее слово, или None."""
    bare = bare_word(word)
    logger.debug("Tatoeba: ищу предложение для %r", bare)
    try:
        resp = requests.get(
            "https://tatoeba.org/eng/api_v0/search",
            params={"from": "deu", "query": bare},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        candidates = [
            html.unescape(r["text"])
            for r in results
            if bare.lower() in r.get("text", "").lower()
        ]
        candidates.sort(key=len)  # короче — обычно ближе к A1-B1
        if candidates:
            logger.debug("Tatoeba: найдено %d вариантов для %r, беру самый короткий", len(candidates), bare)
            return candidates[0]
        logger.debug("Tatoeba: для %r подходящих предложений не найдено", bare)
    except Exception as e:
        logger.debug("Tatoeba: ошибка для %r: %s", bare, e)
    return None


# ---------- Картинка (Openverse) ----------

def find_image_bytes(query: str) -> Optional[bytes]:
    logger.debug("Openverse: ищу картинку для %r", query)
    try:
        resp = requests.get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": 5, "license_type": "all-cc"},
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        logger.debug("Openverse: найдено %d кандидатов для %r", len(results), query)
        for r in results:
            url = r.get("url") or r.get("thumbnail")
            if not url:
                continue
            img = requests.get(url, headers=HEADERS, timeout=10)
            if img.ok and img.content:
                logger.debug("Openverse: картинка для %r скачана (%d байт)", query, len(img.content))
                return img.content
        logger.debug("Openverse: ни один кандидат для %r не скачался", query)
    except Exception as e:
        logger.debug("Openverse: ошибка для %r: %s", query, e)
    return None


# ---------- Озвучка (edge-tts) ----------

def synthesize_tts(text: str) -> Optional[bytes]:
    if not text:
        return None
    logger.debug("edge-tts: озвучиваю %r", text[:60])
    import edge_tts

    async def _run(path):
        communicate = edge_tts.Communicate(text, config.TTS_VOICE_DE)
        await asyncio.wait_for(communicate.save(path), timeout=20)

    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        asyncio.run(_run(path))
        with open(path, "rb") as f:
            data = f.read()
        logger.debug("edge-tts: готово (%d байт) для %r", len(data) if data else 0, text[:60])
        return data if data else None
    except Exception as e:
        logger.debug("edge-tts: ошибка для %r: %s", text[:60], e)
        return None
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")
