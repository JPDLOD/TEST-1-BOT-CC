# ======= CAMBIOS EN main.py =======

# Al inicio del archivo, agregar el import:
from justifications_handler import add_justification_handlers, add_justification_button_to_poll

# En la función main(), después de crear la aplicación y ANTES de run_polling():
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Handlers existentes
    app.add_handler(PollHandler(handle_poll_update))
    app.add_handler(PollAnswerHandler(handle_poll_answer_update))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ¡NUEVO! Agregar handlers de justificaciones
    add_justification_handlers(app)

    app.add_error_handler(on_error)
    
    # ... resto del código


# ======= CAMBIOS EN publisher.py =======

# En la función _publicar_rows, después de enviar la encuesta exitosamente:

async def _publicar_rows(context: ContextTypes.DEFAULT_TYPE, *, rows: List[Tuple[int, str, str]],
                         targets: List[int], mark_as_sent: bool) -> Tuple[int, int, Dict[int, List[int]]]:
    
    # ... código existente ...
    
    for mid, _t, raw in rows:
        # ... código existente ...
        
        for dest in targets:
            if "poll" in data:
                try:
                    # ... código existente de envío de poll ...
                    ok, msg = await _send_with_backoff(coro_factory, base_pause=PAUSE)
                    
                    if ok and is_quiz and msg:
                        # ¡NUEVO! Agregar botón de justificación automáticamente
                        # El justification_message_id podría ser mid (mismo ID) o calculado
                        justification_id = mid  # O usar otra lógica para mapear IDs
                        
                        # Agregar botón después de un breve delay
                        async def add_button_delayed():
                            await asyncio.sleep(1)  # Esperar que se procese la encuesta
                            from justifications_handler import add_justification_button_to_poll
                            await add_justification_button_to_poll(
                                context, 
                                dest, 
                                msg.message_id, 
                                justification_id
                            )
                        
                        # Ejecutar en background
                        asyncio.create_task(add_button_delayed())
                        
                except Exception as e:
                    logger.error(f"Error procesando poll {mid}: {e}")
                    ok, msg = False, None


# ======= COMANDOS ADICIONALES EN main.py =======

# Agregar comando para probar el sistema:
async def _cmd_test_justification(update: Update, context: ContextTypes.DEFAULT_TYPE, txt: str):
    """Comando para probar justificaciones. Uso: /test_just <message_id>"""
    parts = txt.split()
    if len(parts) < 2:
        await context.bot.send_message(SOURCE_CHAT_ID, "Uso: /test_just <message_id>")
        return
    
    try:
        message_id = int(parts[1])
        user_id = 123456789  # Tu user ID para pruebas
        
        from justifications_handler import send_protected_justification
        success = await send_protected_justification(context, user_id, message_id)
        
        if success:
            await context.bot.send_message(SOURCE_CHAT_ID, f"Justificación {message_id} enviada como prueba")
        else:
            await context.bot.send_message(SOURCE_CHAT_ID, f"Error enviando justificación {message_id}")
    
    except ValueError:
        await context.bot.send_message(SOURCE_CHAT_ID, "ID inválido")

# En handle_channel, agregar el nuevo comando:
if low.startswith("/test_just"):
    await _cmd_test_justification(update, context, txt)
    await _delete_user_command_if_possible(update, context);  return


# ======= CONFIGURACIÓN OPCIONAL =======

# En config.py, puedes agregar:
JUSTIFICATIONS_CHAT_ID = -1003058530208
AUTO_DELETE_MINUTES = 10  # 0 para no borrar automáticamente

# En justifications_handler.py, cambiar las importaciones:
from config import TZ, JUSTIFICATIONS_CHAT_ID, AUTO_DELETE_MINUTES
