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

def lookup_noun_forms(word: str) -> Optional[tuple[str, str]]:
    """
    Возвращает (артикль, множественное_число) для существительного через Groq,
    или None если слово не существительное или запрос не удался.
    Пример: 'Buch' -> ('das', 'die Bücher')
    Пример без мн.ч.: 'Obst' -> ('das', '')
    """
    if not config.GROQ_API_KEY:
        logger.warning("Groq: GROQ_API_KEY не задан — артикль не будет определён.")
        return None

    logger.info("Groq: определяю артикль и мн.ч. для %r", word)
    prompt = (
        f'Немецкое слово: "{word}"\n'
        f'Если это существительное, ответь строго в формате двух строк:\n'
        f'ART: <der/die/das>\n'
        f'PLU: die <форма множественного числа>\n'
        f'Если у существительного нет формы множественного числа (например, das Obst, das Wasser) — '
        f'напиши PLU: нет\n'
        f'Если это не существительное — ответь только: нет.'
    )
    try:
        from groq import Groq
        client = Groq(api_key=config.GROQ_API_KEY)
        response = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=30,
        )
        text = response.choices[0].message.content.strip()
        logger.debug("Groq noun forms raw: %r", text)

        article, plural = "", ""
        for line in text.splitlines():
            if line.startswith("ART:"):
                article = line[4:].strip().lower()
            elif line.startswith("PLU:"):
                plu_val = line[4:].strip()
                plu_lower = plu_val.lower()
                if plu_lower in ("нет", "keine", "kein", "-", "—", ""):
                    plural = ""  # нет формы множественного числа
                elif plu_lower.startswith("die ") and len(plu_val) > 4:
                    plural = plu_val  # правильный формат: "die Bücher"
                elif plu_val:
                    plural = f"die {plu_val}"  # Groq забыл артикль — добавляем

        if article in ("der", "die", "das"):
            logger.info("Groq: %r -> %s, мн.ч.: %r", word, article, plural or "нет")
            return article, plural

        logger.info("Groq: %r — не существительное (ответ: %r)", word, text)
    except Exception as e:
        logger.warning("Groq: ошибка определения форм для %r: %s", word, e)
    return None


def ensure_article(word: str) -> str:
    """
    Определяет артикль и форму множественного числа через Groq.
    - Нет артикля, заглавная буква → определяем всё
    - Есть артикль, нет запятой (нет мн.ч.) → добираем мн.ч.
    - Уже есть артикль и мн.ч. (есть запятая) → не трогаем
    - Не существительное / многословное → не трогаем
    """
    word = word.strip()
    if not word:
        return word

    # Уже полностью заполнено: "die Ampel, die Ampeln"
    if "," in word:
        return word

    parts = word.split()
    has_article = parts[0].lower() in ("der", "die", "das")

    if has_article:
        # Есть артикль, но нет множественного числа — добираем мн.ч.
        bare = " ".join(parts[1:])
        forms = lookup_noun_forms(bare)
        if forms:
            _, plural = forms
            if plural:
                return f"{word}, {plural}"
        return word  # мн.ч. нет или не удалось определить

    if len(parts) > 1:
        return word  # многословное выражение без артикля — не трогаем

    if not word[0].isupper():
        return word  # не существительное по орфографии

    forms = lookup_noun_forms(word)
    if forms:
        article, plural = forms
        if plural:
            return f"{article} {word}, {plural}"
        return f"{article} {word}"
    return word  # не удалось определить — оставляем на ревью


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
        f'Составь одно простое немецкое предложение уровня A1-B1, '
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


# ---------- Озвучка (gTTS — Google Translate TTS, без API ключа) ----------

def synthesize_tts(text: str) -> Optional[bytes]:
    """Генерирует MP3 через Google Translate TTS (тот же сервис, что AwesomeTTS)."""
    if not text:
        return None
    logger.info("gTTS: озвучиваю %r", text[:60])
    path = None
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang="de", slow=False)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        tts.save(path)
        with open(path, "rb") as f:
            data = f.read()
        logger.info("gTTS: готово (%d байт)", len(data) if data else 0)
        return data if data else None
    except Exception as e:
        logger.warning("gTTS: ошибка для %r: %s", text[:60], e)
        return None
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")
