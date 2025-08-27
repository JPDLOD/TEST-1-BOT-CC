# -*- coding: utf-8 -*-
"""
Lanza ambos bots (bot principal + bot de justificaciones) en un solo worker de Render.

- Mantiene tu main.py tal cual (sigue teniendo main() que corre el bot principal).
- Arranca main.main() en un thread.
- Arranca el bot de justificaciones en el hilo principal (para heredar señales).

Procfile debe ser:
  worker: python run_both_bots.py
"""
import logging
import threading
import time

import main as main_bot
import justifications_bot as j_bot

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("runner")


def _run_main():
    try:
        main_bot.main()
    except Exception as e:
        log.exception("Main bot terminó con error: %s", e)


def _run_jst():
    try:
        j_bot.main()
    except Exception as e:
        log.exception("Justifications bot terminó con error: %s", e)


def main():
    t1 = threading.Thread(target=_run_main, name="main-bot", daemon=True)
    t1.start()
    log.info("Main bot lanzado en thread.")

    # Ejecutamos el de justificaciones en el hilo principal para heredar señales
    _run_jst()

    # Si el bot de justificaciones terminara, mantenemos vivo el proceso si el otro sigue corriendo
    while t1.is_alive():
        time.sleep(1.0)


if __name__ == "__main__":
    main()