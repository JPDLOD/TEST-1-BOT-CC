#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ejecuta ambos bots"""

import os
import sys
import threading
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_bot(module_name):
    try:
        logger.info(f"Starting {module_name}...")
        time.sleep(2 if module_name == "justifications_bot" else 1)
        __import__(module_name).main()
    except Exception as e:
        logger.error(f"Error in {module_name}: {e}")
        sys.exit(1)

def main():
    if not os.environ.get("BOT_TOKEN"):
        logger.error("Missing BOT_TOKEN")
        sys.exit(1)
    
    if not os.environ.get("JUST_BOT_TOKEN"):
        logger.error("Missing JUST_BOT_TOKEN")
        sys.exit(1)
    
    t1 = threading.Thread(target=run_bot, args=("main",), daemon=True)
    t2 = threading.Thread(target=run_bot, args=("justifications_bot",), daemon=True)
    
    t1.start()
    t2.start()
    
    logger.info("Both bots started")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")
        sys.exit(0)

if __name__ == "__main__":
    main()
