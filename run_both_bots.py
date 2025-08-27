#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ejecuta ambos bots con mejor manejo de errores"""

import os
import sys
import subprocess
import time
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def run_main_bot():
    """Ejecuta el bot principal en un proceso separado."""
    logger.info("🚀 Starting main bot...")
    try:
        subprocess.run([sys.executable, "main.py"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Main bot failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error in main bot: {e}")
        sys.exit(1)

def run_justifications_bot():
    """Ejecuta el bot de justificaciones en un proceso separado."""
    logger.info("🚀 Starting justifications bot...")
    try:
        subprocess.run([sys.executable, "justifications_bot.py"], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Justifications bot failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Error in justifications bot: {e}")
        sys.exit(1)

def check_environment():
    """Verifica que todas las variables de entorno necesarias estén configuradas."""
    required = {
        "BOT_TOKEN": "Token del bot principal",
        "JUST_BOT_TOKEN": "Token del bot de justificaciones",
        "JUSTIFICATIONS_CHAT_ID": "ID del canal de justificaciones"
    }
    
    missing = []
    for var, description in required.items():
        if not os.environ.get(var):
            missing.append(f"  • {var}: {description}")
    
    if missing:
        logger.error("❌ Faltan variables de entorno requeridas:")
        for m in missing:
            logger.error(m)
        return False
    
    logger.info("✅ Variables de entorno verificadas")
    logger.info(f"📁 Canal de justificaciones: {os.environ.get('JUSTIFICATIONS_CHAT_ID')}")
    return True

def main():
    """Función principal que ejecuta ambos bots."""
    logger.info("=" * 50)
    logger.info("🤖 Sistema de Bots de Casos Clínicos")
    logger.info("=" * 50)
    
    # Verificar entorno
    if not check_environment():
        sys.exit(1)
    
    # Importar threading aquí
    import threading
    
    # Crear threads para cada bot
    t1 = threading.Thread(target=run_main_bot, name="MainBot", daemon=False)
    t2 = threading.Thread(target=run_justifications_bot, name="JustBot", daemon=False)
    
    # Iniciar bots
    t1.start()
    logger.info("✅ Bot principal iniciado")
    
    time.sleep(2)  # Pequeño delay entre inicios
    
    t2.start()
    logger.info("✅ Bot de justificaciones iniciado")
    
    logger.info("=" * 50)
    logger.info("🟢 Sistema completo en funcionamiento")
    logger.info("=" * 50)
    
    # Esperar a que ambos terminen
    t1.join()
    t2.join()

if __name__ == "__main__":
    main()
