#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ejecuta ambos bots con mejor manejo de errores"""

import os
import sys
import threading
import time
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def run_bot(module_name):
    """Ejecuta un bot en su propio thread."""
    try:
        logger.info(f"🚀 Starting {module_name}...")
        # Pequeño delay para evitar conflictos de inicio
        time.sleep(2 if module_name == "justifications_bot" else 1)
        
        # Importar y ejecutar el módulo
        module = __import__(module_name)
        module.main()
        
    except Exception as e:
        logger.error(f"❌ Error in {module_name}: {e}")
        import traceback
        traceback.print_exc()
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
    
    # Verificar que JUSTIFICATIONS_CHAT_ID sea consistente
    just_chat_id = os.environ.get("JUSTIFICATIONS_CHAT_ID", "-1003058530208")
    
    # Si existe JUST_CHAT_ID pero es diferente, usar el mismo valor
    if os.environ.get("JUST_CHAT_ID") and os.environ.get("JUST_CHAT_ID") != just_chat_id:
        logger.warning(f"⚠️ JUST_CHAT_ID difiere de JUSTIFICATIONS_CHAT_ID, usando {just_chat_id}")
        os.environ["JUST_CHAT_ID"] = just_chat_id
    
    logger.info("✅ Variables de entorno verificadas")
    logger.info(f"📁 Canal de justificaciones: {just_chat_id}")
    return True

def main():
    """Función principal que ejecuta ambos bots."""
    logger.info("=" * 50)
    logger.info("🤖 Sistema de Bots de Casos Clínicos")
    logger.info("=" * 50)
    
    # Verificar entorno
    if not check_environment():
        sys.exit(1)
    
    # Crear threads para cada bot
    t1 = threading.Thread(
        target=run_bot, 
        args=("main",), 
        name="MainBot",
        daemon=True
    )
    
    t2 = threading.Thread(
        target=run_bot, 
        args=("justifications_bot",), 
        name="JustBot",
        daemon=True
    )
    
    # Iniciar bots
    t1.start()
    logger.info("✅ Bot principal iniciado")
    
    t2.start()
    logger.info("✅ Bot de justificaciones iniciado")
    
    logger.info("=" * 50)
    logger.info("🟢 Sistema completo en funcionamiento")
    logger.info("Presiona Ctrl+C para detener")
    logger.info("=" * 50)
    
    try:
        # Mantener el programa corriendo
        while True:
            time.sleep(1)
            
            # Verificar que los threads sigan vivos
            if not t1.is_alive():
                logger.error("❌ Bot principal se detuvo!")
                sys.exit(1)
            if not t2.is_alive():
                logger.error("❌ Bot de justificaciones se detuvo!")
                sys.exit(1)
                
    except KeyboardInterrupt:
        logger.info("\n🛑 Deteniendo bots...")
        sys.exit(0)

if __name__ == "__main__":
    main()
