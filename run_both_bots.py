# -*- coding: utf-8 -*-
"""
Arranca ambos procesos:
  - main.py        (bot principal)
  - justifications_bot.py  (bot de justificaciones)
Es robusto y no requiere que modifiques main.py.
"""

import subprocess
import sys
import signal
import time

procs = []

def start():
    p1 = subprocess.Popen([sys.executable, "main.py"])
    procs.append(p1)
    p2 = subprocess.Popen([sys.executable, "justifications_bot.py"])
    procs.append(p2)

def stop(sig=None, frame=None):
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    # espera corta y fuerza kill si siguen vivos
    t0 = time.time()
    while time.time() - t0 < 5:
        alive = [p for p in procs if p.poll() is None]
        if not alive:
            break
        time.sleep(0.2)
    for p in procs:
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    start()
    # Espera a que alguno termine (si muere, Render lo reinicia)
    exit_codes = [p.wait() for p in procs]
    stop()
    sys.exit(max(exit_codes) if exit_codes else 0)