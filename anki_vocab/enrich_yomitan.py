# -*- coding: utf-8 -*-
"""
Скрипт 2: дозаполняет карточки, добавленные через Yomitan (тег "yomitan").

Заполняет только пустые поля:
- Sentence          -> генерируется через Groq AI (A1-B1)
- SentenceTranslation -> перевод предложения из того же Groq-запроса
- WordAudio         -> gTTS (Google Translate TTS), не трогается если уже заполнено
- SentenceAudio     -> gTTS, заполняется для нового предложения

После обработки ставится тег "enriched", чтобы повторный запуск
не обрабатывал карточку заново.

Запуск:
    python -m anki_vocab.enrich_yomitan
    python -m anki_vocab.enrich_yomitan --verbose
"""
import argparse
import logging
import time

from . import ankiconnect as ac
from . import config, lookups
from .logsetup import setup_logging

logger = logging.getLogger(__name__)


def enrich_note(nid: int, fields: dict) -> list[str]:
    """
    Дозаполняет одну карточку. Возвращает список полей, которые не удалось заполнить.
    """
    word = fields.get(config.FIELD_WORD, "").strip()
    bare = lookups.bare_word(word)
    safe = "".join(c for c in bare if c.isalnum()) or "word"

    updated = {}
    needs_review = []

    # --- Word: артикль + множественное число для существительных ---
    parts = word.split()
    already_has_article = parts[0].lower() in ("der", "die", "das") if parts else False
    if not already_has_article and word[:1].isupper() and len(parts) == 1:
        logger.info("  -> проверяю артикль и мн.ч. для существительного %r...", word)
        word_enriched = lookups.ensure_article(word)
        if word_enriched != word:
            updated[config.FIELD_WORD] = word_enriched
            word = word_enriched
            bare = lookups.bare_word(word)
            safe = "".join(c for c in bare if c.isalnum()) or "word"
            logger.info("     обновлено: %r", word_enriched)
        else:
            needs_review.append("genus")
            logger.info("     артикль не определён, оставляю как есть")
    else:
        logger.info("  -> Word уже содержит артикль или не существительное, пропускаю")

    # --- Sentence + SentenceTranslation ---
    current_sentence = fields.get(config.FIELD_SENTENCE, "").strip()
    if not current_sentence:
        logger.info("  -> генерирую предложение (Groq)...")
        sentence, sentence_translation = lookups.generate_sentence_and_translation(word)
        if sentence:
            updated[config.FIELD_SENTENCE] = sentence
            updated[config.FIELD_SENTENCE_TRANSLATION] = sentence_translation
            logger.info("     предложение: %r", sentence)
        else:
            needs_review.append("sentence")
            logger.info("     предложение не получено")
    else:
        sentence = current_sentence
        logger.info("  -> Sentence уже заполнен, пропускаю")

    # --- WordAudio ---
    if not fields.get(config.FIELD_WORD_AUDIO, "").strip():
        logger.info("  -> генерирую озвучку слова (gTTS)...")
        audio = lookups.synthesize_tts(bare)
        if audio:
            filename = f"{safe}_word.mp3"
            ac.store_media_file(filename, lookups.b64(audio))
            updated[config.FIELD_WORD_AUDIO] = f"[sound:{filename}]"
            logger.info("     готово: %s", filename)
        else:
            needs_review.append("word_audio")
            logger.info("     не удалось озвучить слово")
    else:
        logger.info("  -> WordAudio уже заполнен, пропускаю")

    # --- SentenceAudio ---
    if not fields.get(config.FIELD_SENTENCE_AUDIO, "").strip() and sentence:
        logger.info("  -> генерирую озвучку предложения (gTTS)...")
        s_audio = lookups.synthesize_tts(sentence)
        if s_audio:
            filename = f"{safe}_sentence.mp3"
            ac.store_media_file(filename, lookups.b64(s_audio))
            updated[config.FIELD_SENTENCE_AUDIO] = f"[sound:{filename}]"
            logger.info("     готово: %s", filename)
        else:
            needs_review.append("sentence_audio")
            logger.info("     не удалось озвучить предложение")
    else:
        logger.info("  -> SentenceAudio уже заполнен или предложение отсутствует, пропускаю")

    if updated:
        ac.update_note_fields(nid, updated)

    tags = config.TAG_ENRICHED
    if needs_review:
        tags += f" {config.TAG_NEEDS_REVIEW}"
    ac.add_tags([nid], tags)

    return needs_review


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="подробные логи")
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    logger.info("Проверяю связь с Anki через AnkiConnect...")
    try:
        version = ac.ping()
        logger.info("AnkiConnect отвечает (версия API: %s).", version)
    except ac.AnkiConnectError as e:
        logger.error("Не удалось связаться с AnkiConnect: %s", e)
        return

    query = f"tag:{config.TAG_YOMITAN} -tag:{config.TAG_ENRICHED}"
    logger.info("Ищу карточки: %s", query)
    note_ids = ac.find_notes(query)
    logger.info("Найдено %d карточек Yomitan для дозаполнения.", len(note_ids))

    if not note_ids:
        return

    notes = ac.notes_info(note_ids)
    done, failed = 0, 0
    total = len(notes)

    for i, note in enumerate(notes, start=1):
        nid = note["noteId"]
        fields = {name: data["value"] for name, data in note["fields"].items()}
        word = fields.get(config.FIELD_WORD, "").strip()
        if not word:
            logger.warning("[%d/%d] Пустое поле Word, пропускаю (noteId=%d)", i, total, nid)
            continue

        logger.info("[%d/%d] Слово: %r (noteId=%d)", i, total, word, nid)
        try:
            needs_review = enrich_note(nid, fields)
            done += 1
            note_msg = " (на проверку: " + ", ".join(needs_review) + ")" if needs_review else ""
            logger.info("  + Готово: %s%s", word, note_msg)
        except ac.AnkiConnectError as e:
            logger.error("  ! Ошибка Anki для %r: %s", word, e)
            failed += 1
        except Exception as e:
            logger.exception("  ! Не удалось обработать %r: %s", word, e)
            failed += 1

        time.sleep(config.REQUEST_DELAY)

    logger.info("Готово: дозаполнено %d, ошибок %d.", done, failed)


if __name__ == "__main__":
    main()
