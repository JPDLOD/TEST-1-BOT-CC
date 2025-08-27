#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Ejecuta ambos bots en el mismo proceso/servicio
Solución para ejecutar todo en un solo servicio de Render
"""

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
import threading

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def run_main_bot():
    """Ejecuta el bot principal en un thread separado."""
    try:
        logger.info("🚀 Iniciando BOT PRINCIPAL...")
        import main
        main.main()
    except Exception as e:
        logger.error(f"❌ Error en bot principal: {e}")
        sys.exit(1)

def run_justifications_bot():
    """Ejecuta el bot de justificaciones en un thread separado."""
    try:
        logger.info("📚 Iniciando BOT DE JUSTIFICACIONES...")
        import justifications_bot
        justifications_bot.main()
    except Exception as e:
        logger.error(f"❌ Error en bot de justificaciones: {e}")
        sys.exit(1)

def main():
    """Función principal que ejecuta ambos bots."""
    logger.info("="*60)
    logger.info("🎯 INICIANDO SISTEMA DUAL DE BOTS")
    logger.info("="*60)
    
    # Crear threads para cada bot
    thread_main = threading.Thread(target=run_main_bot, daemon=False)
    thread_just = threading.Thread(target=run_justifications_bot, daemon=False)
    
    # Iniciar ambos threads
    thread_main.start()
    thread_just.start()
    
    logger.info("✅ Ambos bots iniciados correctamente")
    logger.info("Bot Principal: Maneja publicaciones")
    logger.info("Bot Justificaciones: Maneja justificaciones protegidas")
    
    # Esperar a que ambos threads terminen (nunca deberían terminar)
    try:
        thread_main.join()
        thread_just.join()
    except KeyboardInterrupt:
        logger.info("⏹️ Deteniendo bots...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Error inesperado: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()