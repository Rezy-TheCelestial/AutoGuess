# bot.py  (Telethon-only rewrite)
import os
import sys
import re
import json
import time
import asyncio
import logging
from random import randint
from pathlib import Path

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PhoneCodeExpiredError
from telethon.tl.types import PhotoStrippedSize
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError, FloodWaitError

from pymongo import MongoClient
import certifi

# -------------------------
# Configuration - EDIT THESE
# -------------------------
API_ID = 21453458
API_HASH = '565cac9ed11ff64ca7e2626f7b1b18b2'
BOT_TOKEN = '7911063793:AAHu4w4B62wlLRLbA9mG9_GgDIIcfJQtlgE'  # Bot token from BotFather
ADMIN_USER_ID = 5621201759
LOG_CHANNEL_ID = -1002203950734
CATCH_CHAT_ID = "@Hexamonbot"
CATCH_LIST = ["✨"]

MONGO_URI = "mongodb+srv://Celestial_Guard:Rrahaman%400000@autoguess69.8lwwa1m.mongodb.net/?retryWrites=true&w=majority&appName=AutoGuess69"
DB_NAME = "Ubdb"
USERS_COLL = "Authusers"
ACCOUNTS_COLL = "Accounts"

SESSION_NAME = "hexamon_bot_telethon"
SESSIONS_DIR = "saitama"

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Directories
os.makedirs("cache", exist_ok=True)
os.makedirs("saitama", exist_ok=True)

# -------------------------
# MongoDB
# -------------------------
client = MongoClient(MONGO_URI, tlsCAFile=certifi.where())
db = client[DB_NAME]
users_col = db[USERS_COLL]
accounts_col = db[ACCOUNTS_COLL]
hunt_status = {}  # Global dict to track which phones are actively auto-catching
# -------------------------
# Authorized users (in-memory + DB)
# -------------------------
def load_authorized_users():
    docs = list(users_col.find({}, {"_id": 0, "user_id": 1}))
    if docs:
        return {d["user_id"] for d in docs}
    return set()

def save_authorized_user(uid: int):
    users_col.update_one({"user_id": uid}, {"$set": {"user_id": uid}}, upsert=True)

AUTHORIZED_USERS = load_authorized_users()
if ADMIN_USER_ID not in AUTHORIZED_USERS:
    AUTHORIZED_USERS.add(ADMIN_USER_ID)
    save_authorized_user(ADMIN_USER_ID)

# -------------------------
# Bot client (Telethon)
# -------------------------
bot = TelegramClient(SESSION_NAME, API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Global state
account_clients = {}   # phone -> Telethon user client (connected)
account_tasks = {}     # phone -> asyncio.Task for guessing
auto_catch_tasks = {}  # phone -> asyncio.Task for auto-catch
daily_limits = {}      # phone -> True when limit hit
limit_timers = {}      # phone -> timestamp when limit hit
login_states = {}      # user_id -> state dict for /login flow

# -------------------------
# Helpers
# -------------------------
from telethon import events

@bot.on(events.NewMessage(pattern=r'^\.giveme\s+(\d+)$'))
async def giveme_cmd(event):
    """Admin command to make all accounts send /give {amount}"""
    if event.sender_id != ADMIN_USER_ID:
        return

    try:
        amount = int(event.pattern_match.group(1))
    except (ValueError, IndexError):
        await event.reply("❌ Invalid format. Use: .giveme <amount>")
        return

    account_clients = await get_account_clients()  # same loader used in startall
    if not account_clients:
        await event.reply("❌ No accounts loaded. Start with /startall first.")
        return

    successful_sends, failed_sends = 0, 0
    await event.reply(f"🎯 Sending /give {amount} from {len(account_clients)} accounts...")

    for phone, client in account_clients.items():
        try:
            if not client.is_connected():
                await client.connect()
            if not await client.is_user_authorized():
                await event.reply(f"⚠️ Unauthorized session: {phone}")
                failed_sends += 1
                continue

            await client.send_message(event.chat_id, f"/give {amount}",reply_to=event.message.id)
            successful_sends += 1
            await asyncio.sleep(2)

        except Exception as e:
            failed_sends += 1
            print(f"Failed to send from {phone}: {e}")

    summary = (
        f"✅ Give command completed!\n"
        f"📤 Successful: {successful_sends}\n"
        f"❌ Failed: {failed_sends}\n"
        f"💰 Amount: {amount}"
    )
    await event.reply(summary)

async def log_message(chat_id, msg):
    """Helper function to log messages to console and admin."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{timestamp}] [Chat {chat_id}] {msg}"
    print(log_msg)
    try:
        await bot.send_message(LOG_CHANNEL_ID, log_msg)
    except:
        pass

def is_private_event(event):
    """Return True if message is from a private chat to the bot."""
    return event.is_private

async def send_to_admin(text):
    try:
        await bot.send_message(ADMIN_USER_ID, text)
    except Exception:
        logger.exception("Failed to notify admin")

def safe_text(text):
    """Send plain text without relying on HTML parsing — Telethon send_message default is plain text."""
    return text

# -------------------------
# Authorization decorator
# -------------------------
def authorized_only(func):
    async def wrapper(event):
        uid = event.sender_id
        if uid not in AUTHORIZED_USERS:
            await event.reply("❌ You are not authorized to use this command.")
            return
        return await func(event)
    return wrapper

# -------------------------
# Command Handlers
# -------------------------

async def get_account_clients():
    global account_clients
    # Clear any disconnected clients
    account_clients = {k: v for k, v in account_clients.items() if hasattr(v, 'is_connected') and v.is_connected()}
    
    accounts = list(accounts_col.find({}))  # Get all accounts for client management
    for acc in accounts:
        phone, session_string = acc['phone'], acc['session_string']
        if phone not in account_clients:
            try:
                client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
                await client.connect()
                if not await client.is_user_authorized():
                    await client.start()
                account_clients[phone] = client
            except Exception as e:
                print(f"Failed to start client for {phone}: {str(e)}")
    
    return account_clients

@bot.on(events.NewMessage(pattern=r'^/start$'))
async def start_handler(event):
    if not is_private_event(event):
        return  # ignore groups
    user = await event.get_sender()
    user_id = user.id
    first_name = user.first_name or "Not provided"
    username = f"@{user.username}" if getattr(user, 'username', None) else "Not provided"

    users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "username": user.username or str(user_id)}}, upsert=True)

    info_text = (
        f"User {username} started the bot\n"
        f"Name: {first_name}\n"
        f"Username: {username}\n"
        f"User ID: {user_id}"
    )
    await event.reply(info_text)

    if user_id not in AUTHORIZED_USERS:
        admin_text = (
            f"User {username} started the bot\n"
            f"Name: {first_name}\n"
            f"Username: {username}\n"
            f"User ID: {user_id}\n\n"
            f"Use /auth add {user_id} to authorize the user."
        )
        await send_to_admin(admin_text)
        await event.reply("ℹ️ Your details were sent to the admin for approval.")
    else:
        await event.reply("👋 Welcome! Use /help to see commands.")

@bot.on(events.NewMessage(pattern=r'^/help$'))
@authorized_only
async def help_handler(event):
    # Plain text, avoid <> that look like HTML tags
    msg = (
        "/login - Add a new account (private chat)\n"
        "/accounts - List your accounts\n"
        "/logout <phone> - Log out and remove account\n"
        "/start_guess <phone> - Start guessing for specific account\n"
        "/stop <phone> - Stop guessing for specific account\n"
        "/startall - Choose Auto Guess or Auto Catch for all accounts\n"
        "/stopall - Stop all guessing/catching\n"
        "/status - Show status of your accounts\n"
        "/auth - Admin auth commands (owner only)\n"
    )
    await event.reply(msg)

@bot.on(events.NewMessage(pattern=r'^/auth(?:\s+.*)?$'))
async def auth_handler(event):
    # Only owner can run /auth management
    if event.sender_id != ADMIN_USER_ID:
        await event.reply("❌ Only owner can manage auth.")
        return

    text = event.raw_text.strip()
    parts = text.split()
    if len(parts) < 2:
        await event.reply(
            "Usage:\n"
            "/auth add <user_id>\n"
            "/auth remove <user_id>\n"
            "/auth list"
        )
        return

    cmd = parts[1].lower()
    if cmd == "add" and len(parts) >= 3:
        try:
            uid = int(parts[2])
            if uid in AUTHORIZED_USERS:
                await event.reply(f"User {uid} is already authorized.")
            else:
                AUTHORIZED_USERS.add(uid)
                save_authorized_user(uid)
                await event.reply(f"✅ User {uid} has been authorized.")
        except Exception:
            await event.reply("❌ Invalid user ID format.")
    elif cmd == "remove" and len(parts) >= 3:
        try:
            uid = int(parts[2])
            if uid == ADMIN_USER_ID:
                await event.reply("❌ Cannot remove owner's authorization.")
            elif uid in AUTHORIZED_USERS:
                AUTHORIZED_USERS.remove(uid)
                users_col.delete_one({"user_id": uid})
                await event.reply(f"✅ User {uid} has been removed from authorized users.")
            else:
                await event.reply(f"❌ User {uid} is not in the authorized list.")
        except Exception:
            await event.reply("❌ Invalid user ID format.")
    elif cmd == "list":
        users_list = "\n".join([f"- {uid}" for uid in sorted(AUTHORIZED_USERS)])
        await event.reply(f"🔐 Authorized Users:\n{users_list}")
    else:
        await event.reply("❌ Unknown /auth subcommand.")

# -------------------------
# Login flow (phone -> otp -> password -> group_id)
# -------------------------
@bot.on(events.NewMessage(pattern=r'^/login$'))
@authorized_only
async def login_start(event):
    if not is_private_event(event):
        await event.reply("Please run /login in a private chat with the bot.")
        return
    uid = event.sender_id
    login_states[uid] = {"step": "phone", "retry": 0}
    await event.reply("🔑 Please enter your phone number (with country code, e.g., +1234567890). Send /cancel to abort.")

@bot.on(events.NewMessage(pattern=r'^/cancel$'))
@authorized_only
async def cancel_login(event):
    uid = event.sender_id
    state = login_states.pop(uid, None)
    if state and state.get("tele_client"):
        try:
            await state["tele_client"].disconnect()
        except Exception:
            pass
    await event.reply("❌ Login process cancelled.")

@bot.on(events.NewMessage(func=lambda e: True))
async def login_flow_handler(event):
    # This handler processes free-form messages when user is in login flow.
    # If user isn't in login_states, ignore.
    uid = event.sender_id
    if uid not in login_states:
        return
    if uid != ADMIN_USER_ID:
        return

    state = login_states[uid]
    step = state.get("step")

    try:
        if step == "phone":
            text = (event.raw_text or "").strip()
    
            # Ignore if user just typed /login or another command
            if text.startswith("/"):
                return

            phone = text

            # create a temporary Telethon client for this phone
            tele_client = TelegramClient(StringSession(), API_ID, API_HASH)
            await tele_client.connect()
            try:
                sent = await tele_client.send_code_request(phone)
            except Exception as e:
                await tele_client.disconnect()
                login_states.pop(uid, None)
                await event.reply(f"❌ Error sending code: {e}")
                return

            state.update({"tele_client": tele_client, "phone": phone, "sent": sent, "step": "otp", "retry": 0})
            await event.reply("🔑 OTP sent. Please enter the 5-digit code you received (or send it separated by spaces).")
            return

        if step == "otp":
            otp_raw = event.raw_text.strip()
            otp = re.sub(r'\s+', '', otp_raw)
            if not re.fullmatch(r'\d{4,10}', otp):
                state['retry'] = state.get('retry', 0) + 1
                if state['retry'] >= 3:
                    login_states.pop(uid, None)
                    await event.reply("❌ Too many invalid attempts. Please restart with /login.")
                    if state.get("tele_client"):
                        await state["tele_client"].disconnect()
                    return
                await event.reply("❌ Invalid OTP format. Try again:")
                return

            tele_client = state.get("tele_client")
            sent = state.get("sent")
            if not tele_client or not sent:
                login_states.pop(uid, None)
                await event.reply("❌ Session expired. Please restart with /login.")
                return

            try:
                await tele_client.sign_in(phone=state['phone'], code=otp, phone_code_hash=sent.phone_code_hash)
                # signed in successfully (maybe)
                state['step'] = 'group_id'
                await event.reply(
                    "✅ Verification successful!\n"
                    "Now, please provide the numeric group ID where you want to use this account.\n"
                    "You can get the group ID by adding @username_to_id_bot to the group and sending /id"
                )
                return
            except SessionPasswordNeededError:
                state['step'] = 'password'
                await event.reply("🔒 Two-step auth is enabled. Please enter your 2FA password:")
                return
            except (PhoneCodeInvalidError, PhoneCodeExpiredError):
                login_states.pop(uid, None)
                try:
                    await tele_client.disconnect()
                except:
                    pass
                await event.reply("❌ Invalid or expired code. Restart with /login.")
                return
            except Exception as e:
                login_states.pop(uid, None)
                try:
                    await tele_client.disconnect()
                except:
                    pass
                await event.reply(f"❌ Error verifying code: {e}")
                return

        if step == "password":
            password = event.raw_text.strip()
            tele_client = state.get("tele_client")
            if not tele_client:
                login_states.pop(uid, None)
                await event.reply("❌ Session expired. Please restart with /login.")
                return
            try:
                await tele_client.sign_in(password=password)
                state['step'] = 'group_id'
                await event.reply("✅ 2FA verified. Now provide the group ID where you want to use this account:")
                return
            except Exception as e:
                login_states.pop(uid, None)
                try:
                    await tele_client.disconnect()
                except:
                    pass
                await event.reply(f"❌ Error with 2FA password: {e}")
                return

        if step == "group_id":
            tele_client = state.get("tele_client")
            phone = state.get("phone")
            try:
                group_id = int(event.raw_text.strip())
            except ValueError:
                await event.reply("❌ Invalid group ID. Enter numeric group ID:")
                return

            if not tele_client:
                login_states.pop(uid, None)
                await event.reply("❌ Session expired. Please restart with /login.")
                return

            try:
                # Test access to group
                chat = await tele_client.get_entity(group_id)
                # Save session string
                session_string = tele_client.session.save()

                # Persist account
                accounts_col.delete_one({"phone": phone})
                accounts_col.insert_one({
                    "user_id": uid,
                    "phone": phone,
                    "chat_id": group_id,
                    "session_string": session_string,
                    "active": False
                })
                await event.reply(f"✅ Successfully logged in!\n📱 Account: {phone}\n👥 Group: {getattr(chat, 'title', str(group_id))}\nYou can now use /startall or /start_guess <phone>.")
            except Exception as e:
                await event.reply(f"❌ Cannot access group {group_id}. Make sure the account is part of that group and try again.")
                logger.exception("Group access error: %s", e)
            finally:
                # disconnect tele client we used for login
                try:
                    await tele_client.disconnect()
                except:
                    pass
                login_states.pop(uid, None)
            return

    except Exception as e:
        logger.exception("Login flow error: %s", e)
        login_states.pop(uid, None)
        try:
            if 'tele_client' in state and state['tele_client']:
                await state['tele_client'].disconnect()
        except:
            pass
        await event.reply(f"❌ Unexpected error: {e}")

# -------------------------
# Accounts / logout / list
# -------------------------
@bot.on(events.NewMessage(pattern=r'^/accounts$'))
@authorized_only
async def accounts_handler(event):
    uid = event.sender_id
    if uid == ADMIN_USER_ID:
        docs = list(accounts_col.find({}))
        if not docs:
            await event.reply("No accounts found.")
            return
        msg = "<b>All Accounts (Admin):</b>\n"
        for acc in docs:
            user_info = users_col.find_one({"user_id": acc.get("user_id")})
            username = user_info['username'] if user_info else 'Unknown'
            phone = acc.get("phone")
            status = "Active" if acc.get("active") else "Inactive"
            msg += f"• Phone: {phone} | User: {username} (ID: {acc.get('user_id')}) | Status: {status}\n"
        await event.reply(safe_text(msg))
    else:
        docs = list(accounts_col.find({"user_id": uid}))
        if not docs:
            await event.reply("No accounts found.")
            return
        msg = "<b>Your Accounts:</b>\n"
        for acc in docs:
            phone = acc.get("phone")
            status = "Active" if acc.get("active") else "Inactive"
            msg += f"• Phone: {phone} | Chat ID: {acc.get('chat_id')} | Status: {status}\n"
        await event.reply(safe_text(msg))

@bot.on(events.NewMessage(pattern=r'^/logout(?:\s+.+)?$'))
@authorized_only
async def logout_handler(event):
    text = event.raw_text.strip().split()
    if len(text) < 2:
        await event.reply("❌ Usage: /logout <phone>")
        return
    phone = text[1].strip()
    uid = event.sender_id

    acc = accounts_col.find_one({"phone": phone})
    if not acc:
        await event.reply(f"❌ No account found with phone: {phone}")
        return
    if acc.get("user_id") != uid and uid != ADMIN_USER_ID:
        await event.reply("❌ You can only logout your own accounts.")
        return

    # cancel tasks and disconnect clients
    if phone in account_tasks:
        task = account_tasks.pop(phone, None)
        if task:
            task.cancel()
    if phone in auto_catch_tasks:
        task = auto_catch_tasks.pop(phone, None)
        if task:
            task.cancel()
    if phone in account_clients:
        try:
            await account_clients[phone].disconnect()
        except:
            pass
        account_clients.pop(phone, None)

    # remove from DB
    accounts_col.delete_one({"phone": phone})
    await event.reply(f"✅ Successfully logged out and removed account: {phone}")

# -------------------------
# Start/Stop guessing per-account
# -------------------------
async def ensure_user_client(phone, session_string):
    """Return a connected Telethon client for the given session string (create/connect if necessary)."""
    if phone in account_clients:
        client = account_clients[phone]
        if await client.is_connected():
            return client
        else:
            try:
                await client.connect()
                return client
            except:
                # fall through to recreate
                pass

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    await client.connect()
    # ensure authorized
    if not await client.is_user_authorized():
        # attempt start (should usually be authorized if session_string is valid)
        try:
            await client.start()
        except Exception:
            pass
    account_clients[phone] = client
    return client

@bot.on(events.NewMessage(pattern=r'^/stop(?:\s+.+)?$'))
@authorized_only
async def stop_handler(event):
    parts = event.raw_text.strip().split()
    if len(parts) < 2:
        await event.reply("❌ Usage: /stop <phone>")
        return
    phone = parts[1].strip()
    uid = event.sender_id

    acc = accounts_col.find_one({"phone": phone})
    if not acc:
        await event.reply(f"❌ No account found with phone: {phone}")
        return
    if acc.get("user_id") != uid and uid != ADMIN_USER_ID:
        await event.reply("❌ You can only stop your own accounts.")
        return

    stopped = False
    if phone in account_tasks:
        t = account_tasks.pop(phone, None)
        if t:
            t.cancel()
            stopped = True
    if phone in auto_catch_tasks:
        t = auto_catch_tasks.pop(phone, None)
        if t:
            t.cancel()
            stopped = True
    if phone in account_clients:
        try:
            await account_clients[phone].disconnect()
        except:
            pass
        account_clients.pop(phone, None)

    accounts_col.update_one({"phone": phone}, {"$set": {"active": False}})
    if stopped:
        await event.reply(f"✅ Stopped all activities for account: {phone}")
    else:
        await event.reply(f"❌ Account {phone} was not running.")

# -------------------------
# Startall / Stopall / Status
# -------------------------
from telethon import events, Button

# start_guess
@bot.on(events.NewMessage(pattern=r'^/start_guess(?:\s+(.+))?'))
async def start_guess_cmd(event):
    """Start guessing for a specific account."""
    user_id = event.sender_id
    args = event.raw_text.split()
    if len(args) < 2:
        await event.reply("❌ Usage: /start_guess <phone>")
        return

    phone = args[1].strip()
    account = accounts_col.find_one({"phone": phone})
    if not account:
        await event.reply(f"❌ No account found with phone: {phone}")
        return

    if account['user_id'] != user_id and user_id != ADMIN_USER_ID:
        await event.reply("❌ You can only start your own accounts.")
        return

    global account_tasks
    if phone in account_tasks and await is_task_running(account_tasks[phone]):
        await event.reply(f"❌ Account {phone} is already running.")
        return

    try:
        account_clients = await get_account_clients()
        if phone not in account_clients:
            await event.reply(f"❌ Failed to initialize client for {phone}. Please try /logout and /login again.")
            return

        client_obj = account_clients[phone]
        chat_id = account['chat_id']

        if not client_obj.is_connected():
            await client_obj.connect()

        if not await client_obj.is_user_authorized():
            await event.reply(f"❌ Account {phone} not authorized. Please log in again.")
            return

        task = asyncio.create_task(guessing_logic(client_obj, chat_id, phone))
        account_tasks[phone] = task
        accounts_col.update_one({"phone": phone}, {"$set": {"active": True}})

        await event.reply(f"✅ Started guessing for account: {phone}")
        await log_message(chat_id, f"Started guessing for {phone}")

    except Exception as e:
        await event.reply(f"❌ Error starting account {phone}: {str(e)}")


# startall
@bot.on(events.NewMessage(pattern=r'^/startall$'))
async def startall_cmd(event):
    """Show Auto Guess and Auto Catch options for all accounts."""
    try:
        user_id = event.sender_id
        accounts = list(accounts_col.find({"user_id": user_id}))
        if not accounts:
            await event.reply("❌ No accounts found. Use /login to add an account first.")
            return

        buttons = [
            [Button.inline("🎯 Auto Guess", b"auto_guess")],
            [Button.inline("🎣 Auto Catch", b"auto_catch")]
        ]

        await event.respond(
            f"🚀 Choose mode for {len(accounts)} accounts:\n\n"
            "🎯 Auto Guess - Start Pokemon guessing in groups\n"
            "🎣 Auto Catch - Start Pokemon hunting/catching\n\n"
            "Select your preferred mode:",
            buttons=buttons
        )

    except Exception as e:
        await event.reply(f"❌ Error in startall_cmd: {str(e)}")

from telethon import events, Button

# /start <phone> command
@bot.on(events.NewMessage(pattern=r'^/start(?:\s+(\+?\d+))?'))
async def start_single_cmd(event):
    args = event.message.text.split()
    if len(args) < 2:
        await event.reply("❌ Usage: /start <phone>")
        return

    phone = args[1]
    # Store phone number in the button callback so you know which account to manage
    buttons = [[
            Button.inline("Guess", f"single_guess|{phone}"),
            Button.inline("Catch", f"single_catch|{phone}")
        ]]

    await event.reply(f"Choose an action for phone: {phone}", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"^(single_guess|single_catch)\|"))
async def single_callback_handler(event):
    action, phone = event.data.decode().split("|")
    user_id = event.sender_id

    # Fetch account and chat_id
    account = accounts_col.find_one({"phone": phone})
    if not account:
        await event.reply(f"❌ No account found with phone: {phone}")
        return

    if account['user_id'] != user_id and user_id != ADMIN_USER_ID:
        await event.reply("❌ You can only start your own accounts.")
        return

    global account_tasks
    if phone in account_tasks and await is_task_running(account_tasks[phone]):
        await event.reply(f"❌ Account {phone} is already running.")
        return

    account_clients = await get_account_clients()
    if phone not in account_clients:
        await event.reply(f"❌ Failed to initialize client for {phone}. Please try /logout and /login again.")
        return

    client_obj = account_clients[phone]
    chat_id = account['chat_id']

    if not client_obj.is_connected():
        await client_obj.connect()

    if action == "single_guess":
        await event.answer(f"Starting guess for {phone}...")
        task = asyncio.create_task(guessing_logic(client_obj, chat_id, phone))
    elif action == "single_catch":
        await event.answer(f"Starting catch for {phone}...")
        task = asyncio.create_task(auto_catch_logic(client_obj, phone))

    account_tasks[phone] = task
    accounts_col.update_one({"phone": phone}, {"$set": {"active": True}})
    await event.reply(f"✅ Started {action.replace('single_', '')} for account: {phone}")

# callback for inline buttons
@bot.on(events.CallbackQuery(pattern=b"^(auto_guess|auto_catch)$"))
async def handle_startall_callback(event):
    """Handle Auto Guess and Auto Catch button callbacks."""
    try:
        user_id = event.sender_id
        mode = event.data.decode("utf-8")

        accounts = list(accounts_col.find({"user_id": user_id}))
        if not accounts:
            await event.answer("❌ No accounts found!", alert=True)
            return

        await event.answer(f"Starting {mode.replace('_', ' ').title()} mode...")

        if mode == "auto_guess":
            await start_auto_guess_all(event, user_id, accounts)
        elif mode == "auto_catch":
            await start_auto_catch_all(event, user_id, accounts)

    except Exception as e:
        await event.answer(f"❌ Error: {str(e)}", alert=True)

from telethon import TelegramClient

# stopall
@bot.on(events.NewMessage(pattern=r'^/stopall$'))
async def stopall_cmd(event):
    """Stop all guessing/catching accounts for the user."""
    user_id = event.sender_id
    accounts = list(accounts_col.find({"user_id": user_id}))
    if not accounts:
        await event.reply("❌ No accounts found.")
        return

    global account_tasks, account_clients, auto_catch_tasks
    stopped_count = 0

    for acc in accounts:
        phone = acc['phone']

        # Stop guessing tasks
        if phone in account_tasks:
            try:
                task = account_tasks[phone]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                del account_tasks[phone]
                stopped_count += 1
            except Exception as e:
                print(f"Error stopping guessing for {phone}: {e}")

        # Stop catching tasks
        if phone in auto_catch_tasks:
            try:
                hunt_status[phone] = False
                task = auto_catch_tasks[phone]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                del auto_catch_tasks[phone]
                stopped_count += 1
            except Exception as e:
                print(f"Error stopping catching for {phone}: {e}")

        # Update DB and disconnect
        try:
            accounts_col.update_one({"_id": acc["_id"]}, {"$set": {"active": False}})
            if phone in account_clients:
                if account_clients[phone].is_connected():
                    await account_clients[phone].disconnect()
                del account_clients[phone]
        except Exception as e:
            print(f"Error cleaning up {phone}: {e}")

    if stopped_count > 0:
        await event.reply(f"✅ Stopped all activities for {stopped_count} account{'s' if stopped_count != 1 else ''}")
    else:
        await event.reply("❌ No active accounts to stop.")


# status
@bot.on(events.NewMessage(pattern=r'^/status$'))
async def status_cmd(event):
    user_id = event.sender_id
    accounts = list(accounts_col.find({"user_id": user_id}))
    if not accounts:
        await event.reply("No accounts found.")
        return

    global account_tasks, auto_catch_tasks
    msg = "<b>Status:</b>\n"
    for acc in accounts:
        phone = acc['phone']
        is_guessing = phone in account_tasks and not account_tasks[phone].done()
        is_catching = phone in auto_catch_tasks and not auto_catch_tasks[phone].done()

        status = "🎯 Guessing" if is_guessing else "🎣 Catching" if is_catching else "❌ Inactive"
        msg += f"• <b>Phone:</b> {phone} | <b>Status:</b> {status}\n"

    await event.reply(msg, parse_mode="html")

# --- AUTO GUESS + AUTO CATCH HELPERS (Telethon-ready) ---

async def guessing_logic(client, chat_id, phone):
    """Main guessing logic for the Pokemon guessing game."""
    last_guess_time = 0
    guess_timeout = 15
    pending_guess = False
    retry_lock = asyncio.Lock()

    async def send_guess_command():
        nonlocal last_guess_time, pending_guess
        try:
            await client.send_message(chat_id, '/guess')
            last_guess_time = time.time()
            pending_guess = True
            return True
        except Exception as e:
            await log_message(chat_id, f"Error in sending /guess: {e}")
            return False

    @client.on(events.NewMessage(chats=chat_id, pattern="Who's that pokemon", incoming=True))
    async def guess_pokemon(event):
        nonlocal pending_guess
        try:
            pending_guess = False
            if event.message.photo:
                for size in event.message.photo.sizes:
                    if isinstance(size, PhotoStrippedSize):
                        size_str = str(size)
                        cache_dir = "cache"
                        if os.path.exists(cache_dir):
                            for file in os.listdir(cache_dir):
                                if file.endswith('.txt'):
                                    with open(os.path.join(cache_dir, file), 'r') as f:
                                        file_content = f.read()
                                    if file_content == size_str:
                                        pokemon_name = file.split(".txt")[0]
                                        await asyncio.sleep(2)
                                        await client.send_message(chat_id, f"{pokemon_name}")
                                        await asyncio.sleep(15)
                                        await send_guess_command()
                                        return

                        with open("cache.txt", 'w') as file:
                            file.write(size_str)
                        await log_message(chat_id, "New Pokémon detected, cached photo signature")

        except Exception as e:
            await log_message(chat_id, f"Error in guessing Pokémon: {e}")

    @client.on(events.NewMessage(chats=chat_id, pattern="The Pokemon was", incoming=True))
    async def save_pokemon(event):
        nonlocal pending_guess
        try:
            pending_guess = False
            message_text = event.message.text or ''
            pokemon_name = None

            patterns = [
                r'The pokemon was \*\*(.*?)\*\*',
                r'The pokemon was "(.*?)"',
                r'The pokemon was (.*?)\.',
                r'It was \*\*(.*?)\*\*',
                r'Correct answer was \*\*(.*?)\*\*'
            ]
            for pattern in patterns:
                match = re.search(pattern, message_text)
                if match:
                    pokemon_name = match.group(1).strip()
                    break

            if pokemon_name:
                await log_message(chat_id, f"The Pokémon was: {pokemon_name}")

                if os.path.exists("Ag/cache.txt"):
                    try:
                        with open("cache.txt", 'r') as inf:
                            cont = inf.read().strip()
                        if cont:
                            cache_dir = "cache"
                            os.makedirs(cache_dir, exist_ok=True)
                            cache_path = os.path.join(cache_dir, f"{pokemon_name.lower()}.txt")
                            with open(cache_path, 'w') as file:
                                file.write(cont)
                            await log_message(chat_id, f"Saved {pokemon_name} to cache")
                            os.remove("cache.txt")
                    except Exception as e:
                        await log_message(chat_id, f"Error processing cache file: {e}")

                if "+5" in message_text or "💵" in message_text and "The Pokemon was" in message_text:
                    await log_message(chat_id, "Reward received, continuing guessing")
                    if phone in daily_limits:
                        del daily_limits[phone]
                        if phone in limit_timers:
                            del limit_timers[phone]
                        await log_message(chat_id, "Daily limit reset - rewards working again")
                    await asyncio.sleep(2)
                    await send_guess_command()
                else:
                    await log_message(chat_id, "No reward received - daily limit reached")
                    daily_limits[phone] = True
                    limit_timers[phone] = time.time()
                    await log_message(chat_id, "Switching to auto catch mode for 6 hours")
                    if phone in account_tasks:
                        account_tasks[phone].cancel()
                    await start_auto_catch_single(phone, client, chat_id)
                    asyncio.create_task(schedule_auto_guess_restart(phone, client, chat_id))
                    return
        except Exception as e:
            await log_message(chat_id, f"Error in saving Pokémon data: {e}")

    @client.on(events.NewMessage(chats=chat_id, pattern="There is already a guessing game being played", incoming=True))
    async def handle_active_game(event):
        nonlocal pending_guess
        await log_message(chat_id, "Game already active. Retrying shortly...")
        pending_guess = False
        await asyncio.sleep(5)
        await send_guess_command()

    async def monitor_responses():
        nonlocal last_guess_time, pending_guess
        last_periodic_guess = 0
        while True:
            try:
                async with retry_lock:
                    current_time = time.time()
                    if pending_guess and (current_time - last_guess_time > guess_timeout):
                        await log_message(chat_id, "No response detected after /guess. Retrying...")
                        await send_guess_command()
                    elif not pending_guess and (current_time - last_periodic_guess > 300):
                        await log_message(chat_id, "Sending periodic /guess to prevent lag")
                        await send_guess_command()
                        last_periodic_guess = current_time
                await asyncio.sleep(4)
            except Exception as e:
                await log_message(chat_id, f"Error in monitoring responses: {e}")
                await asyncio.sleep(4)

    try:
        await log_message(chat_id, f"Starting guessing logic for phone: {phone}")
        if not client.is_connected():
            await client.connect()
        monitor_task = asyncio.create_task(monitor_responses())
        await send_guess_command()
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await log_message(chat_id, "Guessing task was cancelled")
    except Exception as e:
        await log_message(chat_id, f"Error in guessing loop: {e}")
    finally:
        if 'monitor_task' in locals():
            monitor_task.cancel()
            try:
                await monitor_task
            except:
                pass


async def start_auto_guess_all(event, user_id, accounts):
    global account_tasks
    account_clients = await get_account_clients()
    start_tasks, valid_accounts = [], []

    for acc in accounts:
        phone = acc['phone']
        if phone in account_tasks and await is_task_running(account_tasks[phone]):
            continue
        if phone in account_clients:
            valid_accounts.append(acc)
            start_tasks.append(start_single_guess_account(acc, account_clients[phone]))

    if not start_tasks:
        await event.message.edit("❌ All accounts are already running or no valid accounts found.")
        return

    results = await asyncio.gather(*start_tasks, return_exceptions=True)
    started_count, errors = 0, []

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append(f"❌ {valid_accounts[i]['phone']}: {str(result)}")
        elif result:
            started_count += 1

    status_parts = []
    if started_count > 0:
        status_parts.append(f"✅ Started Auto Guess for {started_count} account{'s' if started_count != 1 else ''}")
    if errors:
        status_parts.append(f"❌ {len(errors)} error{'s' if len(errors) != 1 else ''}")

    await event.message.edit(" | ".join(status_parts) if status_parts else "❌ No accounts started")


async def start_auto_catch_all(event, user_id, accounts):
    global auto_catch_tasks
    account_clients = await get_account_clients()
    start_tasks, valid_accounts = [], []

    for acc in accounts:
        phone = acc['phone']
        if phone in auto_catch_tasks and await is_task_running(auto_catch_tasks[phone]):
            continue
        if phone in account_clients:
            valid_accounts.append(acc)
            start_tasks.append(start_single_catch_account(acc, account_clients[phone]))

    if not start_tasks:
        await event.message.edit("❌ All accounts are already running or no valid accounts found.")
        return

    results = await asyncio.gather(*start_tasks, return_exceptions=True)
    started_count, errors = 0, []

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            errors.append(f"❌ {valid_accounts[i]['phone']}: {str(result)}")
        elif result:
            started_count += 1

    status_parts = []
    if started_count > 0:
        status_parts.append(f"✅ Started Auto Catch for {started_count} account{'s' if started_count != 1 else ''}")
    if errors:
        status_parts.append(f"❌ {len(errors)} error{'s' if len(errors) != 1 else ''}")

    await event.message.edit(" | ".join(status_parts) if status_parts else "❌ No accounts started")

async def schedule_hunt_restart(phone, client, chat_id):
    """Schedule hunt restart after 6 hours when hunt limit resets."""
    try:
        await log_message(chat_id, f"Scheduled hunt restart for {phone} in 6 hours")
        
        # Wait 6 hours (21600 seconds)
        await asyncio.sleep(21600)
        
        await log_message(chat_id, f"6 hours passed, hunt limit should be reset for {phone}")
        
        # Stop current guessing if running
        if phone in account_tasks:
            account_tasks[phone].cancel()
            del account_tasks[phone]
        
        # Start auto catch again
        await start_auto_catch_single(phone, client, chat_id)
        
    except Exception as e:
        await log_message(chat_id, f"Error in scheduled hunt restart for {phone}: {e}")

async def schedule_auto_guess_restart(phone, client, chat_id):
    """Schedule auto guess restart after 6 hours when daily limit resets."""
    try:
        await log_message(chat_id, f"Scheduled auto guess restart for {phone} in 6 hours")
        
        # Wait 6 hours (21600 seconds)
        await asyncio.sleep(21600)
        
        # Check if account is still in daily limit
        if phone in daily_limits:
            await log_message(chat_id, f"6 hours passed, restarting auto guess for {phone}")
            
            # Stop auto catch
            if phone in auto_catch_tasks:
                auto_catch_tasks[phone].cancel()
                del auto_catch_tasks[phone]
            
            # Remove from daily limits
            del daily_limits[phone]
            if phone in limit_timers:
                del limit_timers[phone]
            
            # Start auto guess again
            task = asyncio.create_task(guessing_logic(client, chat_id, phone))
            account_tasks[phone] = task
            
            await log_message(chat_id, f"Auto guess restarted for {phone}")
        
    except Exception as e:
        await log_message(chat_id, f"Error in scheduled restart for {phone}: {e}")

async def start_auto_catch_single(phone, client, chat_id):
    """Start auto catch for a single account when daily limit is reached."""
    try:
        await log_message(chat_id, f"Starting auto catch for {phone}")
        
        # Send initial /hunt command
        await client.send_message(entity=chat_id, message='/hunt')
        await log_message(chat_id, "Sent /hunt command for auto catch")
        
        # Add handler for daily hunt limit reached
        @client.on(events.NewMessage(chats=chat_id, pattern="Daily hunt limit reached", incoming=True))
        async def handle_hunt_limit(event):
            await log_message(chat_id, f"Hunt limit reached for {phone} - switching to auto guess")
            
            # Stop auto catch
            if phone in auto_catch_tasks:
                auto_catch_tasks[phone].cancel()
                del auto_catch_tasks[phone]
            
            # Start auto guess
            task = asyncio.create_task(guessing_logic(client, chat_id, phone))
            account_tasks[phone] = task
            
            # Schedule hunt restart after 6 hours
            asyncio.create_task(schedule_hunt_restart(phone, client, chat_id))
        
        # Set up periodic /hunt sending to prevent lag
        async def periodic_hunt():
            while phone in daily_limits:  # Continue while in daily limit
                try:
                    await asyncio.sleep(300)  # Wait 5 minutes
                    if phone in daily_limits:  # Check again after sleep
                        await client.send_message(entity=chat_id, message='/hunt')
                        await log_message(chat_id, "Sent periodic /hunt to prevent lag")
                except Exception as e:
                    await log_message(chat_id, f"Error in periodic hunt: {e}")
                    break
        
        # Start periodic hunt task
        hunt_task = asyncio.create_task(periodic_hunt())
        auto_catch_tasks[phone] = hunt_task
        
    except Exception as e:
        await log_message(chat_id, f"Error starting auto catch for {phone}: {e}")

async def start_single_guess_account(account, client_obj):
    """Start guessing for a single account (helper function for concurrent execution)."""
    try:
        phone = account['phone']
        chat_id = account['chat_id']
        acc_id = account['_id']
        
        global account_tasks
        
        # Connect client if not connected
        if not client_obj.is_connected():
            await client_obj.connect()
            
        # Check authorization
        if not await client_obj.is_user_authorized():
            raise Exception(f"Account {phone} not authorized")
        
        # Cancel existing task if it exists
        if phone in account_tasks:
            task = account_tasks[phone]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        # Start new guessing task
        task = asyncio.create_task(guessing_logic(client_obj, chat_id, phone))
        account_tasks[phone] = task
        accounts_col.update_one({"_id": acc_id}, {"$set": {"active": True}})
        
        await log_message(chat_id, f"Started guessing for {phone}")
        return True
        
    except Exception as e:
        print(f"Error starting {account['phone']}: {str(e)}")
        raise e

async def start_single_catch_account(account, client_obj):
    """Start catching for a single account (helper function for concurrent execution)."""
    try:
        phone = account['phone']
        acc_id = account['_id']
        
        global auto_catch_tasks
        
        # Connect client if not connected
        if not client_obj.is_connected():
            await client_obj.connect()
            
        # Check authorization
        if not await client_obj.is_user_authorized():
            raise Exception(f"Account {phone} not authorized")
        
        # Cancel existing task if it exists
        if phone in auto_catch_tasks:
            task = auto_catch_tasks[phone]
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
        
        # Start new catching task
        task = asyncio.create_task(auto_catch_logic(client_obj, phone))
        auto_catch_tasks[phone] = task
        accounts_col.update_one({"_id": acc_id}, {"$set": {"active": True}})
        
        await log_message(CATCH_CHAT_ID, f"Started auto catch for {phone}")
        return True
        
    except Exception as e:
        print(f"Error starting catch for {account['phone']}: {str(e)}")
        raise e

import re
from telethon.errors import FloodWaitError

async def auto_catch_logic(client, phone):
    """Auto catch logic for Pokemon hunting with inventory check."""
    global hunt_status
    hunt_status[phone] = True
    user_notify_chat_id = -1002280330282

    async def check_inventory(initial=False):
        """Check Repeat Balls and Poke Dollars, buy if needed."""
        try:
            entity = await client.get_entity(CATCH_CHAT_ID)
            await client.send_message(entity, "/myinventory")
            await asyncio.sleep(3)

            msgs = await client.get_messages(entity, limit=5)
            inv_msg = next((m for m in msgs if "Poke Dollars" in (m.text or "")), None)

            if inv_msg is None:
                await client.send_message(user_notify_chat_id,
                                          f"⚠️ [{phone}] Inventory not found, cannot continue.")
                return await check_inventory(initial=True)

            text = inv_msg.text

            balls_match = re.search(r"Repeat Balls:\s*(\d+)", text)
            balls = int(balls_match.group(1)) if balls_match else 0

            money_match = re.search(r"Poke Dollars.*?:\s*([\d,]+)", text)
            money = int(money_match.group(1).replace(",", "")) if money_match else 0

            print(f"[{phone}] Inventory → Balls={balls}, Money={money}")

            if balls < 30:
                if money > 1500:
                    await client.send_message(entity, "/buy repeat 30")
                    await asyncio.sleep(2)
                    await client.send_message(user_notify_chat_id,
                                              f"💰 [{phone}] Bought 30 Repeat Balls (Money left: {money-1200})")
                else:
                    await client.send_message(user_notify_chat_id,
                                              f"❌ [{phone}] Not enough balls and low Poke Dollars ({money}). Hunt stopped.")
                    return False
            elif initial:
                await client.send_message(user_notify_chat_id,
                                          f"✅ [{phone}] Ready to hunt → Balls={balls}, Money={money}")
            return True
        except Exception as e:
            print(f"[{phone}] Error checking inventory: {e}")
            return False

    async def send_hunt():
        """Send /hunt repeatedly until stopped."""
        entity = await client.get_entity(CATCH_CHAT_ID)
        while hunt_status.get(phone, False):
            try:
                await client.send_message(entity, "/hunt")
                await asyncio.sleep(randint(2, 4))
            except FloodWaitError as e:
                print(f"[{phone}] Flood wait: sleeping {e.seconds}s")
                await asyncio.sleep(e.seconds)
            except Exception as e:
                print(f"[{phone}] Error in send_hunt: {e}")
                await asyncio.sleep(10)

    @client.on(events.NewMessage(chats=CATCH_CHAT_ID, incoming=True))
    async def battle_handler(event):
        if not hunt_status.get(phone, False):
            return
        try:
            if "Daily hunt limit reached" in (event.message.text or ""):
                hunt_status[phone] = False
                await log_message(user_notify_chat_id,
                                  f"⏹️ [{phone}] Daily hunt limit reached. Auto catch stopped.")
                return
            if event.message.text.startswith("Battle begins"):
                msg = event.message
                await asyncio.sleep(15)
                for _ in range(6):
                    try:
                        await msg.click(text="Poke Balls")
                        await asyncio.sleep(1)
                    except:
                        break
        except Exception as e:
            print(f"[{phone}] Error in battle_handler: {e}")

    @client.on(events.MessageEdited(chats=CATCH_CHAT_ID))
    async def catch_handler(event):
        if not hunt_status.get(phone, False):
            return
        try:
            if "Daily hunt limit reached" in (event.message.text or ""):
                hunt_status[phone] = False
                await log_message(user_notify_chat_id,
                                  f"⏹️ [{phone}] Daily hunt limit reached. Auto catch stopped.")
                return
            msg = event.message
            if msg.text.startswith("Wild"):
                for _ in range(27):
                    try:
                        await msg.click(text="Repeat")
                        await asyncio.sleep(0.5)
                    except:
                        break
            if any(k in (msg.text or "") for k in ["fled", "fainted", "caught"]):
                await asyncio.sleep(randint(2, 4))
                await client.send_message(CATCH_CHAT_ID, "/hunt")
        except Exception as e:
            print(f"[{phone}] Error in catch_handler: {e}")

    try:
        await log_message(CATCH_CHAT_ID, f"🚀 Starting auto catch for {phone}")

        if not client.is_connected():
            await client.connect()

        ok = await check_inventory(initial=True)
        if not ok:
            hunt_status[phone] = False
            return

        hunt_task = asyncio.create_task(send_hunt())

        while hunt_status.get(phone, False):
            await asyncio.sleep(60)

    except Exception as e:
        await log_message(CATCH_CHAT_ID, f"❌ Fatal error in auto_catch_logic for {phone}: {e}")
    finally:
        hunt_status[phone] = False
        if 'hunt_task' in locals():
            hunt_task.cancel()
            try:
                await hunt_task
            except:
                pass

# (the other helpers like start_single_guess_account, start_single_catch_account, auto_catch_logic, 
# start_auto_catch_single, schedule_auto_guess_restart, schedule_hunt_restart 
# remain unchanged — they’re already Telethon-compatible)

# -------------------------
# Run the bot
# -------------------------

if __name__ == "__main__":
    header = f" TELETHON BOT "
    border_char, width = "═", 72
    header_line = border_char * ((width - len(header) - 2) // 2)
    padding_needed = width - 2 - (len(header_line) * 2 + len(header))
    header_line_end = border_char * padding_needed if padding_needed > 0 else ""
    print(f"╔{header_line}{header}{header_line}{header_line_end}╗")

    admin_display_list = [str(ADMIN_USER_ID)]
    print(f"║ Admins: {', '.join(admin_display_list)}".ljust(width - 1) + "║")
    print(f"╚{'═' * (width - 2)}╝")

    # ✅ Startup log (run async in current loop)
    bot.loop.run_until_complete(log_message(LOG_CHANNEL_ID, "🚀 Bot started successfully with Telethon"))

    # ✅ Run bot forever
    bot.run_until_disconnected()

    # ✅ Shutdown log
    try:
        bot.loop.run_until_complete(log_message(LOG_CHANNEL_ID, "🛑 Bot stopped"))
    except:
        pass
