# -*- coding: utf-8 -*-
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import TARGET_CHAT_ID, BACKUP_CHAT_ID, PREVIEW_CHAT_ID
from publisher import is_active_backup

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Listar", callback_data="m:list"),
             InlineKeyboardButton("📦 Enviar", callback_data="m:send")],
            [InlineKeyboardButton("🧪 Preview", callback_data="m:preview"),
             InlineKeyboardButton("⏰ Programar", callback_data="m:sched")],
            [InlineKeyboardButton("⚙️ Ajustes", callback_data="m:settings")]
        ]
    )

def text_main() -> str:
    """Texto completo y detallado de ayuda - NO RESUMIR NI ABREVIAR."""
    return (
        "🛠️ **Comandos Principales:**\n"
        
        "**GESTIÓN DE BORRADORES:**\n"
        "• `/listar` — muestra **borradores pendientes** (excluye programados) con su posición e `id` para identificar cada mensaje. También muestra programaciones pendientes con fecha, hora y cantidad de mensajes.\n"
        "• `/cancelar <id>` — (o responde al mensaje con `/cancelar`) saca **de la cola** un borrador específico sin borrarlo del canal BORRADOR. Solo lo marca como 'no enviar'.\n"
        "• `/deshacer [id]` — revierte el último `/cancelar` realizado (o el id específico indicado). Restaura el borrador a la cola para ser enviado. No funciona con `/eliminar`.\n"
        "• `/eliminar <id>` — (o responde al mensaje) **borra definitivamente** el mensaje del canal BORRADOR y lo quita de la cola. Aliases: `/del`, `/delete`, `/remove`, `/borrar`.\n"
        "• `/nuke <parámetro>` — borrado masivo con múltiples opciones:\n"
        "  - `/nuke all` o `/nuke todos` — elimina **todos** los borradores pendientes\n"
        "  - `/nuke 1,3,5` — elimina las posiciones específicas del listado\n"
        "  - `/nuke 2-7` — elimina un **rango** de posiciones consecutivas\n"
        "  - `/nuke N` — elimina los **últimos N** borradores pendientes\n"
        
        "**ENVÍO Y PUBLICACIÓN:**\n"
        "• `/enviar` — publica **inmediatamente** todos los borradores pendientes (no programados) a los targets activos: canal PRINCIPAL siempre, y canal BACKUP si está activado. Reporta estadísticas detalladas.\n"
        "• `/preview` — envía toda la cola de borradores al canal PREVIEW para **revisar antes de publicar** sin marcarlos como enviados. Útil para verificación.\n"
        
        "**PROGRAMACIÓN TEMPORAL:**\n"
        "• `/programar YYYY-MM-DD HH:MM` — programa el envío de **los borradores actuales** para fecha y hora específica (formato 24 horas: 00:00–23:59, sin AM/PM ni sufijos). Los mensajes programados quedan bloqueados y no se incluyen en `/enviar` ni `/preview` hasta ejecutarse.\n"
        "• `/programados` — muestra **todas las programaciones pendientes** con ID de programación (#pid), fecha/hora exacta con zona horaria, tiempo restante estimado y cantidad de mensajes a enviar.\n"
        "• `/desprogramar <id|all>` — cancela una programación específica por su #pid o **todas** las programaciones pendientes con 'all'. Libera los mensajes bloqueados.\n"
        
        "**CONFIGURACIÓN DE DESTINOS:**\n"
        "• `/canales` — muestra los IDs de todos los canales configurados y el **estado actual** de cada target: PRINCIPAL (siempre ON), BACKUP (ON/OFF configurable), PREVIEW.\n"
        "• `/backup on|off` — activa o desactiva **únicamente** el canal BACKUP. El canal PRINCIPAL siempre permanece activo. Muestra el panel de configuración actualizado.\n"
        
        "**HERRAMIENTAS ADICIONALES:**\n"
        "• `/id [id]` — si respondes a un mensaje muestra su ID; con parámetro numérico genera el **enlace directo** t.me/c/... para acceso rápido al mensaje.\n"
        "• **Atajo `@@@ TÍTULO | URL`** — si escribes una línea con este formato en el canal BORRADOR, esa línea se borra automáticamente y se agrega un **botón inline** con ese TÍTULO que enlaza a la URL al último borrador pendiente. El resto del mensaje permanece intacto.\n"
        
        "**INFORMACIÓN Y AYUDA:**\n"
        "• `/comandos` — muestra este panel completo de ayuda con todos los botones de acceso rápido.\n"
        
        "**NOTAS IMPORTANTES:**\n"
        "- Los mensajes **programados** quedan bloqueados y no aparecen en `/listar` ni se incluyen en `/enviar` o `/preview` hasta que se ejecute la programación.\n"
        "- Las **encuestas tipo quiz** mantienen automáticamente su respuesta correcta original (no se fuerza a opción A).\n"
        "- Los **botones inline** creados con `@@@` no tienen vista previa nativa; si necesitas preview del enlace, agrégalo como línea separada además del botón.\n"
        "- El sistema mantiene **estadísticas acumuladas** de cancelados/eliminados que se reportan en cada envío y se limpian después.\n"
        
        "\nPulsa un botón de acceso rápido o usa `/comandos` para volver a ver este panel."
    )

def kb_settings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔀 Backup ON/OFF", callback_data="m:toggle_backup")],
            [InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
        ]
    )

def text_settings() -> str:
    onoff = "ON" if is_active_backup() else "OFF"
    return (
        f"📡 **Configuración de Canales Target**\n\n"
        f"• **Principal**: `{TARGET_CHAT_ID}` — **ON** (siempre activo, no se puede desactivar)\n"
        f"• **Backup**: `{BACKUP_CHAT_ID}` — **{onoff}** (configurable con el botón)\n"
        f"• **Preview**: `{PREVIEW_CHAT_ID}` — (solo para `/preview`, no se incluye en envíos normales)\n\n"
        f"**Estado actual:** Los envíos van a **Principal** {'+ **Backup**' if onoff == 'ON' else '(solo)'}\n\n"
        "Usa el botón **🔀 Backup ON/OFF** para alternar el estado del canal backup.\n"
        "⬅️ **Volver** regresa al menú principal con todos los comandos."
    )

def kb_schedule() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏳ +5 min", callback_data="s:+5"),
             InlineKeyboardButton("⏳ +15 min", callback_data="s:+15")],
            [InlineKeyboardButton("🕗 Hoy 20:00", callback_data="s:today20"),
             InlineKeyboardButton("🌅 Mañana 07:00", callback_data="s:tom07")],
            [InlineKeyboardButton("🗒 Ver programados", callback_data="s:list"),
             InlineKeyboardButton("❌ Cancelar todos", callback_data="s:clear")],
            [InlineKeyboardButton("✍️ Custom", callback_data="s:custom"),
             InlineKeyboardButton("⬅️ Volver", callback_data="m:back")]
        ]
    )

def text_schedule() -> str:
    return (
        "⏰ **Programar Envío Temporal**\n\n"
        "Programa el envío de **los borradores actuales** para una fecha y hora específica.\n\n"
        "**Opciones rápidas:** Usa los botones de abajo para programaciones comunes.\n\n"
        "**Programación manual:** `/programar YYYY-MM-DD HH:MM`\n"
        "• Formato 24 horas: 00:00 a 23:59\n"
        "• Sin sufijos AM/PM ni '(24h)'\n"
        "• Ejemplo: `/programar 2025-08-27 14:30`\n\n"
        "⚠️ **Importante:** Si no hay borradores pendientes, no se programa nada.\n"
        "Los mensajes programados quedan **bloqueados** hasta su ejecución."
    )
