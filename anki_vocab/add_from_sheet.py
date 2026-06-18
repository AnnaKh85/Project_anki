# -*- coding: utf-8 -*-
"""
Скрипт 1: читает список немецких слов из Google Sheet (CSV-экспорт),
проверяет, каких слов ещё нет в Anki, и создаёт для них карточки
(оба шаблона — "Распознавание" и "Письмо" — создаются автоматически,
т.к. это одна заметка одного Note Type).

Запуск:
    python -m anki_vocab.add_from_sheet
    python -m anki_vocab.add_from_sheet --verbose   # подробные логи каждого запроса
"""
import argparse
import csv
import io
import logging
import sys
import time

import requests

from . import ankiconnect as ac
from . import config, lookups
from .logsetup import setup_logging

logger = logging.getLogger(__name__)


def fetch_sheet_words():
    logger.info("Загружаю список слов из Google Sheet...")
    resp = requests.get(config.SHEET_CSV_URL, timeout=20)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    words = []
    for row in reader:
        value = (row.get(config.SHEET_WORD_COLUMN) or "").strip()
        if value:
            words.append(value)
    return words


def build_fields(word: str):
    logger.info("  -> определяю артикль и перевод...")
    word_with_article = lookups.ensure_article(word)
    bare = lookups.bare_word(word_with_article)
    translation = lookups.translate(word_with_article)
    logger.info("     слово: %r, перевод: %r", word_with_article, translation)

    logger.info("  -> ищу пример предложения (Tatoeba)...")
    sentence = lookups.find_example_sentence(word_with_article)
    sentence_translation = lookups.translate(sentence) if sentence else ""
    if sentence:
        logger.info("     предложение: %r", sentence)
    else:
        logger.info("     предложение не найдено")

    needs_review = []
    if word_with_article == word and word[:1].isupper() and " " not in word:
        needs_review.append("genus")
    if not sentence:
        needs_review.append("sentence")

    fields = {
        config.FIELD_WORD: word_with_article,
        config.FIELD_TRANSLATION: translation,
        config.FIELD_SENTENCE: sentence or "",
        config.FIELD_SENTENCE_TRANSLATION: sentence_translation,
        config.FIELD_IMAGE: "",
        config.FIELD_WORD_AUDIO: "",
        config.FIELD_SENTENCE_AUDIO: "",
    }
    return fields, needs_review, bare


def attach_media(fields: dict, bare_word: str, sentence: str, needs_review: list):
    safe = "".join(c for c in bare_word if c.isalnum()) or "word"

    logger.info("  -> генерирую озвучку слова (edge-tts)...")
    audio = lookups.synthesize_tts(bare_word)
    if audio:
        filename = f"{safe}_word.mp3"
        ac.store_media_file(filename, lookups.b64(audio))
        fields[config.FIELD_WORD_AUDIO] = f"[sound:{filename}]"
        logger.info("     готово: %s", filename)
    else:
        logger.info("     не удалось озвучить слово")
        needs_review.append("word_audio")

    if sentence:
        logger.info("  -> генерирую озвучку предложения...")
        s_audio = lookups.synthesize_tts(sentence)
        if s_audio:
            filename = f"{safe}_sentence.mp3"
            ac.store_media_file(filename, lookups.b64(s_audio))
            fields[config.FIELD_SENTENCE_AUDIO] = f"[sound:{filename}]"
            logger.info("     готово: %s", filename)
        else:
            logger.info("     не удалось озвучить предложение")

    logger.info("  -> ищу картинку (Openverse)...")
    image = lookups.find_image_bytes(bare_word)
    if image:
        filename = f"{safe}_image.jpg"
        ac.store_media_file(filename, lookups.b64(image))
        fields[config.FIELD_IMAGE] = f'<img src="{filename}">'
        logger.info("     готово: %s", filename)
    else:
        logger.info("     картинка не найдена")
        needs_review.append("image")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="подробные логи каждого сетевого запроса")
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    logger.info("Проверяю связь с Anki через AnkiConnect...")
    try:
        version = ac.ping()
        logger.info("AnkiConnect отвечает (версия API: %s).", version)
    except ac.AnkiConnectError as e:
        logger.error("Не удалось связаться с AnkiConnect: %s", e)
        return

    words = fetch_sheet_words()
    logger.info("В таблице найдено %d слов.", len(words))

    added, skipped, failed = 0, 0, 0
    total = len(words)
    for i, word in enumerate(words, start=1):
        logger.info("[%d/%d] Слово: %r", i, total, word)
        bare_for_check = lookups.bare_word(word)

        logger.info("  -> проверяю, нет ли уже такой карточки в Anki...")
        try:
            if ac.word_exists(bare_for_check):
                logger.info("     уже есть в Anki, пропускаю.")
                skipped += 1
                continue
        except ac.AnkiConnectError as e:
            logger.error("%s", e)
            return  # Anki недоступна — нет смысла продолжать

        try:
            fields, needs_review, bare = build_fields(word)
            logger.info("  -> build_fields завершён, запускаю attach_media...")
            attach_media(fields, bare, fields[config.FIELD_SENTENCE], needs_review)
            logger.info("  -> attach_media завершён")

            tags = [config.TAG_AUTO_SHEET]
            if needs_review:
                tags.append(config.TAG_NEEDS_REVIEW)

            logger.info("  -> сохраняю карточку в Anki...")
            note_id = ac.add_note(fields, tags)
            logger.info("  -> add_note вернул note_id=%s", note_id)
            added += 1
            note = " (на проверку: " + ", ".join(needs_review) + ")" if needs_review else ""
            logger.info("  + Добавлено: %s%s", fields[config.FIELD_WORD], note)
        except ac.AnkiConnectError as e:
            logger.error("Ошибка Anki для %r: %s", word, e)
            failed += 1
        except BaseException as e:
            logger.exception("Не удалось обработать %r (%s): %s", word, type(e).__name__, e)
            failed += 1

        time.sleep(config.REQUEST_DELAY)  # вежливая пауза для бесплатных API

    logger.info("Готово: добавлено %d, пропущено (уже есть) %d, ошибок %d.", added, skipped, failed)


if __name__ == "__main__":
    main()
