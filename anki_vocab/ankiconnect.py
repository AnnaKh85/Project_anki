# -*- coding: utf-8 -*-
"""
Тонкая обёртка над AnkiConnect (http://127.0.0.1:8765).

Anki должна быть открыта, а аддон AnkiConnect (код 2055492159) — установлен
и включён. Установка: Anki -> Tools -> Add-ons -> Get Add-ons -> ввести код.
"""
import logging
from typing import Optional

import requests

from . import config

logger = logging.getLogger(__name__)


class AnkiConnectError(Exception):
    pass


def invoke(action: str, **params):
    logger.debug("AnkiConnect -> %s %r", action, params)
    payload = {"action": action, "version": 6, "params": params}
    try:
        resp = requests.post(config.ANKICONNECT_URL, json=payload, timeout=20)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError as e:
        raise AnkiConnectError(
            "Не удалось подключиться к Anki через AnkiConnect. "
            "Убедитесь, что Anki открыта и аддон AnkiConnect включён."
        ) from e
    except requests.exceptions.Timeout as e:
        raise AnkiConnectError(
            "AnkiConnect не ответил за 20 секунд. Возможно, окно Anki "
            "занято модальным диалогом (например, идёт синхронизация) — "
            "закрой все диалоги в Anki и попробуй снова."
        ) from e
    data = resp.json()
    if data.get("error") is not None:
        raise AnkiConnectError(data["error"])
    logger.debug("AnkiConnect <- %s OK", action)
    return data["result"]


def ping() -> int:
    """Быстрая проверка соединения с AnkiConnect. Возвращает версию API."""
    return invoke("version")


def find_notes(query: str):
    return invoke("findNotes", query=query)


def notes_info(note_ids):
    if not note_ids:
        return []
    return invoke("notesInfo", notes=note_ids)


def find_word_notes(bare_word: str) -> list:
    """Возвращает список notesInfo для карточек с данным словом (без артикля)."""
    query = f'note:"{config.MODEL_NAME}" {config.FIELD_WORD}:*{bare_word}*'
    note_ids = find_notes(query)
    return notes_info(note_ids)


def word_exists(bare_word: str) -> bool:
    """Проверяет, есть ли уже карточка с таким словом (без учёта артикля)."""
    query = f'note:"{config.MODEL_NAME}" {config.FIELD_WORD}:*{bare_word}*'
    return len(find_notes(query)) > 0


def remove_tags(note_ids, tags: str):
    invoke("removeTags", notes=note_ids, tags=tags)


def add_note(fields: dict, tags: list) -> Optional[int]:
    note = {
        "deckName": config.DECK_NAME,
        "modelName": config.MODEL_NAME,
        "fields": fields,
        "options": {"allowDuplicate": False},
        "tags": tags,
    }
    return invoke("addNote", note=note)


def update_note_fields(note_id: int, fields: dict):
    invoke("updateNoteFields", note={"id": note_id, "fields": fields})


def add_tags(note_ids, tags: str):
    invoke("addTags", notes=note_ids, tags=tags)


def store_media_file(filename: str, data_b64: str):
    """Сохраняет файл в media-папку коллекции Anki и возвращает имя файла."""
    return invoke("storeMediaFile", filename=filename, data=data_b64)
