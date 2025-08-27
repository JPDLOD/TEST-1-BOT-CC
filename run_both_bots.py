#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Ejecuta ambos bots: Bot Principal y CLINICASE_BOT (@clinicase_bot)
"""

import os
import sys
import subprocess
import time
import logging
import signal
import threading
from typing import Optional

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Variables globales para los procesos
main_bot_process: Optional[subprocess.Popen] = None
just_bot_process: Optional[subprocess.Popen] = None
shutdown_event = threading.Event()

def signal_handler(signum, frame):
    """Maneja señales de interrupción para cerrar los bots limpiamente."""
    logger.info("⚠️ Señal de interrupción recibida. Cerrando bots...")
    shutdown_event.set()
    
    if main_bot_process:
        main_bot_process.terminate()
    if just_bot_process:
        just_bot_process.terminate()
    
    sys.exit(0)

def check_environment():
    """Verifica que todas las variables de entorno necesarias estén configuradas."""
    logger.info("🔍 Verificando configuración de entorno...")
    
    required_main = {
        "BOT_TOKEN": "Token del bot principal",
        "SOURCE_CHAT_ID": "ID del canal borrador",
        "TARGET_CHAT_ID": "ID del canal principal",
    }
    
    required_just = {
        "JUST_BOT_TOKEN": "Token del bot CLINICASE (@clinicase_bot)",
        "JUSTIFICATIONS_CHAT_ID": "ID del canal de justificaciones",
    }
    
    all_required = {**required_main, **required_just}
    missing = []
    
    for var, description in all_required.items():
        value = os.environ.get(var)
        if not value:
            missing.append(f"  ❌ {var}: {description}")
        else:
            # Mostrar valores parciales para debug (ocultando tokens)
            if "TOKEN" in var:
                display_value = f"{value[:10]}..." if len(value) > 10 else "***"
            else:
                display_value = value
            logger.info(f"  ✅ {var}: {display_value}")
    
    if missing:
        logger.error("❌ Faltan variables de entorno requeridas:")
        for m in missing:
            logger.error(m)
        return False
    
    # Verificar que los IDs sean válidos
    try:
        just_chat_id = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "0"))
        if just_chat_id == 0:
            logger.error("❌ JUSTIFICATIONS_CHAT_ID es 0 o inválido")
            return False
    except ValueError:
        logger.error("❌ JUSTIFICATIONS_CHAT_ID no es un número válido")
        return False
    
    logger.info("✅ Todas las variables de entorno verificadas correctamente")
    return True

def run_main_bot():
    """Ejecuta el bot principal en un proceso separado."""
    global main_bot_process
    logger.info("🚀 Iniciando bot principal...")
    
    try:
        main_bot_process = subprocess.Popen(
            [sys.executable, "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Leer output en tiempo real
        while not shutdown_event.is_set():
            if main_bot_process.poll() is not None:
                # El proceso terminó
                stdout, stderr = main_bot_process.communicate()
                if stdout:
                    logger.info(f"[MAIN BOT] {stdout}")
                if stderr:
                    logger.error(f"[MAIN BOT ERROR] {stderr}")
                logger.error(f"❌ Bot principal terminó con código {main_bot_process.returncode}")
                return
            
            # Leer línea de output si hay
            if main_bot_process.stdout:
                line = main_bot_process.stdout.readline()
                if line:
                    logger.info(f"[MAIN] {line.strip()}")
            
            time.sleep(0.1)
            
    except Exception as e:
        logger.error(f"❌ Error en bot principal: {e}")

def run_justifications_bot():
    """Ejecuta el bot CLINICASE (@clinicase_bot) en un proceso separado."""
    global just_bot_process
    logger.info("🚀 Iniciando bot CLINICASE (@clinicase_bot)...")
    
    try:
        just_bot_process = subprocess.Popen(
            [sys.executable, "justifications_bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Leer output en tiempo real
        while not shutdown_event.is_set():
            if just_bot_process.poll() is not None:
                # El proceso terminó
                stdout, stderr = just_bot_process.communicate()
                if stdout:
                    logger.info(f"[CLINICASE] {stdout}")
                if stderr:
                    logger.error(f"[CLINICASE ERROR] {stderr}")
                logger.error(f"❌ Bot CLINICASE terminó con código {just_bot_process.returncode}")
                return
            
            # Leer línea de output si hay
            if just_bot_process.stdout:
                line = just_bot_process.stdout.readline()
                if line:
                    logger.info(f"[CLINICASE] {line.strip()}")
            
            time.sleep(0.1)
            
    except Exception as e:
        logger.error(f"❌ Error en bot CLINICASE: {e}")

def test_bot_tokens():
    """Prueba rápida de que los tokens funcionan."""
    import asyncio
    from telegram import Bot
    
    async def test_token(token, name):
        try:
            bot = Bot(token=token)
            me = await bot.get_me()
            logger.info(f"✅ {name} verificado: @{me.username}")
            return True
        except Exception as e:
            logger.error(f"❌ {name} token inválido: {e}")
            return False
    
    async def test_both():
        main_token = os.environ.get("BOT_TOKEN")
        just_token = os.environ.get("JUST_BOT_TOKEN")
        
        results = await asyncio.gather(
            test_token(main_token, "Bot principal"),
            test_token(just_token, "Bot CLINICASE (@clinicase_bot)")
        )
        
        return all(results)
    
    logger.info("🔐 Verificando tokens...")
    return asyncio.run(test_both())

def main():
    """Función principal que ejecuta ambos bots."""
    logger.info("=" * 60)
    logger.info("🤖 Sistema de Bots de Casos Clínicos - v2.0")
    logger.info("=" * 60)
    
    # Registrar manejador de señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Verificar entorno
    if not check_environment():
        logger.error("❌ Configuración de entorno incompleta. Abortando.")
        sys.exit(1)
    
    # Verificar tokens
    if not test_bot_tokens():
        logger.error("❌ Los tokens no son válidos. Abortando.")
        sys.exit(1)
    
    logger.info("=" * 60)
    
    # Crear threads para cada bot
    t1 = threading.Thread(target=run_main_bot, name="MainBot", daemon=False)
    t2 = threading.Thread(target=run_justifications_bot, name="CliniCaseBot", daemon=False)
    
    # Iniciar bot principal
    t1.start()
    logger.info("✅ Thread del bot principal iniciado")
    
    # Esperar un poco antes de iniciar el segundo
    time.sleep(3)
    
    # Iniciar bot CLINICASE
    t2.start()
    logger.info("✅ Thread del bot CLINICASE iniciado")
    
    logger.info("=" * 60)
    logger.info("🟢 Sistema completo en funcionamiento")
    logger.info("📚 Bot principal: Maneja publicación de casos")
    logger.info("🩺 Bot CLINICASE (@clinicase_bot): Maneja envío de justificaciones")
    logger.info("=" * 60)
    
    # Monitorear threads
    while True:
        if not t1.is_alive():
            logger.error("❌ Thread del bot principal murió!")
            break
        if not t2.is_alive():
            logger.error("❌ Thread del bot CLINICASE murió!")
            break
        
        time.sleep(5)
    
    # Si algún thread murió, cerrar todo
    shutdown_event.set()
    if main_bot_process:
        main_bot_process.terminate()
    if just_bot_process:
        just_bot_process.terminate()
    
    sys.exit(1)

if __name__ == "__main__":
    main()    
    sys.exit(0)

def check_environment():
    """Verifica que todas las variables de entorno necesarias estén configuradas."""
    logger.info("🔍 Verificando configuración de entorno...")
    
    required_main = {
        "BOT_TOKEN": "Token del bot principal",
        "SOURCE_CHAT_ID": "ID del canal borrador",
        "TARGET_CHAT_ID": "ID del canal principal",
    }
    
    required_just = {
        "JUST_BOT_TOKEN": "Token del bot de justificaciones",
        "JUSTIFICATIONS_CHAT_ID": "ID del canal de justificaciones",
    }
    
    all_required = {**required_main, **required_just}
    missing = []
    
    for var, description in all_required.items():
        value = os.environ.get(var)
        if not value:
            missing.append(f"  ❌ {var}: {description}")
        else:
            # Mostrar valores parciales para debug (ocultando tokens)
            if "TOKEN" in var:
                display_value = f"{value[:10]}..." if len(value) > 10 else "***"
            else:
                display_value = value
            logger.info(f"  ✅ {var}: {display_value}")
    
    if missing:
        logger.error("❌ Faltan variables de entorno requeridas:")
        for m in missing:
            logger.error(m)
        return False
    
    # Verificar que los IDs sean válidos
    try:
        just_chat_id = int(os.environ.get("JUSTIFICATIONS_CHAT_ID", "0"))
        if just_chat_id == 0:
            logger.error("❌ JUSTIFICATIONS_CHAT_ID es 0 o inválido")
            return False
    except ValueError:
        logger.error("❌ JUSTIFICATIONS_CHAT_ID no es un número válido")
        return False
    
    logger.info("✅ Todas las variables de entorno verificadas correctamente")
    return True

def run_main_bot():
    """Ejecuta el bot principal en un proceso separado."""
    global main_bot_process
    logger.info("🚀 Iniciando bot principal...")
    
    try:
        main_bot_process = subprocess.Popen(
            [sys.executable, "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Leer output en tiempo real
        while not shutdown_event.is_set():
            if main_bot_process.poll() is not None:
                # El proceso terminó
                stdout, stderr = main_bot_process.communicate()
                if stdout:
                    logger.info(f"[MAIN BOT] {stdout}")
                if stderr:
                    logger.error(f"[MAIN BOT ERROR] {stderr}")
                logger.error(f"❌ Bot principal terminó con código {main_bot_process.returncode}")
                return
            
            # Leer línea de output si hay
            if main_bot_process.stdout:
                line = main_bot_process.stdout.readline()
                if line:
                    logger.info(f"[MAIN] {line.strip()}")
            
            time.sleep(0.1)
            
    except Exception as e:
        logger.error(f"❌ Error en bot principal: {e}")

def run_justifications_bot():
    """Ejecuta el bot de justificaciones en un proceso separado."""
    global just_bot_process
    logger.info("🚀 Iniciando bot de justificaciones...")
    
    try:
        just_bot_process = subprocess.Popen(
            [sys.executable, "justifications_bot.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # Leer output en tiempo real
        while not shutdown_event.is_set():
            if just_bot_process.poll() is not None:
                # El proceso terminó
                stdout, stderr = just_bot_process.communicate()
                if stdout:
                    logger.info(f"[JUST BOT] {stdout}")
                if stderr:
                    logger.error(f"[JUST BOT ERROR] {stderr}")
                logger.error(f"❌ Bot justificaciones terminó con código {just_bot_process.returncode}")
                return
            
            # Leer línea de output si hay
            if just_bot_process.stdout:
                line = just_bot_process.stdout.readline()
                if line:
                    logger.info(f"[JUST] {line.strip()}")
            
            time.sleep(0.1)
            
    except Exception as e:
        logger.error(f"❌ Error en bot justificaciones: {e}")

def test_bot_tokens():
    """Prueba rápida de que los tokens funcionan."""
    import asyncio
    from telegram import Bot
    
    async def test_token(token, name):
        try:
            bot = Bot(token=token)
            me = await bot.get_me()
            logger.info(f"✅ {name} verificado: @{me.username}")
            return True
        except Exception as e:
            logger.error(f"❌ {name} token inválido: {e}")
            return False
    
    async def test_both():
        main_token = os.environ.get("BOT_TOKEN")
        just_token = os.environ.get("JUST_BOT_TOKEN")
        
        results = await asyncio.gather(
            test_token(main_token, "Bot principal"),
            test_token(just_token, "Bot justificaciones")
        )
        
        return all(results)
    
    logger.info("🔐 Verificando tokens...")
    return asyncio.run(test_both())

def main():
    """Función principal que ejecuta ambos bots."""
    logger.info("=" * 60)
    logger.info("🤖 Sistema de Bots de Casos Clínicos - v2.0")
    logger.info("=" * 60)
    
    # Registrar manejador de señales
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Verificar entorno
    if not check_environment():
        logger.error("❌ Configuración de entorno incompleta. Abortando.")
        sys.exit(1)
    
    # Verificar tokens
    if not test_bot_tokens():
        logger.error("❌ Los tokens no son válidos. Abortando.")
        sys.exit(1)
    
    logger.info("=" * 60)
    
    # Crear threads para cada bot
    t1 = threading.Thread(target=run_main_bot, name="MainBot", daemon=False)
    t2 = threading.Thread(target=run_justifications_bot, name="JustBot", daemon=False)
    
    # Iniciar bot principal
    t1.start()
    logger.info("✅ Thread del bot principal iniciado")
    
    # Esperar un poco antes de iniciar el segundo
    time.sleep(3)
    
    # Iniciar bot de justificaciones
    t2.start()
    logger.info("✅ Thread del bot de justificaciones iniciado")
    
    logger.info("=" * 60)
    logger.info("🟢 Sistema completo en funcionamiento")
    logger.info("📚 Bot principal: Maneja publicación de casos")
    logger.info("🩺 Bot justificaciones (@JUST_CC_bot): Maneja envío de justificaciones")
    logger.info("=" * 60)
    
    # Monitorear threads
    while True:
        if not t1.is_alive():
            logger.error("❌ Thread del bot principal murió!")
            break
        if not t2.is_alive():
            logger.error("❌ Thread del bot de justificaciones murió!")
            break
        
        time.sleep(5)
    
    # Si algún thread murió, cerrar todo
    shutdown_event.set()
    if main_bot_process:
        main_bot_process.terminate()
    if just_bot_process:
        just_bot_process.terminate()
    
    sys.exit(1)

if __name__ == "__main__":
    main()
