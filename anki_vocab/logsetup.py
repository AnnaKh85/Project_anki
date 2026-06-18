# -*- coding: utf-8 -*-
"""Настройка консольного логирования с таймстампами для обоих скриптов."""
import logging
import sys


def setup_logging(verbose: bool = False) -> None:
    """
    verbose=False (по умолчанию) — видно прогресс по каждому слову/шагу.
    verbose=True  — плюс подробности каждого сетевого запроса (AnkiConnect,
                    Wiktionary, Tatoeba, Openverse, перевод, TTS).
    """
    try:
        # На некоторых консолях Windows вывод по умолчанию буферизуется
        # построчно только в интерактивном режиме — форсируем явно.
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass  # не критично, если недоступно в этой среде

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
