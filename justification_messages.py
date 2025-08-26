# -*- coding: utf-8 -*-
"""
Banco de mensajes creativos para justificaciones médicas
Organizado por categorías para fácil mantenimiento
"""

import random

# Mensajes profesionales y motivacionales
PROFESSIONAL_MESSAGES = [
    "📚 ¡Justificación lista! Revisa con calma.",
    "✨ Material de estudio enviado.",
    "🎯 ¡Justificación disponible!",
    "📖 Contenido académico listo para revisar.",
    "🔍 Material explicativo enviado exitosamente.",
    "💡 ¡Información detallada lista!",
    "📝 Justificación completa disponible.",
    "🩺 Material clínico enviado. ¡Éxito!",
    "📊 Caso analizado y justificado. ¡A estudiar!",
    "🎓 Material académico listo. ¡Que sea útil!",
    "💪 Un paso más cerca de la residencia. ¡Justificación enviada!",
    "🏆 Futuro residente, aquí está tu justificación.",
    "📈 Tu curva de aprendizaje acaba de subir. Material enviado.",
    "🌟 Brillas más que la lámpara del quirófano. Justificación lista.",
    "🚀 Despegando hacia la residencia. Combustible: Esta justificación.",
    "🏃‍♂️ Corre por esa residencia. Aquí tu impulso.",
]

# Humor médico suave
SOFT_MEDICAL_HUMOR = [
    "💊 Tu dosis de conocimiento ha sido enviada.",
    "🩺 Diagnóstico: Necesitas esta justificación. Tratamiento: Leerla.",
    "📋 Historia clínica del caso: Completa. Tu tarea: Estudiarla.",
    "🔬 Resultados del laboratorio de conocimiento listos.",
    "💉 Inyección de sabiduría administrada con éxito.",
    "🏥 Interconsulta con la justificación: Aprobada.",
    "🚑 Justificación de emergencia despachada.",
    "👨‍⚕️ El Dr. Bot te envió la justificación STAT!",
    "🌡️ Justificación a temperatura ambiente. Consumir antes de 10 min.",
    "🦴 Rayos X del caso revelados. Sin fracturas en la lógica.",
]

# Humor médico con conocimiento
MEDICAL_KNOWLEDGE_HUMOR = [
    "🫀 Tu nodo SA está enviando impulsos de felicidad. Justificación en ritmo sinusal.",
    "🧬 Mutación detectada en el gen del conocimiento: +100 IQ. Justificación enviada.",
    "💊 Farmacocinética: Absorción inmediata, Distribución cerebral, Sin metabolismo, Excreción: nunca.",
    "🦠 Gram positivo para el aprendizaje. Sensible a esta justificación.",
    "🩸 Tu Hb subió 2 puntos solo de ver esta justificación.",
    "🧪 pH del conocimiento: 7.4. Perfectamente balanceado, como debe ser.",
    "🔬 Biopsia de tu ignorancia: Negativa. Tratamiento: Esta justificación PRN.",
    "🫁 Relación V/Q perfecta entre pregunta y justificación.",
    "💉 Vía de administración: Ocular. Biodisponibilidad: 100%. Justificación inyectada.",
    "🦴 Tu apófisis mastoides está vibrando de emoción. Justificación resonando.",
    "🫀 Sistólica: 120, Diastólica: 80, Justificación: Perfecta.",
    "🧬 ADN del caso decodificado. Justificación transcrita.",
    "🔬 Cultivo de conocimiento positivo. Antibiograma: Esta justificación.",
    "🩸 Hemoglobina baja, justificación alta. Balance perfecto.",
    "💊 Paracetamol para el dolor, justificación para la duda.",
]

# Mensajes atrevidos y graciosos
BOLD_FUNNY_MESSAGES = [
    "💀 Si no aciertas después de esto, el problema no es el caso...",
    "🧠 Justificación enviada. Úsala sabiamente (no como el interno del turno pasado).",
    "☕ Justificación + café = Residente feliz",
    "😷 Esta justificación no previene COVID, pero sí la ignorancia.",
    "🔥 Justificación más caliente que la fiebre del paciente de la cama 3.",
    "💸 Esta justificación vale más que tu sueldo de residente.",
    "🍕 Justificación enviada. Ahora sí puedes ir por pizza.",
    "😴 Justificación lista. Léela antes de la guardia o después del café #3.",
    "🎮 Pausaste el PlayStation para esto. Que valga la pena.",
    "📱 Notificación importante: No es match de Tinder, es tu justificación.",
    "🔥 Más hot que la enfermera nueva del piso 3.",
    "💀 Si fallas después de esto, mejor vende Herbalife.",
    "🍺 Esta justificación pega más que guardia post-fiesta.",
    "😏 OnlyFans médico: Solo justificaciones hot para residentes.",
    "🌶️ Picante como el chisme del jefe de cirugía con la instrumentadora.",
    "💸 Gratis. A diferencia de tu vida social después de la residencia.",
    "🎯 Como tu ex: Clara, directa y te va a doler si no le pones atención.",
    "🔞 Contenido explícito: Conocimiento sin censura.",
    "🚬 Más adictiva que el café de la máquina del hospital.",
    "👀 Vista en el chat: 1:50am. Sí, sabemos que estás de guardia.",
]

# Referencias a la vida médica
MEDICAL_LIFE_REFERENCES = [
    "📞 Interconsulta respondida más rápido que las de medicina interna.",
    "⏰ Justificación enviada en menos tiempo que una cirugía de cataratas.",
    "🏃 Más rápido que residente huyendo de guardia de traumato.",
    "💯 Justificación con menos errores que una nota del R1.",
    "⚡ Llegó más rápido que ambulancia sin tráfico (imposible, lo sé).",
    "🎭 Drama médico resuelto. Justificación en escena.",
    "🧊 Justificación más fresca que el aire acondicionado de quirófano.",
    "⏰ 36 horas de guardia y sigues aquí. Respeto. Toma tu justificación.",
    "☕ Equivale a 7 tazas de café del hospital (sí, del malo).",
    "📋 Más clara que las indicaciones del traumatólogo.",
    "🏃‍♂️ Llegó más rápido que R1 escapando de procedimiento.",
    "😴 Para leer entre la 4ta y 5ta alarma del despertador.",
    "🚽 Lectura perfecta para tu escondite favorito del hospital.",
    "📱 Más notifications que el grupo de WhatsApp de la guardia.",
    "🍜 Como la sopa Maruchan: Rápida, efectiva y salva vidas de residentes.",
    "😷 N95 para tu ignorancia. Filtración garantizada.",
    "🏥 Código azul para tu conocimiento. Reanimación exitosa.",
]

# Nerdy/Técnicos
NERDY_TECHNICAL = [
    "🧮 Ecuación de Henderson-Hasselbalch resuelta. HCO3- de tu ignorancia neutralizado.",
    "⚡ Potencial de acción disparado. Despolarización del conocimiento en progreso.",
    "🔬 PCR de tu duda: Amplificada y secuenciada. Primer: Esta justificación.",
    "🧫 Western Blot de tu aprendizaje: Banda única, peso molecular: ∞",
    "🩻 Hounsfield units de tu cerebro: +1000. Justificación hiperdensa detectada.",
    "💊 Inhibidor selectivo de la ignorancia. Vida media: Tu carrera entera.",
    "🧪 Krebs cycle completado. ATP del conocimiento: Máximo.",
    "🔬 Microscopia electrónica de tu duda: Resuelta a nivel molecular.",
    "🧬 CRISPR-Cas9 aplicado a tu ignorancia. Gen editado con éxito.",
    "📊 Curva ROC de esta justificación: AUC = 1.0. Perfecta discriminación.",
]

# Ultra Random
ULTRA_RANDOM = [
    "🦄 Tan rara como un turno tranquilo. Tu justificación llegó.",
    "🍔 Como el almuerzo en guardia: rápido y necesario.",
    "🎰 Jackpot médico: Justificación correcta enviada.",
    "🎪 Bienvenido al show. Primera fila para tu justificación.",
    "🎨 Obra maestra médica pintada. Marco: Tu justificación.",
    "🦖 Dinosaurio vio nacer la medicina. Esta justificación lo jubiló.",
    "🎮 Logro desbloqueado: Justificación legendaria obtenida.",
    "🍕 Si el conocimiento fuera pizza, esta sería con extra queso.",
    "🎸 Rock and roll para tus neuronas. Justificación en sol mayor.",
    "🦸‍♂️ Superman usa bata blanca. Tú usas esta justificación.",
    "🌮 Taco de conocimiento con extra salsa de sabiduría.",
    "🎰 777 - Ganaste el jackpot académico.",
    "🚁 Helicóptero del SAMU trayendo tu justificación urgente.",
    "🍻 Brindis: Por ti, por mí, por esta justificación.",
    "🎭 Shakespeare escribiría sobre esta justificación.",
]

# Humor negro médico (usar con moderación)
DARK_MEDICAL_HUMOR = [
    "⚰️ El paciente no sobrevivió, pero tu conocimiento sí.",
    "💀 Causa de muerte: No leer esta justificación.",
    "🧟 Reanimación tipo Walking Dead: Tu cerebro después de leer esto.",
    "👻 El fantasma del parcial pasado dice: 'Ojalá hubiera tenido esto'.",
    "🩸 Más derramamiento que trauma penetrante. Pero de conocimiento.",
]

# Lista combinada de todos los mensajes
ALL_MESSAGES = (
    PROFESSIONAL_MESSAGES +
    SOFT_MEDICAL_HUMOR +
    MEDICAL_KNOWLEDGE_HUMOR +
    BOLD_FUNNY_MESSAGES +
    MEDICAL_LIFE_REFERENCES +
    NERDY_TECHNICAL +
    ULTRA_RANDOM +
    DARK_MEDICAL_HUMOR
)

def get_random_message() -> str:
    """
    Retorna un mensaje aleatorio del banco completo.
    """
    return random.choice(ALL_MESSAGES)

def get_message_by_category(category: str = "all") -> str:
    """
    Retorna un mensaje aleatorio de una categoría específica.
    
    Categorías disponibles:
    - professional: Mensajes profesionales
    - soft_humor: Humor médico suave
    - knowledge: Humor con conocimiento médico
    - bold: Mensajes atrevidos y graciosos
    - medical_life: Referencias a la vida médica
    - nerdy: Técnicos y nerdy
    - random: Ultra random
    - dark: Humor negro médico
    - all: Todos los mensajes
    """
    categories = {
        "professional": PROFESSIONAL_MESSAGES,
        "soft_humor": SOFT_MEDICAL_HUMOR,
        "knowledge": MEDICAL_KNOWLEDGE_HUMOR,
        "bold": BOLD_FUNNY_MESSAGES,
        "medical_life": MEDICAL_LIFE_REFERENCES,
        "nerdy": NERDY_TECHNICAL,
        "random": ULTRA_RANDOM,
        "dark": DARK_MEDICAL_HUMOR,
        "all": ALL_MESSAGES
    }
    
    selected_category = categories.get(category, ALL_MESSAGES)
    return random.choice(selected_category)

def get_weighted_random_message() -> str:
    """
    Retorna un mensaje con probabilidades ponderadas.
    Mayor probabilidad para mensajes profesionales y humor suave,
    menor para humor negro.
    """
    weights = [
        (PROFESSIONAL_MESSAGES, 20),
        (SOFT_MEDICAL_HUMOR, 20),
        (MEDICAL_KNOWLEDGE_HUMOR, 20),
        (BOLD_FUNNY_MESSAGES, 15),
        (MEDICAL_LIFE_REFERENCES, 15),
        (NERDY_TECHNICAL, 5),
        (ULTRA_RANDOM, 4),
        (DARK_MEDICAL_HUMOR, 1),
    ]
    
    # Crear lista ponderada
    weighted_list = []
    for messages, weight in weights:
        weighted_list.extend(messages * weight)
    
    return random.choice(weighted_list)

# Para testing o debugging
if __name__ == "__main__":
    print("=== Mensaje aleatorio ===")
    print(get_random_message())
    print("\n=== Mensaje profesional ===")
    print(get_message_by_category("professional"))
    print("\n=== Mensaje atrevido ===")
    print(get_message_by_category("bold"))
    print("\n=== Mensaje ponderado (más probable profesional) ===")
    print(get_weighted_random_message())
