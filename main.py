import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from database import Database

logger = logging.getLogger(__name__)

class JustificationHandler:
    def __init__(self, app: Application, db: Database):
        self.app = app
        self.db = db
    
    async def add_justification_button(self, message_id: int, channel_id: str) -> InlineKeyboardMarkup:
        """Add justification button to a message"""
        keyboard = [[
            InlineKeyboardButton(
                "📝 Justificar",
                callback_data=f"justification_add_{message_id}_{channel_id}"
            )
        ]]
        return InlineKeyboardMarkup(keyboard)
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle justification callbacks"""
        query = update.callback_query
        data = query.data
        
        if data.startswith("justification_add_"):
            parts = data.split("_")
            message_id = int(parts[2])
            channel_id = parts[3] if len(parts) > 3 else None
            
            # Store context and ask for justification
            context.user_data['pending_justification'] = {
                'message_id': message_id,
                'channel_id': channel_id
            }
            
            await query.answer()
            await query.message.reply_text(
                "📝 *Escribe tu justificación:*\n\n"
                "Explica por qué elegiste esta respuesta.",
                parse_mode='Markdown'
            )
            
        elif data.startswith("justification_view_"):
            # View justifications for a message
            parts = data.split("_")
            message_id = int(parts[2])
            channel_id = parts[3] if len(parts) > 3 else None
            
            justifications = await self.db.get_justifications(message_id, channel_id)
            
            if justifications:
                text = "*📝 Justificaciones:*\n\n"
                for j in justifications:
                    text += f"• {j['justification_text']}\n"
                    text += f"  _por usuario {j['user_id']}_ \n\n"
            else:
                text = "_No hay justificaciones para este mensaje._"
            
            await query.answer()
            await query.message.reply_text(text, parse_mode='Markdown')
    
    async def save_justification(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save justification text"""
        if 'pending_justification' not in context.user_data:
            return
        
        justification_data = context.user_data['pending_justification']
        justification_text = update.message.text
        
        await self.db.store_justification(
            message_id=justification_data['message_id'],
            channel_id=justification_data['channel_id'],
            user_id=update.effective_user.id,
            justification_text=justification_text
        )
        
        # Clear pending justification
        context.user_data.pop('pending_justification', None)
        
        await update.message.reply_text(
            "✅ Justificación guardada correctamente.",
            reply_markup=None
        )
        
        logger.info(f"Justification saved for message {justification_data['message_id']}")
    
    async def attach_to_poll(self, poll_message, channel_id: str):
        """Attach justification functionality to a poll"""
        # This would be called after creating a poll
        # to add the justification button
        try:
            keyboard = await self.add_justification_button(
                poll_message.message_id,
                channel_id
            )
            
            # Note: Polls can't have inline keyboards directly
            # So we might need to send a follow-up message
            follow_up = await self.app.bot.send_message(
                chat_id=channel_id,
                text="_Responde al poll y luego justifica tu respuesta:_",
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            
            return follow_up
            
        except Exception as e:
            logger.error(f"Failed to attach justification to poll: {e}")
            return None
