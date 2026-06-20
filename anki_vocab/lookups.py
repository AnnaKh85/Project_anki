# -*- coding: utf-8 -*-
"""
Бесплатные источники данных для карточек (без оплаты, без подписки, без ключей):

- перевод            -> deep-translator (Google Translate без API-ключа)
- род существительного -> de.wiktionary.org (MediaWiki API)
- пример предложения  -> Groq AI (бесплатный ключ на console.groq.com)
- картинка            -> Openverse API (только CC-лицензии, без ключа)
- озвучка             -> edge-tts (голоса Microsoft Edge, без ключа)

Все функции — best-effort: при сбое возвращают None / "", а не бросают
исключение наверх. Так один неудачный запрос не обрывает обработку всего
списка слов — такие места просто помечаются тегом needs-review.
"""
import asyncio
import base64
import logging
import os
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

def lookup_genus(word: str) -> Optional[str]:
    """Возвращает 'der'/'die'/'das' через Groq или None, если не существительное."""
    if not config.GROQ_API_KEY:
        logger.warning("Groq: GROQ_API_KEY не задан — артикль не будет определён.")
        return None

    logger.info("Groq: определяю артикль для %r", word)
    prompt = (
        f'Немецкое слово: "{word}"\n'
        f'Если это существительное, ответь только одним словом: der, die или das.\n'
        f'Если это не существительное — ответь только: нет.'
    )
    try:
        from groq import Groq
        client = Groq(api_key=config.GROQ_API_KEY)
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=10,
        )
        answer = response.choices[0].message.content.strip().lower().split()[0]
        if answer in ("der", "die", "das"):
            logger.info("Groq: артикль для %r -> %s", word, answer)
            return answer
        logger.info("Groq: %r — не существительное (ответ: %r)", word, answer)
    except Exception as e:
        logger.warning("Groq: ошибка определения артикля для %r: %s", word, e)
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


# ---------- Пример предложения + перевод (Groq AI) ----------

def generate_sentence_and_translation(word: str) -> tuple[str, str]:
    """
    Генерирует немецкое предложение уровня A1-B1 с данным словом
    и его перевод на русский. Возвращает (sentence, translation).
    При ошибке возвращает ("", "").
    """
    if not config.GROQ_API_KEY:
        logger.warning("Groq: GROQ_API_KEY не задан — предложение не будет сгенерировано. "
                       "Получите бесплатный ключ на https://console.groq.com и задайте "
                       "переменную окружения GROQ_API_KEY.")
        return "", ""

    bare = bare_word(word)
    logger.info("Groq: генерирую предложение для %r", bare)

    prompt = (
        f'Составь одно простое немецкое предложение уровня A2-B1, '
        f'в котором используется слово "{bare}". '
        f'Ответь строго в формате двух строк:\n'
        f'DE: <немецкое предложение>\n'
        f'RU: <перевод на русский>'
    )

    try:
        from groq import Groq
        client = Groq(api_key=config.GROQ_API_KEY)
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=256,
        )
        text = response.choices[0].message.content.strip()
        logger.info("Groq: ответ получен")
        logger.debug("Groq raw: %r", text)

        sentence, translation = "", ""
        for line in text.splitlines():
            if line.startswith("DE:"):
                sentence = line[3:].strip()
            elif line.startswith("RU:"):
                translation = line[3:].strip()

        if sentence:
            logger.info("Groq: предложение: %r", sentence)
            logger.info("Groq: перевод:     %r", translation)
            return sentence, translation

        logger.warning("Groq: не удалось разобрать ответ: %r", text)
    except Exception as e:
        logger.warning("Groq: ошибка для %r: %s", bare, e)

    return "", ""


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
    logger.info("edge-tts: озвучиваю %r", text[:60])
    try:
        import edge_tts
        logger.info("edge-tts: библиотека импортирована успешно")
    except ImportError as e:
        logger.warning("edge-tts: не удалось импортировать библиотеку: %s", e)
        return None

    async def _run(path):
        logger.info("edge-tts: создаю Communicate, голос=%r", config.TTS_VOICE_DE)
        communicate = edge_tts.Communicate(text, config.TTS_VOICE_DE)
        logger.info("edge-tts: вызываю communicate.save('%s')...", path)
        await asyncio.wait_for(communicate.save(path), timeout=20)
        logger.info("edge-tts: communicate.save завершён")

    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        logger.info("edge-tts: запускаю asyncio.run (временный файл: %s)", path)
        asyncio.run(_run(path))
        logger.info("edge-tts: asyncio.run завершён")
        with open(path, "rb") as f:
            data = f.read()
        logger.info("edge-tts: файл прочитан, %d байт", len(data) if data else 0)
        return data if data else None
    except BaseException as e:
        logger.warning("edge-tts: ошибка (%s) для %r: %s", type(e).__name__, text[:60], e)
        return None
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")
