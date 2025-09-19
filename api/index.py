# -*- coding: utf-8 -*-
# ==========================================================
# BOT DAAQUI JOYAS - VERSIÓN LIMPIA Y FINAL
# ==========================================================
from flask import Flask, request, jsonify
import requests
import logging
from logging import getLogger
import os
import re
import json
import time
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
import uuid
import gspread
import unicodedata
from datetime import datetime, timezone, timedelta

# Configuración del logger
logging.basicConfig(level=logging.INFO)
logger = getLogger(__name__)

app = Flask(__name__)

# ==========================================================
# 1. INICIALIZACIÓN DE SERVICIOS Y VARIABLES GLOBALES
# ==========================================================
db = None
gc = None
worksheet_pedidos = None
BUSINESS_RULES = {}
FAQ_RESPONSES = {}
BUSINESS_DATA = {}
PALABRAS_CANCELACION = []
FAQ_KEYWORD_MAP = {}
MENU_PRINCIPAL = {}
CATALOGO_PRODUCTOS = {}
MENU_FAQ = {}
CAMPAIGNS_CONFIG = {} # <-- NUEVA VARIABLE AÑADIDA

try:
    # --- CONEXIÓN CON FIREBASE ---
    service_account_info_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    if service_account_info_str:
        service_account_info = json.loads(service_account_info_str)
        cred = credentials.Certificate(service_account_info)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("✅ Conexión con Firebase establecida correctamente.")

        # Carga de toda la configuración desde Firestore...
        docs_a_cargar = {
            'reglas_envio': BUSINESS_RULES,
            'respuestas_faq': FAQ_RESPONSES,
            'datos_negocio': BUSINESS_DATA,
            'menu_principal': MENU_PRINCIPAL,
            'catalogo_productos': CATALOGO_PRODUCTOS,
            'menu_faq': MENU_FAQ
        }
        for doc_id, var in docs_a_cargar.items():
            doc = db.collection('configuracion').document(doc_id).get()
            if doc.exists:
                var.update(doc.to_dict())
                logger.info(f"✅ Documento '{doc_id}' cargado.")
            else:
                logger.warning(f"⚠️ Documento '{doc_id}' no encontrado.")
        
        config_doc = db.collection('configuracion').document('configuracion_general').get()
        if config_doc.exists:
            config_data = config_doc.to_dict()
            PALABRAS_CANCELACION = config_data.get('palabras_cancelacion', ['cancelar'])
            FAQ_KEYWORD_MAP = config_data.get('faq_keyword_map', {})
            logger.info("✅ Configuración general cargada.")
        else:
            logger.warning("⚠️ Documento 'configuracion_general' no encontrado.")
            
        # --- INICIO DEL NUEVO BLOQUE ---
        # Carga la configuración de campañas
        campaigns_doc = db.collection('configuracion').document('campañas_y_ofertas').get()
        if campaigns_doc.exists:
            CAMPAIGNS_CONFIG = campaigns_doc.to_dict()
            logger.info("✅ Configuración de campañas y ofertas cargada.")
        else:
            logger.warning("⚠️ Documento 'campañas_y_ofertas' no encontrado.")
        # --- FIN DEL NUEVO BLOQUE ---

        # --- CONEXIÓN CON GOOGLE SHEETS ---
        creds_json_str = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        sheet_name = os.environ.get('GOOGLE_SHEET_NAME')
        if creds_json_str and sheet_name:
            creds_dict = json.loads(creds_json_str)
            gc = gspread.service_account_from_dict(creds_dict)
            spreadsheet = gc.open(sheet_name)
            worksheet_pedidos = spreadsheet.worksheet("Pedidos")
            logger.info("✅ Conexión con Google Sheets establecida correctamente.")
        else:
            logger.warning("⚠️ Faltan variables de entorno para Google Sheets.")
    else:
        logger.error("❌ La variable de entorno FIREBASE_SERVICE_ACCOUNT_JSON no está configurada.")
except Exception as e:
    logger.error(f"❌ Error crítico durante la inicialización: {e}")

# ==========================================================
# 2. CONFIGURACIÓN DEL NEGOCIO Y VARIABLES GLOBALES
# ==========================================================
WHATSAPP_TOKEN = os.environ.get('WHATSAPP_ACCESS_TOKEN')
VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN', 'JoyasBot2025!')
PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
ADMIN_WHATSAPP_NUMBER = os.environ.get('ADMIN_WHATSAPP_NUMBER')
MAKE_SECRET_TOKEN = os.environ.get('MAKE_SECRET_TOKEN')

RUC_EMPRESA = BUSINESS_DATA.get('ruc', 'RUC_NO_CONFIGURADO')
TITULAR_YAPE = BUSINESS_DATA.get('titular_yape', 'TITULAR_NO_CONFIGURADO')
YAPE_NUMERO = BUSINESS_DATA.get('yape_numero', 'YAPE_NO_CONFIGURADO')

# ==============================================================================
# 3. FUNCIONES DE COMUNICACIÓN CON WHATSAPP
# ==============================================================================
def send_whatsapp_message(to_number, message_data):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("Token de WhatsApp o ID de número no configurados.")
        return
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}', 'Content-Type': 'application/json'}
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    data = {"messaging_product": "whatsapp", "to": to_number, **message_data}
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logger.info(f"Mensaje enviado a {to_number}.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error enviando mensaje a {to_number}: {e.response.text if e.response else e}")

def send_text_message(to_number, text):
    send_whatsapp_message(to_number, {"type": "text", "text": {"body": text}})

def send_image_message(to_number, image_url):
    send_whatsapp_message(to_number, {"type": "image", "image": {"link": image_url}})

def send_interactive_message(to_number, body_text, buttons):
    button_payload = [{"type": "reply", "reply": {"id": b.get('id'), "title": b.get('title')}} for b in buttons[:3]]
    message_data = {"type": "interactive", "interactive": {"type": "button", "body": {"text": body_text}, "action": {"buttons": button_payload}}}
    send_whatsapp_message(to_number, message_data)

# ==============================================================================
# 4. FUNCIONES DE INTERACCIÓN CON FIRESTORE
# ==============================================================================
def get_session(user_id):
    if not db: return None
    try:
        doc = db.collection('sessions').document(user_id).get()
        return doc.to_dict() if doc.exists else None
    except Exception as e:
        logger.error(f"Error obteniendo sesión para {user_id}: {e}")
        return None

def save_session(user_id, session_data):
    if not db: return
    try:
        session_data['last_updated'] = firestore.SERVER_TIMESTAMP
        db.collection('sessions').document(user_id).set(session_data, merge=True)
    except Exception as e:
        logger.error(f"Error guardando sesión para {user_id}: {e}")	

def delete_session(user_id):
    if not db: return
    try:
        db.collection('sessions').document(user_id).delete()
    except Exception as e:
        logger.error(f"Error eliminando sesión para {user_id}: {e}")

# Reemplaza tu función original con esta
def save_completed_sale_and_customer(session_data):
    if not db: return False, None
    try:
        # --- INICIO DE LA CORRECCIÓN ---
        # Define la zona horaria de Perú (UTC-5)
        peru_tz = timezone(timedelta(hours=-5))
        # Obtiene la hora actual en la zona horaria de Perú
        now_in_peru = datetime.now(peru_tz)
        # --- FIN DE LA CORRECCIÓN ---

        sale_id = str(uuid.uuid4())
        customer_id = session_data.get('whatsapp_id')
        precio_total = session_data.get('product_price', 0)
        adelanto = session_data.get('adelanto', 0)
        
        sale_data = {
            "fecha": now_in_peru, # <-- CAMBIO 1: Usamos la hora de Perú
            "id_venta": sale_id,
            "producto_id": session_data.get('product_id'), "producto_nombre": session_data.get('product_name'),
            "precio_venta": precio_total, "tipo_envio": session_data.get('tipo_envio'),
            "metodo_pago": session_data.get('metodo_pago'), "provincia": session_data.get('provincia'),
            "distrito": session_data.get('distrito'), "detalles_cliente": session_data.get('detalles_cliente'),
            "cliente_id": customer_id, "estado_pedido": "Adelanto Pagado",
            "adelanto_recibido": adelanto, "saldo_restante": precio_total - adelanto
        }
        db.collection('ventas').document(sale_id).set(sale_data)
        logger.info(f"Venta {sale_id} guardada.")
        
        customer_data = {
            "nombre_perfil_wa": session_data.get('user_name'),
            "provincia_ultimo_envio": session_data.get('provincia'), "distrito_ultimo_envio": session_data.get('distrito'),
            "detalles_ultimo_envio": session_data.get('detalles_cliente'), "total_compras": firestore.Increment(1),
            "fecha_ultima_compra": now_in_peru # <-- CAMBIO 2: Usamos la hora de Perú
        }
        db.collection('clientes').document(customer_id).set(customer_data, merge=True)
        logger.info(f"Cliente {customer_id} creado/actualizado.")
        return True, sale_data
    except Exception as e:
        logger.error(f"Error guardando venta y cliente: {e}")
        return False, None

# ==============================================================================
# 5. FUNCIONES AUXILIARES Y DE FAQ
# ==============================================================================
def strip_accents(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

def normalize_and_check_district(text):
    clean_text = re.sub(r'soy de|vivo en|estoy en|es en|de', '', text, flags=re.IGNORECASE).strip()
    normalized_input = strip_accents(clean_text.lower())
    
    abreviaturas = BUSINESS_RULES.get('abreviaturas_distritos', {})
    for abbr, full_name in abreviaturas.items():
        if abbr in normalized_input:
            normalized_input = strip_accents(full_name.lower())
            break
            
    distritos_cobertura = BUSINESS_RULES.get('distritos_cobertura_delivery', [])
    if any(normalized_input in strip_accents(d.lower()) for d in distritos_cobertura):
        return next((d.title() for d in distritos_cobertura if normalized_input in strip_accents(d.lower())), None), 'CON_COBERTURA'
        
    distritos_totales = BUSINESS_RULES.get('distritos_lima_total', [])
    if any(normalized_input in strip_accents(d.lower()) for d in distritos_totales):
        return next((d.title() for d in distritos_totales if normalized_input in strip_accents(d.lower())), None), 'SIN_COBERTURA'
        
    return None, 'NO_ENCONTRADO'

def parse_province_district(text):
    clean_text = re.sub(r'soy de|vivo en|mi ciudad es|el distrito es', '', text, flags=re.IGNORECASE).strip()
    for sep in [',', '-', '/']:
        if sep in clean_text:
            parts = [part.strip() for part in clean_text.split(sep, 1)]
            return parts[0].title(), parts[1].title()
    return clean_text.title(), clean_text.title()

def get_delivery_day_message():
    # Define la zona horaria de Perú (UTC-5)
    peru_tz = timezone(timedelta(hours=-5))
    # Obtiene la hora actual específicamente para la zona horaria de Perú
    now_in_peru = datetime.now(peru_tz)
    
    # La lógica se mantiene, pero ahora usa la hora correcta de Perú
    return BUSINESS_RULES.get('mensaje_dia_habil', 'mañana') if now_in_peru.weekday() < 4 else BUSINESS_RULES.get('mensaje_fin_de_semana', 'el Lunes')

def check_and_handle_faq(from_number, text):
    text_lower = text.lower()
    for key, keywords in FAQ_KEYWORD_MAP.items():
        if any(keyword in text_lower for keyword in keywords):
            response_text = FAQ_RESPONSES.get(key)
            if response_text:
                send_text_message(from_number, response_text)
                return True
    return False

# Reemplaza la función que tienes en tu archivo con esta
def guardar_pedido_en_sheet(sale_data):
    if not worksheet_pedidos:
        logger.error("[Sheets] La conexión no está inicializada. No se puede guardar el pedido.")
        return False
    try:
        # --- INICIO DE LA CORRECCIÓN ---
        # Define la zona horaria de Perú (UTC-5)
        peru_tz = timezone(timedelta(hours=-5))
        # Obtiene la hora actual en la zona horaria de Perú y la formatea
        timestamp_peru = datetime.now(peru_tz).strftime("%d/%m/%Y %H:%M:%S")
        # --- FIN DE LA CORRECCIÓN ---

        # Define el orden correcto de las columnas para la hoja de cálculo
        nueva_fila = [
            timestamp_peru, # <-- Usamos la nueva variable con la hora correcta
            sale_data.get('id_venta', 'N/A'),
            sale_data.get('producto_nombre', 'N/A'),
            sale_data.get('precio_venta', 0),
            sale_data.get('tipo_envio', 'N/A'),
            sale_data.get('metodo_pago', 'N/A'),
            sale_data.get('adelanto_recibido', 0),
            sale_data.get('saldo_restante', 0),
            sale_data.get('provincia', 'N/A'),
            sale_data.get('distrito', 'N/A'),
            sale_data.get('detalles_cliente', 'N/A'),
            sale_data.get('cliente_id', 'N/A')
        ]
        worksheet_pedidos.append_row(nueva_fila)
        logger.info(f"[Sheets] Pedido {sale_data.get('id_venta')} guardado exitosamente.")
        return True
    except Exception as e:
        logger.error(f"[Sheets] ERROR INESPERADO al guardar: {e}")
        return False

# ==============================================================================
# 6. LÓGICA DE LA CONVERSACIÓN - ETAPA INICIAL
# ==============================================================================
def start_sales_flow(from_number, user_name, product_id):
    """Inicia un flujo de venta: guarda la sesión y envía el mensaje de bienvenida."""
    product_doc = db.collection('productos').document(product_id).get()
    if not product_doc.exists:
        send_text_message(from_number, "Lo siento, hubo un problema al cargar la información del producto.")
        return
        
    product_data = product_doc.to_dict()
    
    # Paso 1: Guardar la sesión y establecer el estado para esperar la respuesta
    session_data = {
        "state": "awaiting_occasion_response", # Espera la respuesta a la pregunta "¿esta magia es para ti...?"
        "product_id": product_id,
        "product_name": product_data.get('nombre'),
        "product_price": float(product_data.get('precio_base', 0)),
        "user_name": user_name,
        "whatsapp_id": from_number,
        "is_upsell": False
    }
    save_session(from_number, session_data)
    
    # Paso 2: Enviar la imagen del producto
    url_img = product_data.get('imagenes', {}).get('principal')
    if url_img:
        send_image_message(from_number, url_img)
        time.sleep(1)
    
    # Paso 3: Enviar el nuevo mensaje de bienvenida para iniciar la conversación
    send_welcome_message(from_number, user_name)

def send_welcome_message(from_number, user_name):
    """Envía el mensaje de bienvenida persuasivo y luego la pregunta con botones."""
    welcome_text = (
        f"¡Hola {user_name}! Estás a punto de descubrir el *secreto* del Collar Mágico Girasol Radiant. 🤫✨\n"
        "No es solo una joya, es una pieza que *se conecta contigo*, cambiando de color para reflejar tu propia energía. 💖\n"
        "Debido a su diseño único, tenemos *pocas unidades disponibles* en esta campaña. ⚠️\n"
        "Puedes llevarte la tuya por *S/ 69.00* (incluye *envío gratis* a todo el Perú 🇵🇪🚚)."
    )
    # Primero enviamos el texto principal
    send_text_message(from_number, welcome_text)
    time.sleep(1.5) # Pausa para que el texto y los botones no lleguen juntos
    
    # Luego, enviamos la pregunta con los botones
    question_text = "¿Esta *magia* es para ti o para sorprender a alguien especial? 🎁"
    botones = [
        {'id': 'es_regalo', 'title': '🎁 Es para un regalo'},
        {'id': 'es_para_mi', 'title': '💖 Es para mí'}
    ]
    send_interactive_message(from_number, question_text, botones)

def handle_initial_message(from_number, user_name, text):
    # --- LÓGICA MEJORADA: LEE LA CONFIGURACIÓN DESDE FIREBASE ---
    anuncio_config = CAMPAIGNS_CONFIG.get('anuncio_principal', {})
    frase_anuncio = anuncio_config.get('frase_exacta')
    producto_id_anuncio = anuncio_config.get('producto_id')

    # 1. Revisa si es la frase exacta del anuncio cargada desde Firebase
    if frase_anuncio and text == frase_anuncio:
        logger.info(f"Coincidencia de anuncio desde Firebase para: {from_number}")
        start_sales_flow(from_number, user_name, producto_id_anuncio)
        return

    # 2. Revisa si es un ID de producto (del menú del catálogo)
    try:
        if db.collection('productos').document(text).get().exists:
            logger.info(f"ID de producto del catálogo detectado: {text}")
            start_sales_flow(from_number, user_name, text)
            return
    except Exception:
        pass 
    
    # 3. Revisa si es una pregunta frecuente (FAQ)
    if check_and_handle_faq(from_number, text):
        return
        
    # 4. Si no, muestra el menú principal
    if MENU_PRINCIPAL:
        welcome_message = MENU_PRINCIPAL.get('mensaje_bienvenida', '¡Hola! ¿Cómo puedo ayudarte?')
        botones = [{'id': '1', 'title': '🛍️ Ver Colección'}, {'id': '2', 'title': '❓ Preguntas'}]
        send_interactive_message(from_number, welcome_message, botones)
        save_session(from_number, {"state": "awaiting_menu_choice", "user_name": user_name, "whatsapp_id": from_number})
    else:
        send_text_message(from_number, f"¡Hola {user_name}! 👋🏽✨ Bienvenida a *Daaqui Joyas*.")

def handle_menu_choice(from_number, text, session, product_data):
    choice = text.strip()
    if choice == '1':
        if CATALOGO_PRODUCTOS:
            mensaje = "¡Genial! Estas son nuestras colecciones. Elige una para ver detalles:"
            catalogo_texto = "\n".join([f"{idx}️⃣ {v.get('nombre', '')}" for idx, (k, v) in enumerate(sorted(CATALOGO_PRODUCTOS.items()), 1)])
            send_text_message(from_number, f"{mensaje}\n\n{catalogo_texto}")
            session['state'] = 'awaiting_product_choice'
            save_session(from_number, session)
        else:
            send_text_message(from_number, "Lo siento, no pude cargar el catálogo.")
    elif choice == '2':
        if MENU_FAQ:
            mensaje = "¡Claro! Nuestras dudas más comunes. Elige una para ver la respuesta:"
            faq_texto = "\n".join([f"{k}️⃣ {v.get('pregunta', '')}" for k, v in sorted(MENU_FAQ.items())])
            send_text_message(from_number, f"{mensaje}\n\n{faq_texto}")
            session['state'] = 'awaiting_faq_choice'
            save_session(from_number, session)
        else:
            send_text_message(from_number, "Lo siento, no pude cargar las preguntas.")
    else:
        send_text_message(from_number, "Opción no válida. Elige una del menú.")

def handle_product_choice(from_number, text, session, product_data):
    choice = text.strip()
    product_list = sorted(CATALOGO_PRODUCTOS.items())
    if choice.isdigit() and 0 < int(choice) <= len(product_list):
        product_info = product_list[int(choice) - 1][1] 
        if product_id := product_info.get('product_id'):
            handle_initial_message(from_number, session.get('user_name', 'Usuario'), product_id)
            return
    send_text_message(from_number, "Opción no válida. Elige un número del catálogo.")

def handle_faq_choice(from_number, text, session, product_data):
    choice = text.strip()
    faq_info = MENU_FAQ.get(choice)
    if faq_info and (clave := faq_info.get('clave_respuesta')):
        respuesta = FAQ_RESPONSES.get(clave, "No encontré una respuesta.")
        send_text_message(from_number, respuesta)
        delete_session(from_number)
    else:
        send_text_message(from_number, "Opción no válida. Elige un número del menú.")

# ==============================================================================
# 7. LÓGICA DE LA CONVERSACIÓN - ETAPA 2 (FLUJO DE COMPRA)
# ==============================================================================
def handle_occasion_response(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    # Revisa si el texto NO es una de las opciones esperadas en los botones
    if text not in ['es_regalo', 'es_para_mi']:
        # Si no es una opción, intenta manejarla como una FAQ
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5) # Pausa para que el usuario lea la respuesta
            # Vuelve a hacer la pregunta original con los botones
            question_text = "Espero haber aclarado tu duda. 😊 Continuando... ¿esta magia es para ti o es un regalo?"
            botones = [
                {'id': 'es_regalo', 'title': '🎁 Es para un regalo'},
                {'id': 'es_para_mi', 'title': '💖 Es para mí'}
            ]
            send_interactive_message(from_number, question_text, botones)
            return # Detiene la ejecución para esperar la nueva respuesta
        # Si no fue una FAQ, simplemente ignoramos y esperamos una respuesta válida (botón o nueva pregunta)
        # Podríamos opcionalmente reenviar los botones aquí, pero es mejor esperar para no ser spam.
        return

    # --- LÓGICA DEL SIGUIENTE PASO ---
    # Si el cliente SÍ presionó un botón, continuamos con el flujo normal.
    url_imagen_empaque = product_data.get('imagenes', {}).get('empaque')
    if url_imagen_empaque:
        send_image_message(from_number, url_imagen_empaque)
        time.sleep(1)
    
    detalles = product_data.get('detalles', {})
    mensaje_persuasion_1 = (f"¡Maravillosa elección! ✨ El *{product_data.get('nombre')}* es pura energía. Aquí tienes todos los detalles:\n\n"
                            f"💎 *Material:* {detalles.get('material', 'alta calidad')}\n"
                            f"🔮 *La Magia:* {detalles.get('magia', 'una pieza única')}\n"
                            f"🎁 *Presentación:* {detalles.get('empaque', 'incluye empaque de regalo')}")
    send_text_message(from_number, mensaje_persuasion_1)
    time.sleep(1.5)
    
    mensaje_persuasion_2 = (f"Para tu total seguridad, somos Daaqui Joyas, un negocio formal con *RUC {RUC_EMPRESA}*. ¡Tu compra es 100% segura! 🇵🇪\n\n"
                            "¿Te gustaría coordinar tu pedido ahora para asegurar el tuyo?")
    botones_compra = [{'id': 'si_coordinar', 'title': '✅ Sí, coordinar'}, {'id': 'no_gracias', 'title': 'No, gracias'}]
    send_interactive_message(from_number, mensaje_persuasion_2, botones_compra)
    
    # Actualizamos el estado al siguiente paso
    session['state'] = 'awaiting_purchase_decision'
    save_session(from_number, session)
    
def handle_purchase_decision(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    # Revisa si el texto NO es una de las opciones esperadas en los botones
    if text not in ['si_coordinar', 'no_gracias']:
        # Si no es una opción, intenta manejarla como una FAQ
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5) # Pausa para que el usuario lea la respuesta de la FAQ
            # Vuelve a hacer la pregunta original con los botones
            reprompt_message = ("Continuando con tu pedido... 😊\n\n¿Te gustaría coordinar ahora para asegurar el tuyo?")
            botones = [{'id': 'si_coordinar', 'title': '✅ Sí, coordinar'}, {'id': 'no_gracias', 'title': 'No, gracias'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return # Detiene la ejecución para esperar la nueva respuesta

    # --- LÓGICA ORIGINAL DE LA FUNCIÓN ---
    if text == 'si_coordinar':
        url_imagen_upsell = product_data.get('imagenes', {}).get('upsell')
        if url_imagen_upsell:
            send_image_message(from_number, url_imagen_upsell)
            time.sleep(1)
            
        upsell_message_1 = (f"¡Excelente elección! Pero espera... por decidir llevar tu collar, ¡acabas de desbloquear una oferta exclusiva! ✨\n\n"
                            "Añade un segundo Collar Mágico y te incluimos de regalo dos cadenas de diseño italiano.\n\n"
                            "Tu pedido se ampliaría a:\n"
                            "✨ 2 Collares Mágicos\n🎁 2 Cadenas de Regalo\n🎀 2 Cajitas Premium\n"
                            "💎 Todo por un único pago de S/ 99.00")
        send_text_message(from_number, upsell_message_1)
        time.sleep(1.5)
        
        mensaje_decision = "Para continuar con tu pedido, ¿cuál será tu elección?"
        botones = [{'id': 'oferta', 'title': '🔥 Quiero la oferta'}, {'id': 'continuar', 'title': 'Continuar con uno'}]
        send_interactive_message(from_number, mensaje_decision, botones)
        session['state'] = 'awaiting_upsell_decision'
        save_session(from_number, session)
    else: # Esto ahora solo se activará si el cliente presiona 'No, gracias'
        delete_session(from_number)
        send_text_message(from_number, "Entendido. Si cambias de opinión, aquí estaré. ¡Que tengas un buen día! 😊")

def handle_upsell_decision(from_number, text, session, product_data):
    # (El filtro inteligente para interrupciones se mantiene igual)
    if text not in ['oferta', 'continuar']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            reprompt_message = "Aclarada tu duda, para continuar con tu pedido, ¿cuál será tu elección?"
            botones = [{'id': 'oferta', 'title': '🔥 Quiero la oferta'}, {'id': 'continuar', 'title': 'Continuar con uno'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA MEJORADA: LEE LA OFERTA DESDE FIREBASE ---
    upsell_config = CAMPAIGNS_CONFIG.get('oferta_upsell', {})
    nombre_oferta = upsell_config.get('nombre_producto', 'Oferta Especial')
    precio_oferta = upsell_config.get('precio', 99.00)
    oferta_activa = upsell_config.get('activa', False)

    # Solo actualiza la sesión con la oferta si está activa en Firebase
    if text == 'oferta' and oferta_activa:
        session.update({"product_name": nombre_oferta, "product_price": precio_oferta, "is_upsell": True})
        send_text_message(from_number, "¡Genial! Has elegido la oferta. ✨")
    else:
        session['is_upsell'] = False
        send_text_message(from_number, "¡Perfecto! Continuamos con tu collar individual. ✨")
    
    time.sleep(1)
    
    mensaje = "¡Perfecto! Tu joya está casi en camino. Para coordinar tu envío gratis, indícame si el envío es para:"
    botones = [{'id': 'lima', 'title': '📍 Lima'}, {'id': 'provincia', 'title': '🚚 Provincia'}]
    send_interactive_message(from_number, mensaje, botones)
    session['state'] = 'awaiting_location'
    save_session(from_number, session)

def handle_location(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    if text not in ['lima', 'provincia']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            reprompt_message = "Espero haber aclarado tu duda. Continuando... Para coordinar tu envío gratis, indícame si es para:"
            botones = [{'id': 'lima', 'title': '📍 Lima'}, {'id': 'provincia', 'title': '🚚 Provincia'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA ORIGINAL DE LA FUNCIÓN ---
    if text == 'lima':
        session.update({"state": "awaiting_lima_district", "provincia": "Lima"})
        save_session(from_number, session)
        send_text_message(from_number, "¡Genial! ✨ Para saber qué tipo de envío te corresponde, por favor, dime: ¿en qué distrito te encuentras? 📍")
    elif text == 'provincia':
        session['state'] = 'awaiting_province_district'
        save_session(from_number, session)
        send_text_message(from_number, "¡Entendido! Para continuar, indícame tu *provincia y distrito*. ✍🏽\n\n📝 *Ej: Arequipa, Arequipa*")
    else:
        # Esta respuesta ahora es para cuando el cliente escribe algo que no es ni FAQ ni una opción válida
        mensaje = "Por favor, elige una de las dos opciones del menú:"
        botones = [{'id': 'lima', 'title': '📍 Lima'}, {'id': 'provincia', 'title': '🚚 Provincia'}]
        send_interactive_message(from_number, mensaje, botones)

def handle_province_district(from_number, text, session, product_data):
    provincia, distrito = parse_province_district(text)
    session.update({"tipo_envio": "Provincia Shalom", "metodo_pago": "Adelanto y Saldo (Yape/Plin)", "provincia": provincia, "distrito": distrito})
    adelanto = BUSINESS_RULES.get('adelanto_shalom', 20)
    
    # --- CORRECCIÓN DE FORMATO Y TEXTO ---
    mensaje = (f"¡Genial! Prepararemos tu envío para *{provincia}* vía Shalom.\n\n"
               f"Nuestros despachos a provincia se están agendando rápidamente ⚠️. Para *asegurar y priorizar* tu paquete en la próxima salida, solicitamos un adelanto de *S/ {adelanto:.2f}* como compromiso de recojo.\n\n"
               "¿Procedemos?")
    
    botones = [{'id': 'si_acuerdo', 'title': '✅ Sí, de acuerdo'}, {'id': 'no_acuerdo', 'title': 'No en este momento'}]
    send_interactive_message(from_number, mensaje, botones)
    session['state'] = 'awaiting_shalom_agreement'
    save_session(from_number, session)

def handle_lima_district(from_number, text, session, product_data):
    distrito, status = normalize_and_check_district(text)
    if status != 'NO_ENCONTRADO':
        session['distrito'] = distrito
        if status == 'CON_COBERTURA':
            session.update({"state": "awaiting_delivery_details", "tipo_envio": "Lima Contra Entrega", "metodo_pago": "Contra Entrega (Efectivo/Yape/Plin)"})
            save_session(from_number, session)
            mensaje = (f"¡Excelente! Tenemos cobertura en *{distrito}*. 🏙️\n\n"
                       "Para registrar tu pedido, envíame en *un solo mensaje* tu *Nombre, Dirección exacta* y *Referencia*.\n\n"
                       "📝 *Ej: Ana Pérez, Jr. Gamarra 123, Depto 501. Al lado de la farmacia.*")
            send_text_message(from_number, mensaje)
        elif status == 'SIN_COBERTURA':
            session.update({"tipo_envio": "Lima Shalom", "metodo_pago": "Adelanto y Saldo (Yape/Plin)"})
            adelanto = BUSINESS_RULES.get('adelanto_shalom', 20)
            
            # --- CORRECCIÓN DE TEXTO PARA SER CONSISTENTE ---
            mensaje = (f"¡Genial! Prepararemos tu envío para *{distrito}* vía *Shalom*.\n\n"
                       f"Nuestros despachos se están agendando rápidamente ⚠️. Para *asegurar y priorizar* tu paquete en la próxima salida, solicitamos un adelanto de *S/ {adelanto:.2f}* como compromiso de recojo.\n\n"
                       "¿Procedemos?")

            botones = [{'id': 'si_acuerdo', 'title': '✅ Sí, de acuerdo'}, {'id': 'no_acuerdo', 'title': 'No en este momento'}]
            send_interactive_message(from_number, mensaje, botones)
            session['state'] = 'awaiting_shalom_agreement'
            save_session(from_number, session)
    else:
        send_text_message(from_number, "No pude reconocer ese distrito. Por favor, intenta escribirlo de nuevo.")

def handle_customer_details(from_number, text, session, product_data):
    session.update({"detalles_cliente": text})
    resumen = ("¡Gracias! Revisa que todo esté correcto:\n\n"
               f"*Resumen del Pedido*\n"
               f"💎 {session.get('product_name', '')}\n"
               f"💵 Total: S/ {session.get('product_price', 0):.2f}\n"
               f"🚚 Envío: *{session.get('distrito', session.get('provincia', ''))}* - ¡Gratis!\n"
               f"💳 Pago: {session.get('metodo_pago', '')}\n\n"
               f"*Datos de Entrega*\n"
               f"{session.get('detalles_cliente', '')}\n\n"
               "¿Confirmas que todo es correcto?")
    botones = [{'id': 'si_correcto', 'title': '✅ Sí, todo correcto'}, {'id': 'corregir', 'title': '📝 Corregir datos'}]
    send_interactive_message(from_number, resumen, botones)
    session['state'] = 'awaiting_final_confirmation'
    save_session(from_number, session)

def handle_shalom_agreement(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    if text not in ['si_acuerdo', 'no_acuerdo']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            # Vuelve a hacer la pregunta original
            adelanto = BUSINESS_RULES.get('adelanto_shalom', 20)
            reprompt_message = (f"Aclarada tu duda. 😊 Para continuar, te recuerdo que para asegurar tu paquete, solicitamos un adelanto de S/ {adelanto:.2f} como compromiso de recojo.\n\n"
                                "¿Procedemos?")
            botones = [{'id': 'si_acuerdo', 'title': '✅ Sí, de acuerdo'}, {'id': 'no_acuerdo', 'title': 'No en este momento'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA ORIGINAL DE LA FUNCIÓN ---
    if text == 'si_acuerdo':
        session['state'] = 'awaiting_shalom_experience'
        save_session(from_number, session)
        mensaje = "¡Genial! Para hacer el proceso más fácil, cuéntame: ¿alguna vez has recogido un pedido en una agencia Shalom? 🙋🏽‍♀️"
        botones = [{'id': 'si_conozco', 'title': '✅ Sí, ya conozco'}, {'id': 'no_conozco', 'title': 'No, explícame más'}]
        send_interactive_message(from_number, mensaje, botones)
    else:
        delete_session(from_number)
        send_text_message(from_number, "Comprendo. Si cambias de opinión, aquí estaré. ¡Gracias! 😊")

def handle_shalom_experience(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    if text not in ['si_conozco', 'no_conozco']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            # Vuelve a hacer la pregunta original
            reprompt_message = "Aclarada tu duda. 😊 Para continuar, cuéntame, ¿alguna vez has recogido un pedido en una agencia Shalom?"
            botones = [{'id': 'si_conozco', 'title': '✅ Sí, ya conozco'}, {'id': 'no_conozco', 'title': 'No, explícame más'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA ORIGINAL DE LA FUNCIÓN ---
    if text == 'si_conozco':
        session['state'] = 'awaiting_shalom_details'
        save_session(from_number, session)
        mensaje = ("¡Excelente! Entonces ya conoces el proceso. ✅\n\n"
                   "Para terminar, bríndame en un solo mensaje tu *Nombre Completo, DNI* y la *dirección exacta de la agencia Shalom* donde recogerás. ✍🏽\n\n"
                   "📝 *Ej: Juan Quispe, 45678901, Av. Pardo 123, Miraflores.*")
        send_text_message(from_number, mensaje)
    else: # 'no_conozco'
        session['state'] = 'awaiting_shalom_agency_knowledge'
        save_session(from_number, session)
        mensaje = ("¡No te preocupes! Te explico: Shalom es una empresa de envíos. Te damos un código de seguimiento, y cuando tu pedido llega a la agencia, nos yapeas el saldo restante. Apenas confirmemos, te damos la clave secreta para el recojo. ¡Es 100% seguro! 🔒\n\n"
                   "¿Conoces la dirección de alguna agencia Shalom cerca a ti?")
        
        botones = [
            {'id': 'shalom_knows_addr_yes', 'title': 'Sí, la conozco'},
            {'id': 'shalom_knows_addr_no', 'title': 'No, necesito buscar'}
        ]
        send_interactive_message(from_number, mensaje, botones)

def handle_shalom_agency_knowledge(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    if text not in ['shalom_knows_addr_yes', 'shalom_knows_addr_no']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            # Vuelve a hacer la pregunta original
            reprompt_message = "Aclarada tu duda. 😊 Continuando, ¿conoces la dirección de alguna agencia Shalom cerca a ti?"
            botones = [{'id': 'shalom_knows_addr_yes', 'title': 'Sí, la conozco'}, {'id': 'shalom_knows_addr_no', 'title': 'No, necesito buscar'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA ORIGINAL DE LA FUNCIÓN ---
    if text == 'shalom_knows_addr_yes':
        session['state'] = 'awaiting_shalom_details'
        save_session(from_number, session)
        mensaje = ("¡Perfecto! Por favor, bríndame en un solo mensaje tu *Nombre Completo, DNI* y la *dirección de esa agencia Shalom*. ✍🏽")
        send_text_message(from_number, mensaje)
    else: # 'shalom_knows_addr_no'
        delete_session(from_number)
        send_text_message(from_number, "Entiendo. 😔 Te recomiendo buscar en Google 'Shalom agencias' para encontrar la más cercana. Cuando la tengas, puedes iniciar la conversación de nuevo. ¡Gracias por tu interés!")	

def handle_final_confirmation(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    if text not in ['si_correcto', 'corregir']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            # Vuelve a hacer la pregunta original con el resumen del pedido
            reprompt_message = ("Espero haber aclarado tu duda. 😊 Por favor, revisa nuevamente que todo esté correcto y confirma tu pedido:\n\n"
                                f"*Resumen del Pedido*\n"
                                f"💎 {session.get('product_name', '')}\n"
                                f"💵 Total: S/ {session.get('product_price', 0):.2f}\n"
                                f"🚚 Envío: *{session.get('distrito', session.get('provincia', ''))}* - ¡Gratis!\n"
                                f"💳 Pago: {session.get('metodo_pago', '')}\n\n"
                                f"*Datos de Entrega*\n"
                                f"{session.get('detalles_cliente', '')}\n\n"
                                "¿Confirmas que todo es correcto?")
            botones = [{'id': 'si_correcto', 'title': '✅ Sí, todo correcto'}, {'id': 'corregir', 'title': '📝 Corregir datos'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA ORIGINAL MODIFICADA ---
    if text == 'si_correcto':
        if session.get('tipo_envio') == 'Lima Contra Entrega':
            adelanto = float(BUSINESS_RULES.get('adelanto_lima_delivery', 10))
            session.update({'adelanto': adelanto})
            
            # 1. Restaurar el mensaje persuasivo largo
            mensaje_largo = (
                "¡Perfecto! Tu pedido contra entrega está listo para ser agendado. ✨\n\n"
                "Nuestras rutas de reparto para mañana 🚚 ya se están llenando y tenemos *cupos limitados* ⚠️. Para asegurar tu espacio y priorizar tu entrega, solo solicitamos un adelanto de *S/ 10.00*.\n\n"
                "Este pequeño monto confirma tu compromiso y nos permite seguir ofreciendo *envío gratis* a clientes serios como tú. Por supuesto, se descuenta del total."
            )
            send_text_message(from_number, mensaje_largo)
            time.sleep(2) # Pausa para leer el texto
            
            # 2. Usar la nueva pregunta y botones que elegiste
            pregunta_final = "¡Casi es tuyo! ✨ Tu Collar Mágico está esperando. ¿Aseguramos tu joya?"
            botones = [
                {'id': 'si_proceder', 'title': '💖 ¡Sí, lo quiero!'},
                {'id': 'no_proceder', 'title': 'Ahora no, gracias'}
            ]
            send_interactive_message(from_number, pregunta_final, botones)
            
            session['state'] = 'awaiting_lima_payment_agreement'
            save_session(from_number, session)
        else: # Shalom
            adelanto = float(BUSINESS_RULES.get('adelanto_shalom', 20))
            session.update({'adelanto': adelanto, 'state': 'awaiting_shalom_payment'})
            save_session(from_number, session)
            mensaje = (f"¡Genial! Puedes realizar el adelanto de *S/ {adelanto:.2f}* a:\n\n"
                       f"💳 *YAPE / PLIN:* {YAPE_NUMERO}\n"
                       f"👤 *Titular:* {TITULAR_YAPE}\n\n"
                       "Una vez realizado, envíame la *captura de pantalla* para validar.")
            send_text_message(from_number, mensaje)
    else: # 'corregir'
        previous_state = 'awaiting_delivery_details' if session.get('tipo_envio') == 'Lima Contra Entrega' else 'awaiting_shalom_details'
        session['state'] = previous_state
        save_session(from_number, session)
        send_text_message(from_number, "¡Claro, lo corregimos! 😊 Envíame nuevamente la información completa en un solo mensaje.")

def handle_lima_payment_agreement(from_number, text, session, product_data):
    # --- INICIO DEL FILTRO INTELIGENTE PARA INTERRUPCIONES ---
    if text not in ['si_proceder', 'no_proceder']:
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            # Vuelve a hacer la pregunta original
            reprompt_message = "Aclarada tu duda. 😊 Para continuar, ¿aseguramos tu joya?"
            botones = [
                {'id': 'si_proceder', 'title': '💖 ¡Sí, lo quiero!'},
                {'id': 'no_proceder', 'title': 'Ahora no, gracias'}
            ]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    # --- LÓGICA ORIGINAL DE LA FUNCIÓN ---
    if text == 'si_proceder':
        session['state'] = 'awaiting_lima_payment'
        save_session(from_number, session)
        mensaje = (f"¡Genial! Puedes realizar el adelanto de *S/ {session.get('adelanto', 10):.2f}* a:\n\n"
                   f"💳 *YAPE / PLIN:* {YAPE_NUMERO}\n"
                   f"👤 *Titular:* {TITULAR_YAPE}\n\n"
                   "Una vez realizado, envíame la *captura de pantalla* para validar.")
        send_text_message(from_number, mensaje)
    else: # 'no_proceder'
        delete_session(from_number)
        send_text_message(from_number, "Entendido. Si cambias de opinión, aquí estaré. ¡Gracias!")

def handle_payment_received(from_number, text, session, product_data):
    if text == "COMPROBANTE_RECIBIDO":
        guardado_exitoso, sale_data = save_completed_sale_and_customer(session)
        if guardado_exitoso:
            guardar_pedido_en_sheet(sale_data) 
            if ADMIN_WHATSAPP_NUMBER:
                admin_message = (f"🎉 ¡Nueva Venta Confirmada! 🎉\n"
                                 f"Producto: {sale_data.get('producto_nombre')}\nTipo: {sale_data.get('tipo_envio')}\n"
                                 f"Cliente: {sale_data.get('cliente_id')}\nDetalles:\n{sale_data.get('detalles_cliente')}")
                send_text_message(ADMIN_WHATSAPP_NUMBER, admin_message)
                
            if session.get('tipo_envio') == 'Lima Contra Entrega':
                dia_entrega = get_delivery_day_message()
                horario = BUSINESS_RULES.get('horario_entrega_lima', 'durante el día')
                mensaje_resumen = (f"¡Adelanto confirmado, gracias! ✨ Aquí tienes el resumen final de tu pedido y los detalles de la entrega:\n\n"
                                   f"*Tu Pedido en Detalle:*\n"
                                   f"💰 *Costo Total:* S/ {sale_data.get('precio_venta', 0):.2f}\n"
                                   f"✅ *Adelanto Recibido:* - S/ {sale_data.get('adelanto_recibido', 0):.2f}\n"
                                   f"💵 *Saldo a Pagar al recibir:* S/ {sale_data.get('saldo_restante', 0):.2f}\n\n"
                                   f"*Entrega Programada:*\n"
                                   f"🗓️ *Día:* {dia_entrega.title()}\n"
                                   f"⏰ *Horario:* {horario}\n\n"
                                   f"A continuación, te pediré un último paso para asegurar tu envío.")
                send_text_message(from_number, mensaje_resumen)
                time.sleep(1.5)
                mensaje_solicitud = (f"¡Ya casi es tuya! 💎\n\n"
                                     f"Para garantizar una entrega exitosa *{dia_entrega}*, por favor confirma que habrá alguien disponible para recibir tu joya y pagar el saldo 💵.\n\n"
                                     f"👉 Solo presiona *CONFIRMO* y tu pedido quedará asegurado en la ruta. 🚚✨")
                botones = [{'id': 'confirmo_entrega_lima', 'title': '✅ CONFIRMO'}]
                send_interactive_message(from_number, mensaje_solicitud, botones)
                session['state'] = 'awaiting_delivery_confirmation_lima'
                save_session(from_number, session)
            else: # Shalom
                # <-- INICIO DE LA MODIFICACIÓN -->
                resumen_shalom = (f"¡Adelanto confirmado, gracias! ✨ Aquí tienes el resumen final de tu pedido:\n\n"
                                  f"*Tu Pedido en Detalle:*\n"
                                  f"💰 *Costo Total:* S/ {sale_data.get('precio_venta', 0):.2f}\n"
                                  f"✅ *Adelanto Recibido:* - S/ {sale_data.get('adelanto_recibido', 0):.2f}\n"
                                  f"------------------------------------\n"
                                  f"💵 *Saldo a Pagar:* S/ {sale_data.get('saldo_restante', 0):.2f}")
                send_text_message(from_number, resumen_shalom)
                time.sleep(1.5)

                tiempo_entrega = "1-2 días hábiles" if session.get('tipo_envio') == 'Lima Shalom' else "3-5 días hábiles"
                proximos_pasos = (f"📝 *Próximos Pasos:*\n\n"
                                  f"⏳ En las próximas 24h hábiles te enviaremos tu código de seguimiento 📲. El tiempo de entrega en agencia es de *{tiempo_entrega}* 📦.")
                # <-- FIN DE LA MODIFICACIÓN -->
                send_text_message(from_number, proximos_pasos)
                delete_session(from_number)
        else:
            send_text_message(from_number, "¡Uy! Hubo un problema al registrar tu pedido. Un asesor se pondrá en contacto contigo.")
    else:
        send_text_message(from_number, "Estoy esperando la *captura de pantalla* de tu pago. 😊")

def handle_delivery_confirmation_lima(from_number, text, session, product_data):
    if 'confirmo' not in text.lower() and text != 'confirmo_entrega_lima':
        if check_and_handle_faq(from_number, text):
            time.sleep(1.5)
            dia_entrega = get_delivery_day_message()
            reprompt_message = (f"Espero haber aclarado tu duda. 😊 Para finalizar, solo necesito que confirmes que habrá alguien disponible para recibir tu joya y pagar el saldo el día {dia_entrega}.")
            botones = [{'id': 'confirmo_entrega_lima', 'title': '✅ CONFIRMO'}]
            send_interactive_message(from_number, reprompt_message, botones)
            return

    if 'confirmo' in text.lower() or text == 'confirmo_entrega_lima':
        
        # <-- INICIO DE LA MODIFICACIÓN 2 -->
        mensaje_final = (
            "¡Listo! ✅ Tu pedido ha sido *confirmado en la ruta* 🚚.\n\n"
            "De parte de todo el equipo de *Daaqui Joyas*, ¡muchas gracias por tu compra! 🎉😊"
        )
        # <-- FIN DE LA MODIFICACIÓN 2 -->

        send_text_message(from_number, mensaje_final)
        delete_session(from_number)
    else:
        mensaje_solicitud = ("Por favor, para asegurar tu pedido, presiona el botón de confirmación.")
        botones = [{'id': 'confirmo_entrega_lima', 'title': '✅ CONFIRMO'}]
        send_interactive_message(from_number, mensaje_solicitud, botones)

# ==============================================================================
# 8. MANEJADOR CENTRAL Y WEBHOOK
# ==============================================================================
STATE_HANDLERS = {
    "awaiting_menu_choice": handle_menu_choice, "awaiting_product_choice": handle_product_choice,
    "awaiting_faq_choice": handle_faq_choice, "awaiting_occasion_response": handle_occasion_response,
    "awaiting_purchase_decision": handle_purchase_decision, "awaiting_upsell_decision": handle_upsell_decision,
    "awaiting_location": handle_location, "awaiting_province_district": handle_province_district,
    "awaiting_lima_district": handle_lima_district, "awaiting_delivery_details": handle_customer_details,
    "awaiting_shalom_details": handle_customer_details, "awaiting_shalom_agreement": handle_shalom_agreement,
    "awaiting_shalom_experience": handle_shalom_experience, "awaiting_shalom_agency_knowledge": handle_shalom_agency_knowledge,
    "awaiting_final_confirmation": handle_final_confirmation, "awaiting_lima_payment_agreement": handle_lima_payment_agreement,
    "awaiting_lima_payment": handle_payment_received, "awaiting_shalom_payment": handle_payment_received,
    "awaiting_delivery_confirmation_lima": handle_delivery_confirmation_lima,
}

@app.route('/api/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return 'Forbidden', 403
    
    data = request.get_json()
    if data.get('object') == 'whatsapp_business_account':
        for entry in data.get('entry', []):
            for change in entry.get('changes', []):
                if change.get('field') == 'messages' and (value := change.get('value', {})):
                    if messages := value.get('messages'):
                        for message in messages:
                            try:
                                process_message(message, value.get('contacts', []))
                            except Exception as e:
                                logger.error(f"Error procesando un mensaje: {e}")
    return jsonify({'status': 'success'}), 200

def process_message(message, contacts):
    from_number = message.get('from')
    user_name = next((c.get('profile', {}).get('name', 'Usuario') for c in contacts if c.get('wa_id') == from_number), 'Usuario')
    session = get_session(from_number)
    
    text_body = ""
    message_type = message.get('type')
    if message_type == 'text':
        text_body = message.get('text', {}).get('body', '')
    elif message_type == 'interactive' and message.get('interactive', {}).get('type') == 'button_reply':
        text_body = message.get('interactive', {}).get('button_reply', {}).get('id', '')
    elif message_type == 'image' and session and session.get('state') in ['awaiting_lima_payment', 'awaiting_shalom_payment']:
        text_body = "COMPROBANTE_RECIBIDO"
    else:
        return # Ignora otros tipos de mensajes

    logger.info(f"Procesando de {user_name} ({from_number}): '{text_body}'")

    if any(palabra in text_body.lower() for palabra in PALABRAS_CANCELACION):
        if session:
            delete_session(from_number)
            send_text_message(from_number, "Hecho. He cancelado el proceso. Si necesitas algo más, escríbeme. 😊")
        return

    if not session:
        handle_initial_message(from_number, user_name, text_body)
        return

    if 'last_updated' in session:
        last_update_time = session['last_updated']
        if last_update_time.tzinfo is None: last_update_time = last_update_time.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - last_update_time > timedelta(hours=2):
            delete_session(from_number)
            send_text_message(from_number, "Hola de nuevo. 😊 Parece que ha pasado un tiempo. Si necesitas algo, no dudes en preguntar.")
            handle_initial_message(from_number, user_name, text_body)
            return

    current_state = session.get('state')
    handler_func = STATE_HANDLERS.get(current_state)

    if handler_func:
        product_data = None
        if current_state not in ["awaiting_menu_choice", "awaiting_product_choice", "awaiting_faq_choice"]:
            if product_id := session.get('product_id'):
                product_doc = db.collection('productos').document(product_id).get()
                if product_doc.exists:
                    product_data = product_doc.to_dict()
                else:
                    send_text_message(from_number, "Lo siento, este producto ya no está disponible.")
                    delete_session(from_number); return
            else:
                send_text_message(from_number, "Hubo un problema con tu sesión. Empieza de nuevo.")
                delete_session(from_number); return
        handler_func(from_number, text_body, session, product_data)
    else:
        logger.warning(f"No se encontró manejador para el estado: {current_state}")
        send_text_message(from_number, "Estoy un poco confundido. Si deseas reiniciar, escribe 'cancelar'.")

# ==============================================================================
# 9. ENDPOINTS PARA AUTOMATIZACIONES (MAKE.COM)
# ==============================================================================
@app.route('/api/send-tracking', methods=['POST'])
def send_tracking_code():
    if (auth_header := request.headers.get('Authorization')) is None or auth_header != f'Bearer {MAKE_SECRET_TOKEN}':
        logger.warning("Acceso no autorizado a /api/send-tracking")
        return jsonify({'error': 'No autorizado'}), 401
    
    data = request.get_json()
    to_number, nro_orden, codigo_recojo = data.get('to_number'), data.get('nro_orden'), data.get('codigo_recojo')
    
    if not to_number or not nro_orden:
        logger.error("Faltan parámetros en la solicitud de Make.com")
        return jsonify({'error': 'Faltan parámetros'}), 400
    
    try:
        customer_name = "cliente"
        if db and (customer_doc := db.collection('clientes').document(str(to_number)).get()).exists:
            customer_name = customer_doc.to_dict().get('nombre_perfil_wa', 'cliente')

        message_1 = (f"¡Hola {customer_name}! 👋🏽✨\n\n¡Excelentes noticias! Tu pedido de Daaqui Joyas ha sido enviado. 🚚\n\n"
                     f"Datos para seguimiento Shalom:\n👉🏽 *Nro. de Orden:* {nro_orden}" +
                     (f"\n👉🏽 *Código de Recojo:* {codigo_recojo}" if codigo_recojo else "") +
                     "\n\nA continuación, los pasos a seguir:")
        send_text_message(str(to_number), message_1)
        time.sleep(2)
        message_2 = ("*Pasos para una entrega exitosa:* 👇\n\n"
                     "*1. HAZ EL SEGUIMIENTO:* 📲\nDescarga la app *\"Mi Shalom\"*. Si eres nuevo, regístrate. Con los datos de arriba, podrás ver el estado de tu paquete.\n\n"
                     "*2. PAGA EL SALDO CUANDO LLEGUE:* 💳\nCuando la app confirme que tu pedido llegó a la agencia, yapea o plinea el saldo restante. Haz este paso *antes de ir a la agencia*.\n\n"
                     "*3. AVISA Y RECIBE TU CLAVE:* 🔑\nApenas nos envíes la captura de tu pago, lo validaremos y te daremos la *clave secreta de recojo*. ¡La necesitarás junto a tu DNI! 🎁")
        send_text_message(str(to_number), message_2)
        time.sleep(2)
        message_3 = ("✨ *¡Ya casi es tuya! Tu último paso es el más importante.* ✨\n\n"
                     "Para darte atención prioritaria, responde este chat con la **captura de tu pago**.\n\n"
                     "¡Estaremos atentos para enviarte tu clave al instante! La necesitarás junto a tu DNI para recibir tu joya. 🎁")
        send_text_message(str(to_number), message_3)

        return jsonify({'status': 'mensajes enviados'}), 200
    except Exception as e:
        logger.error(f"Error crítico en send_tracking_code: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500
