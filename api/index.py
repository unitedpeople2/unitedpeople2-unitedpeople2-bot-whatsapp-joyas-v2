# -*- coding: utf-8 -*-
# ==========================================================
# BOT DAAQUI JOYAS - VERSIÓN CORREGIDA Y COMPLETA
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
        rules_doc = db.collection('configuracion').document('reglas_envio').get()
        if rules_doc.exists: BUSINESS_RULES = rules_doc.to_dict(); logger.info("✅ Reglas del negocio cargadas.")
        faq_doc = db.collection('configuracion').document('respuestas_faq').get()
        if faq_doc.exists: FAQ_RESPONSES = faq_doc.to_dict(); logger.info("✅ Respuestas FAQ cargadas.")
        business_doc = db.collection('configuracion').document('datos_negocio').get()
        if business_doc.exists: BUSINESS_DATA = business_doc.to_dict(); logger.info("✅ Datos del negocio cargados.")
        config_doc = db.collection('configuracion').document('configuracion_general').get()
        if config_doc.exists:
            config_data = config_doc.to_dict()
            PALABRAS_CANCELACION = config_data.get('palabras_cancelacion', ['cancelar'])
            FAQ_KEYWORD_MAP = config_data.get('faq_keyword_map', {})
            logger.info("✅ Configuración general cargada.")

        # --- CÓDIGO DEL MENÚ EN SU LUGAR CORRECTO ---
        menu_doc = db.collection('configuracion').document('menu_principal').get()
        if menu_doc.exists:
            MENU_PRINCIPAL = menu_doc.to_dict()
            logger.info("✅ Menú principal cargado.")
        else:
            MENU_PRINCIPAL = {}
            logger.warning("⚠️ Documento 'menu_principal' no encontrado.")
        # ------------------------------------

	# --- AÑADIR ESTO PARA CARGAR EL CATÁLOGO ---
        catalogo_doc = db.collection('configuracion').document('catalogo_productos').get()
        if catalogo_doc.exists:
            CATALOGO_PRODUCTOS = catalogo_doc.to_dict()
            logger.info("✅ Catálogo de productos cargado.")
        else:
            CATALOGO_PRODUCTOS = {}
            logger.warning("⚠️ Documento 'catalogo_productos' no encontrado.")

	# --- AÑADIR ESTO PARA CARGAR EL MENÚ DE FAQS ---
        menu_faq_doc = db.collection('configuracion').document('menu_faq').get()
        if menu_faq_doc.exists:
            MENU_FAQ = menu_faq_doc.to_dict()
            logger.info("✅ Menú de FAQs cargado.")
        else:
            MENU_FAQ = {}
            logger.warning("⚠️ Documento 'menu_faq' no encontrado.")

        # --- AÑADIDO PARA GOOGLE SHEETS ---
        creds_json_str = os.environ.get('GOOGLE_CREDENTIALS_JSON')
        sheet_name = os.environ.get('GOOGLE_SHEET_NAME')
        if creds_json_str and sheet_name:
            creds_dict = json.loads(creds_json_str)
            gc = gspread.service_account_from_dict(creds_dict)
            spreadsheet = gc.open(sheet_name)
            worksheet_pedidos = spreadsheet.worksheet("Pedidos")
            logger.info("✅ Conexión con Google Sheets establecida correctamente.")
        else:
            logger.warning("⚠️ Faltan variables de entorno para Google Sheets. Las funciones relacionadas no operarán.")

    else:
        logger.error("❌ La variable de entorno FIREBASE_SERVICE_ACCOUNT_JSON no está configurada.")

except Exception as e:
    logger.error(f"❌ Error crítico durante la inicialización de servicios: {e}")

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

KEYWORDS_GIRASOL = ["girasol", "radiant", "precio", "cambia de color"]

# ==============================================================================
# 3. FUNCIONES DE COMUNICACIÓN CON WHATSAPP
# ==============================================================================
def send_whatsapp_message(to_number, message_data):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("Token de WhatsApp o ID de número de teléfono no configurados.")
        return
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}', 'Content-Type': 'application/json'}
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    data = {"messaging_product": "whatsapp", "to": to_number, **message_data}
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logger.info(f"Mensaje enviado exitosamente a {to_number}.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error enviando mensaje a {to_number}: {e.response.text if e.response else e}")

def send_text_message(to_number, text):
    send_whatsapp_message(to_number, {"type": "text", "text": {"body": text}})

def send_image_message(to_number, image_url):
    send_whatsapp_message(to_number, {"type": "image", "image": {"link": image_url}})

# --- NUEVA FUNCIÓN PARA BOTONES AÑADIDA AQUÍ ---
def send_interactive_message(to_number, body_text, buttons):
    # 'buttons' debe ser una lista de diccionarios, ej: [{'id': '1', 'title': 'Ver Catálogo'}]
    # La API de WhatsApp solo permite un máximo de 3 botones.
    if len(buttons) > 3:
        logger.warning("Se intentó enviar un mensaje con más de 3 botones. Solo se usarán los primeros 3.")
        buttons = buttons[:3]

    button_payload = []
    for button in buttons:
        button_payload.append({
            "type": "reply",
            "reply": {
                "id": button.get('id'),
                "title": button.get('title')
            }
        })

    message_data = {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {
                "text": body_text
            },
            "action": {
                "buttons": button_payload
            }
        }
    }
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

def find_product_by_keywords(text):
    if not db: return None, None
    try:
        # Primero, intenta tratar el texto como un ID de producto directo.
        # Esto permitirá que nuestro nuevo menú de catálogo funcione.
        product_doc = db.collection('productos').document(text).get()
        if product_doc.exists and product_doc.to_dict().get('activo'):
            return text, product_doc.to_dict()

        # Si no es un ID, busca por palabras clave como lo hacía antes (para los anuncios).
        if any(keyword in text.lower() for keyword in KEYWORDS_GIRASOL):
            product_id = "collar-girasol-radiant-01"
            product_doc = db.collection('productos').document(product_id).get()
            if product_doc.exists and product_doc.to_dict().get('activo'):
                return product_id, product_doc.to_dict()
                
    except Exception as e:
        logger.error(f"Error buscando producto por palabras clave o ID: {e}")
    return None, None

def save_completed_sale_and_customer(session_data):
    if not db: return False, None
    try:
        sale_id = str(uuid.uuid4())
        customer_id = session_data.get('whatsapp_id')
        precio_total = session_data.get('product_price', 0)
        adelanto = session_data.get('adelanto', 0)
        saldo_restante = precio_total - adelanto
        sale_data = {
            "fecha": firestore.SERVER_TIMESTAMP,
            "id_venta": sale_id,
            "producto_id": session_data.get('product_id'),
            "producto_nombre": session_data.get('product_name'),
            "precio_venta": precio_total,
            "tipo_envio": session_data.get('tipo_envio'),
            "metodo_pago": session_data.get('metodo_pago'),
            "provincia": session_data.get('provincia'),
            "distrito": session_data.get('distrito'),
            "detalles_cliente": session_data.get('detalles_cliente'),
            "cliente_id": customer_id,
            "estado_pedido": "Adelanto Pagado",
            "adelanto_recibido": adelanto,
            "saldo_restante": saldo_restante
        }
        db.collection('ventas').document(sale_id).set(sale_data)
        logger.info(f"Venta {sale_id} guardada en Firestore.")
        customer_data = {
            "nombre_perfil_wa": session_data.get('user_name'),
            "provincia_ultimo_envio": session_data.get('provincia'),
            "distrito_ultimo_envio": session_data.get('distrito'),
            "detalles_ultimo_envio": session_data.get('detalles_cliente'),
            "total_compras": firestore.Increment(1),
            "fecha_ultima_compra": firestore.SERVER_TIMESTAMP
        }
        db.collection('clientes').document(customer_id).set(customer_data, merge=True)
        logger.info(f"Cliente {customer_id} creado/actualizado.")
        return True, sale_data
    except Exception as e:
        logger.error(f"Error guardando venta y cliente en Firestore: {e}")
        return False, None

# ==============================================================================
# 5. FUNCIONES AUXILIARES DE LÓGICA DE NEGOCIO
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
    for distrito in distritos_cobertura:
        if normalized_input in strip_accents(distrito.lower()):
            return distrito.title(), 'CON_COBERTURA'
    distritos_totales = BUSINESS_RULES.get('distritos_lima_total', [])
    for distrito in distritos_totales:
        if normalized_input in strip_accents(distrito.lower()):
            return distrito.title(), 'SIN_COBERTURA'
    return None, 'NO_ENCONTRADO'

def parse_province_district(text):
    clean_text = re.sub(r'soy de|vivo en|mi ciudad es|el distrito es', '', text, flags=re.IGNORECASE).strip()
    separators = [',', '-', '/']
    for sep in separators:
        if sep in clean_text:
            parts = [part.strip() for part in clean_text.split(sep, 1)]
            return parts[0].title(), parts[1].title()
    return clean_text.title(), clean_text.title()

def get_delivery_day_message():
    weekday = datetime.now().weekday()
    if weekday < 4: 
        return BUSINESS_RULES.get('mensaje_dia_habil', 'mañana')
    else: 
        return BUSINESS_RULES.get('mensaje_fin_de_semana', 'el Lunes')

def guardar_pedido_en_sheet(sale_data):
    if not worksheet_pedidos:
        logger.error("[Sheets] La conexión no está inicializada. No se puede guardar el pedido.")
        return False
    try:
        nueva_fila = [
            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
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

def find_key_in_sheet(cliente_id):
    if not worksheet_pedidos:
        logger.error("[Sheets] La conexión no está inicializada. No se puede buscar la clave.")
        return None
    try:
        cell = worksheet_pedidos.find(cliente_id, in_column=12) 
        if cell:
            clave = worksheet_pedidos.cell(cell.row, 15).value 
            logger.info(f"[Sheets] Clave encontrada para {cliente_id}: {'Sí' if clave else 'No'}")
            return clave
        else:
            logger.warning(f"[Sheets] No se encontró la fila para el cliente {cliente_id}.")
            return None
    except Exception as e:
        logger.error(f"[Sheets] ERROR buscando la clave: {e}")
        return None

def get_last_question(state):
    questions = {
        "awaiting_occasion_response": "Cuéntame, ¿es un tesoro para ti o un regalo para alguien especial?",
        "awaiting_purchase_decision": "¿Te gustaría coordinar tu pedido ahora para asegurar el tuyo? (Sí/No)",
        "awaiting_upsell_decision": "Para continuar, por favor, respóndeme con una de estas dos palabras:\n👉🏽 Escribe *oferta* para ampliar tu pedido.\n👉🏽 Escribe *continuar* para llevar solo un collar.",
        "awaiting_location": "Para empezar a coordinar el envío, por favor, dime: ¿eres de *Lima* o de *provincia*?",
        "awaiting_lima_district": "¡Genial! ✨ Para saber qué tipo de envío te corresponde, por favor, dime: ¿en qué distrito te encuentras? 📍",
        "awaiting_province_district": "¡Entendido! Para continuar, por favor, indícame tu *provincia y distrito*. ✍🏽\n\n📝 *Ej: Arequipa, Arequipa*",
        "awaiting_shalom_agreement": "¿Estás de acuerdo con el adelanto? (Sí/No)",
        "awaiting_lima_payment_agreement": "¿Procedemos? (Sí/No)",
        "awaiting_lima_payment": "Una vez realizado, por favor, envíame la *captura de pantalla* para validar tu pedido.",
        "awaiting_shalom_payment": "Una vez realizado, por favor, envíame la *captura de pantalla* para validar tu pedido.",
        "awaiting_delivery_confirmation_lima": "Por favor, responde a este mensaje con la palabra *CONFIRMO* para asegurar tu entrega."
    }
    return questions.get(state)

# ==============================================================================
# 5.1. FUNCIÓN CENTRALIZADA DE FAQ (NUEVA SECCIÓN)
# ==============================================================================
def check_and_handle_faq(from_number, text, session):
    # Condición para evitar que el bot responda FAQs si está esperando una respuesta específica
    # Excluimos los estados iniciales para permitir las FAQs al inicio de la conversación
    if session and session.get('state') not in [
        None, 
        'awaiting_menu_choice', 
        'awaiting_product_choice',
        'awaiting_faq_choice'
    ]:
        # Si estamos en un estado de compra avanzado, no manejar FAQs
        return False
        
    text_lower = text.lower()
    for key, keywords in FAQ_KEYWORD_MAP.items():
        if any(keyword in text_lower for keyword in keywords):
            response_text = FAQ_RESPONSES.get(key)
            if session:
                if key == 'precio' and session.get('product_name'):
                    response_text = f"¡Claro! El precio de tu pedido (*{session['product_name']}*) es de *S/ {session['product_price']:.2f}*, con envío gratis. 🚚"
                elif key == 'stock' and session.get('product_name'):
                    response_text = f"¡Sí, claro! Aún tenemos unidades del *{session['product_name']}*. ✨ ¿Iniciamos tu pedido?"
            if response_text:
                send_text_message(from_number, response_text)
                if session and (last_question := get_last_question(session.get('state'))):
                    time.sleep(1)
                    send_text_message(from_number, f"¡Espero haber aclarado tu duda! 😊 Continuando...\n\n{last_question}")
                return True
    return False

# ==============================================================================
# 6. LÓGICA DE LA CONVERSACIÓN - ETAPA 1 (EMBUDO DE VENTAS)
# ==============================================================================

def handle_initial_message(from_number, user_name, text):
    product_id, product_data = find_product_by_keywords(text)
    if product_data:
        nombre_producto, desc_corta, precio, url_img = product_data.get('nombre', ''), product_data.get('descripcion_corta', ''), product_data.get('precio_base', 0), product_data.get('imagenes', {}).get('principal')
        if url_img: send_image_message(from_number, url_img); time.sleep(1)
        msg = (f"¡Hola {user_name}! 🌞 El *{nombre_producto}* {desc_corta}\n\n"
               f"Por campaña, llévatelo a *S/ {precio:.2f}* (¡incluye envío gratis a todo el Perú! 🚚).\n\n"
               "Cuéntame, ¿es un tesoro para ti o un regalo para alguien especial?")
        send_text_message(from_number, msg)
        save_session(from_number, {"state": "awaiting_occasion_response", "product_id": product_id, "product_name": nombre_producto, "product_price": float(precio), "user_name": user_name, "whatsapp_id": from_number, "is_upsell": False})
        return
   
    if check_and_handle_faq(from_number, text, session=None):
        return

    if MENU_PRINCIPAL:
        welcome_message = MENU_PRINCIPAL.get('mensaje_bienvenida', '¡Hola! ¿Cómo puedo ayudarte?')
        
        # --- LÓGICA DE BOTONES CON EMOJIS ---
        botones = [
            {'id': '1', 'title': '🛍️ Ver Colección'},
            {'id': '2', 'title': '❓ Preguntas'}
        ]
        send_interactive_message(from_number, welcome_message, botones)
        # ------------------------------------
        
        save_session(from_number, {"state": "awaiting_menu_choice", "user_name": user_name, "whatsapp_id": from_number})
    else:
        send_text_message(from_number, f"¡Hola {user_name}! 👋🏽✨ Bienvenida a *Daaqui Joyas*.")


def handle_menu_choice(from_number, text, session, product_data):
    choice = text.strip()
    
    # El usuario eligió ver el catálogo -> RESPUESTA DE TEXTO
    if choice == '1':
        if CATALOGO_PRODUCTOS:
            mensaje_catalogo = "¡Genial! Estas son nuestras colecciones disponibles. Elige una para ver los detalles:"
            catalogo_texto = "\n".join([f"{key}️⃣ {value.get('nombre', '')}" for key, value in sorted(CATALOGO_PRODUCTOS.items())])
            
            send_text_message(from_number, f"{mensaje_catalogo}\n\n{catalogo_texto}")
            save_session(from_number, {"state": "awaiting_product_choice"})
        else:
            send_text_message(from_number, "Lo siento, no pude cargar el catálogo en este momento.")

    # El usuario eligió ver las FAQs -> RESPUESTA DE TEXTO
    elif choice == '2':
        if MENU_FAQ:
            mensaje_faq = "¡Claro! Aquí tienes nuestras dudas más comunes. Elige una para ver la respuesta:"
            faq_texto = "\n".join([f"{key}️⃣ {value.get('pregunta', '')}" for key, value in sorted(MENU_FAQ.items())])

            send_text_message(from_number, f"{mensaje_faq}\n\n{faq_texto}")
            save_session(from_number, {"state": "awaiting_faq_choice"})
        else:
            send_text_message(from_number, "Lo siento, no pude cargar las preguntas frecuentes.")
    else:
        send_text_message(from_number, "Opción no válida. Por favor, elige una de las opciones.")   


def handle_product_choice(from_number, text, session, product_data):
    choice = text.strip()
    
    # Buscamos la elección del cliente en nuestro catálogo cargado
    product_info = CATALOGO_PRODUCTOS.get(choice)
    
    if product_info and product_info.get('product_id'):
        # Si encontramos el producto, obtenemos su ID y reiniciamos el flujo de venta
        # para ese producto específico.
        product_id = product_info.get('product_id')
        user_name = session.get('user_name', 'Usuario')

        # Usamos el product_id como "palabra clave" para que handle_initial_message lo encuentre
        handle_initial_message(from_number, user_name, product_id)
    else:
        send_text_message(from_number, "Opción no válida. Por favor, elige un número del catálogo.")

def handle_faq_choice(from_number, text, session, product_data):
    choice = text.strip()
    
    # Buscamos la elección del cliente en nuestro menú de FAQs
    faq_info = MENU_FAQ.get(choice)
    
    if faq_info and faq_info.get('clave_respuesta'):
        # Si encontramos la pregunta, obtenemos su clave de respuesta
        clave_respuesta = faq_info.get('clave_respuesta')
        
        # Usamos la clave para buscar la respuesta completa en FAQ_RESPONSES
        respuesta_final = FAQ_RESPONSES.get(clave_respuesta, "Lo siento, no encontré una respuesta para esa pregunta.")
        
        send_text_message(from_number, respuesta_final)
        
        # Borramos la sesión para que el usuario pueda hacer otra cosa
        delete_session(from_number)
    else:
        send_text_message(from_number, "Opción no válida. Por favor, elige un número del menú de preguntas.")

# ==============================================================================
# 7. LÓGICA DE LA CONVERSACIÓN - ETAPA 2 (FLUJO DE COMPRA CON BOTONES)
# ==============================================================================
def handle_occasion_response(from_number, text, session, product_data):
    url_imagen_empaque = product_data.get('imagenes', {}).get('empaque')
    detalles = product_data.get('detalles', {})
    material = detalles.get('material', 'material de alta calidad')
    presentacion = detalles.get('empaque', 'viene en una hermosa caja de regalo')
    
    if url_imagen_empaque:
        send_image_message(from_number, url_imagen_empaque)
        time.sleep(1)
        
    mensaje_persuasion_1 = (f"¡Maravillosa elección! ✨ El *Collar Mágico Girasol Radiant* es pura energía. Aquí tienes todos los detalles:\n\n"
                            f"💎 *Material:* {material} ¡Hipoalgénico y no se oscurece!\n"
                            f"🔮 *La Magia:* Su piedra central es termocromática, cambia de color con tu temperatura.\n"
                            f"🎁 *Presentación:* {presentacion}")
    send_text_message(from_number, mensaje_persuasion_1)
    time.sleep(1.5)
    
    mensaje_persuasion_2 = (f"Para tu total seguridad, somos Daaqui Joyas, un negocio formal con *RUC {RUC_EMPRESA}*. ¡Tu compra es 100% segura! 🇵🇪\n\n"
                            "¿Te gustaría coordinar tu pedido ahora para asegurar el tuyo?")
    
    # --- CORRECCIÓN DE TÍTULO DE BOTÓN ---
    botones = [
        {'id': 'si_coordinar', 'title': '✅ Sí, coordinar'},
        {'id': 'no_gracias', 'title': 'No, gracias'}
    ]
    send_interactive_message(from_number, mensaje_persuasion_2, botones)
    
    session['state'] = 'awaiting_purchase_decision'
    save_session(from_number, session)

def handle_purchase_decision(from_number, text, session, product_data):
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
        
        # --- PREGUNTA PERSUASIVA MEJORADA ---
        mensaje_decision = "Para continuar con tu pedido, ¿cuál será tu elección?"
        botones = [
            {'id': 'oferta', 'title': '🔥 Quiero la oferta'},
            {'id': 'continuar', 'title': 'Continuar con uno'}
        ]
        send_interactive_message(from_number, mensaje_decision, botones)
        
        session['state'] = 'awaiting_upsell_decision'
        save_session(from_number, session)
    else:
        delete_session(from_number)
        send_text_message(from_number, "Entendido. Si cambias de opinión, aquí estaré. ¡Que tengas un buen día! 😊")

def handle_upsell_decision(from_number, text, session, product_data):
    if text == 'oferta':
        session.update({"product_name": "Oferta 2x Collares Mágicos + Cadenas", "product_price": 99.00, "is_upsell": True})
        send_text_message(from_number, "¡Genial! Has elegido la oferta. ✨")
    else:
        session['is_upsell'] = False
        send_text_message(from_number, "¡Perfecto! Continuamos con tu collar individual. ✨")
    
    time.sleep(1)
    
    mensaje = "¡Perfecto! Tu joya está casi en camino. Para coordinar tu envío gratis, indícame si el envío es para:"
    botones = [
        {'id': 'lima', 'title': '📍 Lima'},
        {'id': 'provincia', 'title': '🚚 Provincia'}
    ]
    send_interactive_message(from_number, mensaje, botones)

    session['state'] = 'awaiting_location'
    save_session(from_number, session)

def handle_location(from_number, text, session, product_data):
    if text == 'lima':
        session.update({"state": "awaiting_lima_district", "provincia": "Lima"})
        save_session(from_number, session)
        send_text_message(from_number, "¡Genial! ✨ Para saber qué tipo de envío te corresponde, por favor, dime: ¿en qué distrito te encuentras? 📍")
    elif text == 'provincia':
        session['state'] = 'awaiting_province_district'
        save_session(from_number, session)
        send_text_message(from_number, "¡Entendido! Para continuar, indícame tu *provincia y distrito*. ✍🏽\n\n📝 *Ej: Arequipa, Arequipa*")
    else:
        mensaje = "Por favor, elige una de las dos opciones:"
        botones = [{'id': 'lima', 'title': '📍 Lima'}, {'id': 'provincia', 'title': '🚚 Provincia'}]
        send_interactive_message(from_number, mensaje, botones)

def handle_province_district(from_number, text, session, product_data):
    provincia, distrito = parse_province_district(text)
    session.update({"tipo_envio": "Provincia Shalom", "metodo_pago": "Adelanto y Saldo (Yape/Plin)", "provincia": provincia, "distrito": distrito})
    adelanto = BUSINESS_RULES.get('adelanto_shalom', 20)
    
    # --- MENSAJE CORREGIDO PARA MOSTRAR SOLO PROVINCIA EN NEGRITA ---
    mensaje = (f"¡Genial! Prepararemos tu envío para *{provincia}* vía Shalom. "
               f"Nuestros despachos a provincia se están agendando rápidamente ⚠️. "
               f"Para asegurar y priorizar tu paquete en la próxima salida, solicitamos un adelanto de S/ {adelanto:.2f} como compromiso de recojo.\n\n"
               "¿Procedemos?")
    
    botones = [
        {'id': 'si_acuerdo', 'title': '✅ Sí, de acuerdo'},
        {'id': 'no_acuerdo', 'title': 'No en este momento'}
    ]
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
                       "Para registrar tu pedido, envíame en *un solo mensaje* tu *Nombre Completo, Dirección exacta* y una *Referencia*.\n\n"
                       "📝 *Ej: Ana Pérez, Jr. Gamarra 123, Depto 501, La Victoria. Al lado de la farmacia.*")
            send_text_message(from_number, mensaje)
        elif status == 'SIN_COBERTURA':
            session.update({"tipo_envio": "Lima Shalom", "metodo_pago": "Adelanto y Saldo (Yape/Plin)"})
            adelanto = BUSINESS_RULES.get('adelanto_shalom', 20)
            
            # --- MENSAJE PERSUASIVO MEJORADO ---
            mensaje = (f"¡Genial! Prepararemos tu envío para *{distrito}* vía *Shalom*. "
                       f"Nuestros despachos se están agendando rápidamente ⚠️. "
                       f"Para asegurar y priorizar tu paquete en la próxima salida, solicitamos un adelanto de *S/ {adelanto:.2f}* como compromiso de recojo.\n\n"
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
    
    botones = [
        {'id': 'si_correcto', 'title': '✅ Sí, todo correcto'},
        {'id': 'corregir', 'title': '📝 Corregir datos'}
    ]
    send_interactive_message(from_number, resumen, botones)
    session['state'] = 'awaiting_final_confirmation'
    save_session(from_number, session)

def handle_shalom_agreement(from_number, text, session, product_data):
    if text == 'si_acuerdo':
        session['state'] = 'awaiting_shalom_experience'
        save_session(from_number, session)
        mensaje = "¡Genial! Para hacer el proceso más fácil, cuéntame: ¿alguna vez has recogido un pedido en una agencia Shalom? 🙋🏽‍♀️"
        
        botones = [
            {'id': 'si_conozco', 'title': '✅ Sí, ya conozco'},
            {'id': 'no_conozco', 'title': 'No, explícame más'}
        ]
        send_interactive_message(from_number, mensaje, botones)
    else:
        delete_session(from_number)
        send_text_message(from_number, "Comprendo. Si cambias de opinión, aquí estaré. ¡Gracias! 😊")

def handle_shalom_experience(from_number, text, session, product_data):
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
    if text == 'shalom_knows_addr_yes':
        session['state'] = 'awaiting_shalom_details'
        save_session(from_number, session)
        mensaje = ("¡Perfecto! Por favor, bríndame en un solo mensaje tu *Nombre Completo, DNI* y la *dirección de esa agencia Shalom*. ✍🏽\n\n"
                   "📝 *Ej: Carlos Ruiz, 87654321, Jr. Gamarra 456, Trujillo.*")
        send_text_message(from_number, mensaje)
    else: # 'shalom_knows_addr_no'
        delete_session(from_number)
        send_text_message(from_number, "Entiendo. 😔 Te recomiendo buscar en Google 'Shalom agencias' para encontrar la más cercana. Cuando la tengas, puedes iniciar la conversación de nuevo. ¡Gracias por tu interés!")

def handle_final_confirmation(from_number, text, session, product_data):
    if text == 'si_correcto':
        if session.get('tipo_envio') == 'Lima Contra Entrega':
            adelanto = float(BUSINESS_RULES.get('adelanto_lima_delivery', 10))
            session.update({'adelanto': adelanto})
            mensaje = (
                "¡Perfecto! Tu pedido contra entrega está listo para ser agendado. ✨\n\n"
                "Nuestras rutas de reparto para mañana 🚚 ya se están llenando y tenemos *cupos limitados* ⚠️. Para asegurar tu espacio y priorizar tu entrega, solo solicitamos un adelanto de *S/ 10.00*.\n\n"
                "Este pequeño monto confirma tu compromiso y nos permite seguir ofreciendo *envío gratis* a clientes serios como tú. Por supuesto, se descuenta del total.\n\n"
                "👉 ¿Procedemos para reservar tu lugar?"
            )
            
            botones = [{'id': 'si_proceder', 'title': 'Sí, reservar ahora'}, {'id': 'no_proceder', 'title': 'No, gracias'}]
            send_interactive_message(from_number, mensaje, botones)
            session['state'] = 'awaiting_lima_payment_agreement'
            save_session(from_number, session)
        else: # Shalom
            adelanto = float(BUSINESS_RULES.get('adelanto_shalom', 20))
            session.update({'adelanto': adelanto, 'state': 'awaiting_shalom_payment'})
            save_session(from_number, session)
            mensaje = (f"¡Genial! Puedes realizar el adelanto de *S/ {adelanto:.2f}* a nuestra cuenta:\n\n"
                       f"💳 *YAPE / PLIN:* {YAPE_NUMERO}\n"
                       f"👤 *Titular:* {TITULAR_YAPE}\n"
                       f"🔒 Tu compra es 100% segura (*RUC {RUC_EMPRESA}*).\n\n"
                       "Una vez realizado, envíame la *captura de pantalla* para validar tu pedido.")
            send_text_message(from_number, mensaje)
    else: # 'corregir'
        previous_state = 'awaiting_delivery_details' if session.get('tipo_envio') == 'Lima Contra Entrega' else 'awaiting_shalom_details'
        session['state'] = previous_state
        save_session(from_number, session)
        send_text_message(from_number, "¡Claro, lo corregimos! 😊 Por favor, envíame nuevamente la información de envío completa en un solo mensaje.")

def handle_lima_payment_agreement(from_number, text, session, product_data):
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
                admin_message = (f"🎉 ¡Nueva Venta Confirmada! 🎉\n\n"
                                 f"Producto: {sale_data.get('producto_nombre')}\n"
                                 f"Tipo: {sale_data.get('tipo_envio')}\n"
                                 f"Cliente WA ID: {sale_data.get('cliente_id')}\n"
                                 f"Detalles:\n{sale_data.get('detalles_cliente')}")
                send_text_message(ADMIN_WHATSAPP_NUMBER, admin_message)
            if session.get('tipo_envio') == 'Lima Contra Entrega':
                dia_entrega = get_delivery_day_message()
                horario = BUSINESS_RULES.get('horario_entrega_lima', 'durante el día')
                mensaje_resumen = (f"¡Adelanto confirmado, gracias! ✨ Aquí tienes el resumen final de tu pedido y los detalles de la entrega:\n\n"
                                   f"*Tu Pedido en Detalle:*\n"
                                   f"💰 Costo Total: S/ {sale_data.get('precio_venta', 0):.2f}\n"
                                   f"✅ Adelanto Recibido: - S/ {sale_data.get('adelanto_recibido', 0):.2f}\n"
                                   f"------------------------------------\n"
                                   f"💵 *Saldo a Pagar al recibir: S/ {sale_data.get('saldo_restante', 0):.2f}*\n\n"
                                   f"*Entrega Programada:*\n"
                                   f"🗓️ Día: {dia_entrega.title()}\n"
                                   f"⏰ Horario: {horario}\n\n"
                                   f"A continuación, te pediré un último paso para asegurar tu envío.")
                send_text_message(from_number, mensaje_resumen)
                time.sleep(1.5)
                mensaje_solicitud = (f"¡Ya casi es tuya! 💎\n\n"
                                     f"Para garantizar una entrega exitosa *{dia_entrega}*, por favor confirma que habrá alguien disponible para recibir tu joya y pagar el saldo 💵.")
                
                botones = [{'id': 'confirmo_entrega_lima', 'title': '✅ CONFIRMO'}]
                send_interactive_message(from_number, mensaje_solicitud, botones)

                session['state'] = 'awaiting_delivery_confirmation_lima'
                save_session(from_number, session)

            else: # Shalom
                resumen_shalom = (f"¡Adelanto confirmado, gracias! ✨ Aquí tienes el resumen final de tu pedido:\n\n"
                                  f"*Tu Pedido en Detalle:*\n"
                                  f"💰 Costo Total: S/ {sale_data.get('precio_venta', 0):.2f}\n"
                                  f"✅ Adelanto Recibido: - S/ {sale_data.get('adelanto_recibido', 0):.2f}\n"
                                  f"------------------------------------\n"
                                  f"💵 *Saldo a Pagar: S/ {sale_data.get('saldo_restante', 0):.2f}*")
                send_text_message(from_number, resumen_shalom)
                time.sleep(1.5)
                tiempo_entrega = "1-2 días hábiles" if session.get('tipo_envio') == 'Lima Shalom' else "3-5 días hábiles"
                proximos_pasos = (f"📝 *Próximos Pasos:*\n\n"
                                  f"⏳ En las próximas 24h hábiles te enviaremos tu código de seguimiento 📲. El tiempo de entrega en agencia es de *{tiempo_entrega}* 📦.")
                send_text_message(from_number, proximos_pasos)
                delete_session(from_number)
        else:
            send_text_message(from_number, "¡Uy! Hubo un problema al registrar tu pedido. Un asesor se pondrá en contacto contigo.")
    else:
        send_text_message(from_number, "Estoy esperando la *captura de pantalla* de tu pago. 😊")

def handle_delivery_confirmation_lima(from_number, text, session, product_data):
    if 'confirmo' in text.lower() or text == 'confirmo_entrega_lima':
        mensaje_final = ("¡Listo! ✅ Tu pedido ha sido *confirmado en la ruta* 🚚.\n\n"
                         "De parte de todo el equipo de *Daaqui Joyas*, ¡muchas gracias por tu compra! 🎉😊")
        send_text_message(from_number, mensaje_final)
        delete_session(from_number)
    else:
        # Enviamos de nuevo el mensaje con el botón por si responde otra cosa
        mensaje_solicitud = ("Por favor, para asegurar tu pedido, presiona el botón de confirmación.")
        botones = [{'id': 'confirmo_entrega_lima', 'title': '✅ CONFIRMO'}]
        send_interactive_message(from_number, mensaje_solicitud, botones)

# ------------------------------------------------------------------------------
# 7.2. Diccionario de Despacho y Función Principal del Flujo
# ------------------------------------------------------------------------------

STATE_HANDLERS = {
    "awaiting_occasion_response": handle_occasion_response,
    "awaiting_purchase_decision": handle_purchase_decision,
    "awaiting_upsell_decision": handle_upsell_decision,
    "awaiting_location": handle_location,
    "awaiting_province_district": handle_province_district,
    "awaiting_lima_district": handle_lima_district,
    # Nota: Ambos estados usan la misma función porque la lógica es idéntica
    "awaiting_delivery_details": handle_customer_details,
    "awaiting_shalom_details": handle_customer_details,
    "awaiting_shalom_agreement": handle_shalom_agreement,
    "awaiting_shalom_experience": handle_shalom_experience,
    "awaiting_shalom_agency_knowledge": handle_shalom_agency_knowledge,
    "awaiting_final_confirmation": handle_final_confirmation,
    "awaiting_lima_payment_agreement": handle_lima_payment_agreement,
    # Nota: Ambos estados de pago también usan la misma función
    "awaiting_lima_payment": handle_payment_received,
    "awaiting_shalom_payment": handle_payment_received,
    "awaiting_delivery_confirmation_lima": handle_delivery_confirmation_lima,
    "awaiting_menu_choice": handle_menu_choice,
    "awaiting_product_choice": handle_product_choice,
    "awaiting_faq_choice": handle_faq_choice,
}

def handle_sales_flow(from_number, text, session):
    # 1. Obtenemos el estado actual y su función (manejador) correspondiente
    current_state = session.get('state')
    handler_func = STATE_HANDLERS.get(current_state)

    # 2. Lógica especial para los estados de menú que no necesitan un producto
    if current_state in ["awaiting_menu_choice", "awaiting_product_choice", "awaiting_faq_choice"]:
        if handler_func:
            handler_func(from_number, text, session, None)
        return

    # --- ESTE BLOQUE ES CRUCIAL ---
    # 3. Obtenemos los datos del producto ANTES de cualquier otra lógica
    product_id = session.get('product_id')
    if not product_id:
        send_text_message(from_number, "Hubo un problema con tu sesión. Por favor, empieza de nuevo escribiendo 'cancelar'.")
        return
    try:
        product_doc = db.collection('productos').document(product_id).get()
        if not product_doc.exists:
            send_text_message(from_number, "Lo siento, este producto ya no está disponible. Por favor, empieza de nuevo.")
            delete_session(from_number)
            return
        # La variable 'product_data' se define aquí
        product_data = product_doc.to_dict()
    except Exception as e:
        logger.error(f"Error al obtener producto {product_id}: {e}")
        send_text_message(from_number, "Tuvimos un problema al consultar el producto. Inténtalo de nuevo.")
        return
    # --- FIN DEL BLOQUE CRUCIAL ---

    # 4. LÓGICA PRINCIPAL INVERTIDA
    if handler_func:
        # Primero, se intenta ejecutar la lógica del estado actual, pasando el 'product_data' que acabamos de obtener
        handler_func(from_number, text, session, product_data)
        
        new_session = get_session(from_number)
        
        # Si la sesión existe y el estado NO cambió, la respuesta del usuario no fue la esperada
        if new_session and new_session.get('state') == current_state:
            
            # Ahora sí, revisamos si es una FAQ
            if not check_and_handle_faq(from_number, text, new_session):
            
                # Si tampoco es una FAQ, le recordamos al usuario la pregunta anterior
                last_question = get_last_question(current_state)
                if last_question:
                    send_text_message(from_number, f"No entendí muy bien tu respuesta. Para continuar, por favor respóndeme a esto:\n\n{last_question}")
                else:
                    send_text_message(from_number, "Estoy un poco confundido. Si deseas reiniciar, escribe 'cancelar'.")
    else:
        # Si no se encontró un manejador para el estado, se notifica y se intenta la FAQ como último recurso
        logger.warning(f"No se encontró un manejador para el estado: {current_state} del usuario {from_number}")
        if not check_and_handle_faq(from_number, text, session):
            send_text_message(from_number, "Estoy un poco confundido. Si deseas reiniciar, escribe 'cancelar'.")

# ==============================================================================
# 8. WEBHOOK PRINCIPAL Y PROCESADOR DE MENSAJES
# ==============================================================================
@app.route('/api/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return 'Forbidden', 403
    elif request.method == 'POST':
        try:
            data = request.get_json()
            if data.get('object') == 'whatsapp_business_account':
                for entry in data.get('entry', []):
                    for change in entry.get('changes', []):
                        if change.get('field') == 'messages' and (value := change.get('value', {})):
                            if messages := value.get('messages'):
                                for message in messages:
                                    process_message(message, value.get('contacts', []))
            return jsonify({'status': 'success'}), 200
        except Exception as e:
            logger.error(f"Error procesando webhook: {e}"); return jsonify({'error': str(e)}), 500

def process_message(message, contacts):
    try:
        from_number = message.get('from')
        user_name = next((c.get('profile', {}).get('name', 'Usuario') for c in contacts if c.get('wa_id') == from_number), 'Usuario')
        session = get_session(from_number)
        
        message_type = message.get('type')
        text_body = ""
        if message_type == 'text':
            text_body = message.get('text', {}).get('body', '')
        elif message_type == 'interactive' and message.get('interactive', {}).get('type') == 'button_reply':
            text_body = message.get('interactive', {}).get('button_reply', {}).get('id', '')
        elif message_type == 'image':
            if session and session.get('state') in ['awaiting_lima_payment', 'awaiting_shalom_payment']:
                text_body = "COMPROBANTE_RECIBIDO"
            else:
                text_body = "_Imagen Recibida_"
        else:
            send_text_message(from_number, "Por ahora solo puedo procesar mensajes de texto, botones e imágenes. 😊")
            return

        logger.info(f"Procesando de {user_name} ({from_number}): '{text_body}'")

        # --- NUEVA LÓGICA: REVISAR CANCELACIONES Y FAQS PRIMERO ---
        if any(palabra in text_body.lower() for palabra in PALABRAS_CANCELACION):
            if session:
                delete_session(from_number)
                send_text_message(from_number, "Hecho. He cancelado el proceso. Si necesitas algo más, escríbeme. 😊")
            return

        if check_and_handle_faq(from_number, text_body, session):
            # Si se maneja una FAQ, detenemos el procesamiento aquí.
            return

        # --- FIN DE LA NUEVA LÓGICA ---

        # Si no hay sesión, iniciar el flujo de mensaje inicial
        if not session:
            handle_initial_message(from_number, user_name, text_body)
            return

        # Manejar la expiración de la sesión
        if 'last_updated' in session:
            last_update_time = session['last_updated']
            if last_update_time.tzinfo is None:
                last_update_time = last_update_time.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_update_time > timedelta(hours=2):
                logger.info(f"Sesión expirada por inactividad para {from_number}. Eliminando.")
                delete_session(from_number)
                send_text_message(from_number, "Hola de nuevo. 😊 Parece que ha pasado un tiempo. Si necesitas algo, no dudes en preguntar.")
                handle_initial_message(from_number, user_name, text_body)
                return

        # Si existe una sesión, manejar el flujo de ventas
        handle_sales_flow(from_number, text_body, session)

    except Exception as e:
        logger.error(f"Error fatal en process_message: {e}")

# ==============================================================================
# 9. ENDPOINT PARA AUTOMATIZACIONES (MAKE.COM)
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

# ==============================================================================
# 10. ENDPOINT PARA NOTIFICACIONES INTERNAS (EJ: ALERTA DE STOCK)
# ==============================================================================
@app.route('/api/notify-admin', methods=['POST'])
def notify_admin():
    auth_header = request.headers.get('Authorization')
    if not auth_header or auth_header != f'Bearer {MAKE_SECRET_TOKEN}':
        logger.warning("Intento de acceso no autorizado a /api/notify-admin")
        return jsonify({'error': 'No autorizado'}), 401
    
    data = request.get_json()
    message_to_admin = data.get('message')

    if not message_to_admin:
        logger.error("No se recibió 'message' en la solicitud de Make.com a notify-admin")
        return jsonify({'error': 'Falta el parámetro message'}), 400
    
    try:
        if ADMIN_WHATSAPP_NUMBER:
            send_text_message(ADMIN_WHATSAPP_NUMBER, message_to_admin)
            logger.info(f"Notificación de administrador enviada exitosamente.")
            return jsonify({'status': 'notificacion enviada'}), 200
        else:
            logger.error("ADMIN_WHATSAPP_NUMBER no está configurado. No se puede enviar la alerta.")
            return jsonify({'error': 'Admin no configurado'}), 500
    except Exception as e:
        logger.error(f"Error crítico en notify_admin: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500