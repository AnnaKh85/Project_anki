# -*- coding: utf-8 -*-
"""
Скрипт 2: дозаполняет карточки, добавленные через Yomitan (тег "yomitan"):

- Word              -> добавляет артикль der/die/das, если это существительное без него
- Sentence          -> перезаполняется новым примером (A1-B1, по длине)
- SentenceTranslation -> заполняется переводом нового предложения
- Image             -> заполняется, если поле было пустым
- WordAudio         -> НЕ трогается, если уже заполнено
- SentenceAudio     -> заполняется озвучкой нового предложения

После обработки на карточку ставится тег "enriched", чтобы повторный запуск
скрипта не обрабатывал её заново (и не тратил лимиты бесплатных API повторно).

Запуск:
    python -m anki_vocab.enrich_yomitan
"""
import sys
import time

from . import ankiconnect as ac
from . import config, lookups


def main():
    query = f"tag:{config.TAG_YOMITAN} -tag:{config.TAG_ENRICHED}"
    note_ids = ac.find_notes(query)
    print(f"Найдено {len(note_ids)} карточек Yomitan для дозаполнения.")

    notes = ac.notes_info(note_ids)
    done, failed = 0, 0

    for note in notes:
        nid = note["noteId"]
        f = {name: data["value"] for name, data in note["fields"].items()}
        word = f.get(config.FIELD_WORD, "").strip()
        if not word:
            continue

        try:
            needs_review = []

            word_with_article = lookups.ensure_article(word)
            if word_with_article == word and word[:1].isupper() and " " not in word:
                needs_review.append("genus")

            sentence = lookups.find_example_sentence(word_with_article)
            sentence_translation = lookups.translate(sentence) if sentence else ""
            if not sentence:
                needs_review.append("sentence")

            updated = {
                config.FIELD_WORD: word_with_article,
                config.FIELD_SENTENCE: sentence or f.get(config.FIELD_SENTENCE, ""),
                config.FIELD_SENTENCE_TRANSLATION: sentence_translation,
            }
            # WordAudio сознательно не включаем в updated — он не меняется.

            bare = lookups.bare_word(word_with_article)
            safe = "".join(c for c in bare if c.isalnum()) or "word"

            if sentence:
                s_audio = lookups.synthesize_tts(sentence)
                if s_audio:
                    filename = f"{safe}_sentence.mp3"
                    ac.store_media_file(filename, lookups.b64(s_audio))
                    updated[config.FIELD_SENTENCE_AUDIO] = f"[sound:{filename}]"

            if not f.get(config.FIELD_IMAGE):
                image = lookups.find_image_bytes(bare)
                if image:
                    filename = f"{safe}_image.jpg"
                    ac.store_media_file(filename, lookups.b64(image))
                    updated[config.FIELD_IMAGE] = f'<img src="{filename}">'
                else:
                    needs_review.append("image")

            ac.update_note_fields(nid, updated)

            tags_to_add = config.TAG_ENRICHED
            if needs_review:
                tags_to_add += f" {config.TAG_NEEDS_REVIEW}"
            ac.add_tags([nid], tags_to_add)

            done += 1
            note_msg = " (на проверку: " + ", ".join(needs_review) + ")" if needs_review else ""
            print(f"  + {updated.get(config.FIELD_WORD, word)}{note_msg}")
        except ac.AnkiConnectError as e:
            print(f"  ! Ошибка Anki для '{word}': {e}", file=sys.stderr)
            failed += 1
        except Exception as e:
            print(f"  ! Не удалось обработать '{word}': {e}", file=sys.stderr)
            failed += 1

        time.sleep(config.REQUEST_DELAY)

    print(f"\nГотово: дозаполнено {done}, ошибок {failed}.")


if __name__ == "__main__":
    main()
