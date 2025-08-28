#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Iniciador simple para ambos bots
Usa threads en lugar de procesos para mejor compatibilidad con Render
"""

import threading
import logging
import signal
import sys
import time

# Configurar logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variable global para controlar el cierre
shutdown = False

def signal_handler(signum, frame):
    """Maneja señales de terminación."""
    global shutdown
    logger.info("Señal de terminación recibida, cerrando bots...")
    shutdown = True
    sys.exit(0)

def run_main_bot():
    """Ejecuta el bot principal en un thread."""
    try:
        logger.info("🚀 Iniciando bot principal...")
        import main
        main.main()
    except Exception as e:
        logger.error(f"Error en bot principal: {e}")
        raise

def run_justifications_bot():
    """Ejecuta el bot de justificaciones en un thread."""
    try:
        logger.info("📚 Iniciando bot de justificaciones...")
        import justifications_bot
        app = justifications_bot.build_just_app()
        app.run_polling(allowed_updates=["message", "edited_message"])
    except Exception as e:
        logger.error(f"Error en bot de justificaciones: {e}")
        raise

def main():
    """Función principal."""
    
    # Configurar manejo de señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Verificar configuración
    try:
        from config import BOT_TOKEN, JUST_BOT_TOKEN
        
        if not BOT_TOKEN:
            logger.error("❌ BOT_TOKEN no configurado")
            sys.exit(1)
        
        if not JUST_BOT_TOKEN:
            logger.error("❌ JUST_BOT_TOKEN no configurado")
            sys.exit(1)
        
        logger.info("✅ Tokens verificados")
        
    except ImportError as e:
        logger.error(f"❌ Error importando configuración: {e}")
        sys.exit(1)
    
    # Mostrar banner
    logger.info("=" * 60)
    logger.info("🤖 SISTEMA DE BOTS DE CASOS CLÍNICOS")
    logger.info("=" * 60)
    
    # Crear threads para cada bot
    thread_main = threading.Thread(target=run_main_bot, name="BotPrincipal", daemon=False)
    thread_just = threading.Thread(target=run_justifications_bot, name="BotJustificaciones", daemon=False)
    
    try:
        # Iniciar bot principal
        thread_main.start()
        logger.info("✅ Thread del bot principal iniciado")
        
        # Esperar un poco antes de iniciar el segundo bot
        time.sleep(3)
        
        # Iniciar bot de justificaciones
        thread_just.start()
        logger.info("✅ Thread del bot de justificaciones iniciado")
        
        logger.info("=" * 60)
        logger.info("✅ AMBOS BOTS ESTÁN FUNCIONANDO")
        logger.info("• Bot principal: Escucha comandos en el canal BORRADOR")
        logger.info("• Bot justificaciones: SOLO responde a deep links")
        logger.info("=" * 60)
        
        # Mantener el programa principal vivo
        while not shutdown:
            time.sleep(1)
            
            # Verificar que los threads sigan vivos
            if not thread_main.is_alive():
                logger.error("❌ Bot principal se detuvo inesperadamente")
                break
            if not thread_just.is_alive():
                logger.error("❌ Bot de justificaciones se detuvo inesperadamente")
                break
        
    except KeyboardInterrupt:
        logger.info("Interrupción por teclado, cerrando...")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
    finally:
        logger.info("Finalizando bots...")
        # Los threads terminarán cuando reciban la señal
        sys.exit(0)

if __name__ == "__main__":
    main()
