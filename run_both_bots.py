# -*- coding: utf-8 -*-
"""
Lanza ambos bots (principal + justificaciones) en un solo worker.

- Mantiene tu main.py tal cual (el bot principal).
- Arranca main.main() en un thread.
- Arranca el bot de justificaciones en otro thread **solo si** hay token.
- Evita que el proceso muera si alguno de los dos cae (logs claros).

Procfile:
  worker: python run_both_bots.py
"""
import logging
import os
import threading
import time

import main as main_bot

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
log = logging.getLogger("runner")


def _run_main():
    try:
        log.info("Iniciando bot principal…")
        main_bot.main()
    except Exception as e:
        log.exception("Main bot terminó con error: %s", e)


def _run_jst():
    try:
        import justifications_bot as j_bot  # import tardío para no romper si el archivo falta
        log.info("Iniciando bot de justificaciones…")
        j_bot.main()
    except Exception as e:
        log.exception("Justifications bot terminó con error: %s", e)


def main():
    # Bot principal SIEMPRE
    t_main = threading.Thread(target=_run_main, name="main-bot", daemon=True)
    t_main.start()
    log.info("Main bot lanzado en thread.")

    # Bot de justificaciones SOLO si hay token
    jst_token = os.environ.get("JUSTIFICATIONS_BOT_TOKEN", "").strip()
    if jst_token:
        t_jst = threading.Thread(target=_run_jst, name="jst-bot", daemon=True)
        t_jst.start()
        log.info("Justifications bot lanzado en thread.")
    else:
        log.warning("JUSTIFICATIONS_BOT_TOKEN no está definido. Solo correrá el bot principal.")

    # Mantener vivo el proceso mientras alguno siga corriendo
    try:
        while True:
            time.sleep(2.0)
            if not t_main.is_alive():
                log.error("El bot principal terminó. Saliendo del runner.")
                break
            # si hay jst y muere, avisamos pero mantenemos vivo el principal
            # (logs del hilo jst ya mostrarán el motivo)
    except KeyboardInterrupt:
        log.info("Recibido KeyboardInterrupt. Saliendo…")


if __name__ == "__main__":
    main()