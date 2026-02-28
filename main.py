# main.py
import logging
import os
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import requests
import json
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
APIRONE_API_KEY = os.getenv("APIRONE_API_KEY")
APIRONE_INVOICE_URL = "https://api.apirone.com/v2/invoice"

# --- CONFIGURARE PRODUSE ---
PRODUCTS = {
    "cacao": {
        "name": "Cacao 1g", 
        "price_ron": 170
    },
    "coffea": {
        "name": "Coffea 1g", 
        "price_ron": 100
}
CITIES = ["Chisinau", "București"]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    with sqlite3.connect("data/users.db") as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                city TEXT,
                product TEXT,
                price REAL,
                invoice_id TEXT,
                address TEXT,
                payment_url TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT
            )
        """)

def save_user_data(user_id, data):
    with sqlite3.connect("data/users.db") as conn:
        conn.execute("""
            INSERT OR REPLACE INTO users 
            (user_id, city, product, price, invoice_id, address, payment_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data.get("city"),
            data.get("product"),
            data.get("price"),
            data.get("invoice_id"),
            data.get("address"),
            data.get("payment_url"),
            data.get("status", "pending"),
            data.get("created_at", datetime.now().isoformat())
        ))

def get_user_data(user_id):
    with sqlite3.connect("data/users.db") as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(city, callback_data=f"city_{city.lower()}")] for city in CITIES]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋 Bun venit la magazinul nostru!\n\n📍 Alege orașul:", reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    user_session = get_user_data(user_id)

    if data.startswith("city_"):
        city = data.split("_")[1].capitalize()
        user_session["city"] = city
        save_user_data(user_id, user_session)
        keyboard = [
            [InlineKeyboardButton(f"{p['name']} – {p['price']} Lei", callback_data=f"prod_{k}")]
            for k, p in PRODUCTS.items()
        ]
        keyboard.append([InlineKeyboardButton("⬅️ Înapoi", callback_data="back_start")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("🛒 Alege produsul dorit:", reply_markup=reply_markup)

    elif data.startswith("prod_"):
        prod_key = data.split("_")[1]
        product = PRODUCTS[prod_key]
        user_session.update({
            "product": product["name"],
            "price": product["price"]
        })
        save_user_data(user_id, user_session)
        text = f"""
✅ Ai selectat:
📦 Produs: {product['name']}
📍 Oraș: {user_session['city']}
💰 Preț: {product['price']} Lei

👉 Alege metoda de plată:
        """
        keyboard = [[InlineKeyboardButton("💳 Plătește cu Apirone", callback_data="pay_apirone")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text.strip(), reply_markup=reply_markup)

    elif data == "pay_apirone":
        price = user_session["price"]
        payload = {
            "currency": "mdl",
            "amount": price,
            "description": f"Comandă {user_session['product']} - {user_session['city']}",
            "callback_url": os.getenv("APRIONE_CALLBACK_URL")
        }
        headers = {
            "Authorization": f"Bearer {APIRONE_API_KEY}",
            "Content-Type": "application/json"
        }
        try:
            response = requests.post(APIRONE_INVOICE_URL, data=json.dumps(payload), headers=headers)
            if response.status_code == 201:
                invoice = response.json()
                user_session.update({
                    "invoice_id": invoice["invoice_id"],
                    "address": invoice["address"],
                    "payment_url": invoice["url"],
                    "status": "unpaid"
                })
                save_user_data(user_id, user_session)
                text = f"""
💳 Factură generată!

🔗 [Plătește acum]({invoice['url']})

🪙 Adresă: `{invoice['address']}`
⏳ Valabil 30 minute

🔁 Verifică statusul plății mai târziu.
                """
                keyboard = [
                    [InlineKeyboardButton("🔄 Verifică Statusul", callback_data="check_status")],
                    [InlineKeyboardButton("❌ Anulează comanda", callback_data="cancel")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.message.reply_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            else:
                error = response.json().get("error", "Necunoscut")
                await query.message.reply_text(f"❌ Eroare API Apirone: {error}")
        except Exception as e:
            logger.error(e)
            await query.message.reply_text("⚠️ Eroare la conectarea cu Apirone.")

    elif data == "check_status":
        invoice_id = user_session.get("invoice_id")
        if not invoice_id:
            await query.message.reply_text("❌ Nu există o factură activă.")
            return
        resp = requests.get(f"{APIRONE_INVOICE_URL}/{invoice_id}", headers={"Authorization": f"Bearer {APIRONE_API_KEY}"})
        if resp.status_code == 200:
            status_data = resp.json()
            status = status_data["status"]
            if status == "paid":
                user_session["status"] = "paid"
                save_user_data(user_id, user_session)
                await query.message.reply_text("✅ Plata confirmată! Comanda ta este procesată. Operatorul te va contacta pentru livrare.")
            elif status == "expired":
                user_session["status"] = "expired"
                save_user_data(user_id, user_session)
                await query.message.reply_text("❌ Factura a expirat. Te rugăm să reîncepi comanda cu /start.")
            else:
                await query.message.reply_text("⏳ Plata nu a fost încă detectată. Mai așteaptă sau verifică din nou.")
        else:
            await query.message.reply_text("⚠️ Nu se poate verifica statusul acum. Încearcă mai târziu.")

    elif data == "cancel":
        user_session["status"] = "canceled"
        save_user_data(user_id, user_session)
        await query.edit_message_text("🗑️ Comanda a fost anulată. Poți începe una nouă cu /start.")

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("🚀 Botul pornește... Așteaptă comenzile.")
    app.run_polling()
