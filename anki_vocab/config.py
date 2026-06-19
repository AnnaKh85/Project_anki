# -*- coding: utf-8 -*-
"""
Общие настройки конвейера автоматического заполнения Anki.
Поменяйте значения здесь — остальной код их не трогает.
"""

# --- Anki / AnkiConnect ---
ANKICONNECT_URL = "http://127.0.0.1:8765"
DECK_NAME = "Deutsch::Vokabular"
MODEL_NAME = "DE Vocab"

# Имена полей в модели DE Vocab — должны совпадать 1:1 с тем, что в Anki
FIELD_WORD = "Word"
FIELD_TRANSLATION = "Translation"
FIELD_SENTENCE = "Sentence"
FIELD_SENTENCE_TRANSLATION = "SentenceTranslation"
FIELD_IMAGE = "Image"
FIELD_WORD_AUDIO = "WordAudio"
FIELD_SENTENCE_AUDIO = "SentenceAudio"

# --- Языки ---
SOURCE_LANG = "de"
TARGET_LANG = "ru"

# --- Голос для озвучки (edge-tts, бесплатно, без ключа и регистрации) ---
# Посмотреть все доступные немецкие голоса:
#   edge-tts --list-voices | grep de-DE
TTS_VOICE_DE = "de-DE-KatjaNeural"   # женский голос; есть и de-DE-ConradNeural (мужской)

# --- Теги, которые скрипты ставят на карточки ---
TAG_AUTO_SHEET = "auto-sheet"     # карточка добавлена скриптом 1 (из Google Sheet)
TAG_YOMITAN = "yomitan"           # уже стоит на карточках из Yomitan
TAG_ENRICHED = "enriched"         # карточка Yomitan уже дозаполнена скриптом 2
TAG_NEEDS_REVIEW = "needs-review"  # что-то не нашлось автоматически — проверить руками

# --- Groq AI (бесплатный ключ: https://console.groq.com) ---
# Поместите ключ в переменную окружения GROQ_API_KEY или вставьте прямо сюда.
import os as _os
GROQ_API_KEY: str = _os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "qwen/qwen3-32b"  # протестированная модель

# --- Вежливая задержка между обращениями к бесплатным публичным API (сек) ---
REQUEST_DELAY = 0.8

# --- Google Sheet (источник для скрипта 1) ---
# Ссылка на CSV-экспорт публичного листа. Как получить — см. README,
# раздел "Настройка Google Sheet".
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vT_GQ6wVpMp2pob0g2NQs8Wf4DXHvKdGwtye4DJM0Ok47QQb3lnCUPtr-SzrKseY7A6aMtQxeF3LL4R/pub?gid=0&single=true&output=csv"
SHEET_WORD_COLUMN = "word"  # название колонки в таблице, где лежат немецкие слова
