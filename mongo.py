import os
import asyncio
import time
import re
import random
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import SessionPasswordNeeded, PhoneNumberInvalid, PhoneCodeInvalid, PhoneCodeExpired, FloodWait, UserNotParticipant, ChannelPrivate
from telethon import TelegramClient as TClient
from telethon import events
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError, PhoneNumberInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError, FloodWaitError
from telethon.tl.types import PhotoStrippedSize
import aiosqlite
import json
import hashlib

import io
import sqlite3

# Simple SQLite database helper for accounts
from pymongo import MongoClient
import os

class Database:
    def __init__(self, uri: str = None, db_name: str = "accounts_db"):
        # Use environment variable or pass URI
        self.uri = uri or os.getenv("MONGO_URI", "mongodb://Celestial_Guard:Rrahaman%400000@ac-p8nqtpp-shard-00-00.8lwwa1m.mongodb.net:27017,ac-p8nqtpp-shard-00-01.8lwwa1m.mongodb.net:27017,ac-p8nqtpp-shard-00-02.8lwwa1m.mongodb.net:27017/?ssl=true&replicaSet=atlas-fopv4u-shard-0&authSource=admin&retryWrites=true&w=majority&appName=AutoGuess69")
        self.client = MongoClient(self.uri)
        self.db = self.client[db_name]
        self.coll = self.db["accounts"]

    def init_db(self):
        # Ensure index on phone (unique)
        self.coll.create_index("phone", unique=True)

    def add_account(self, owner_id: int, phone: str, chat_id: int, active: int, session_string: str):
        self.coll.update_one(
            {"phone": phone},
            {
                "$set": {
                    "owner_id": owner_id,
                    "phone": phone,
                    "chat_id": chat_id,
                    "reserved": 0,
                    "session_string": session_string,
                    "active": active,
                }
            },
            upsert=True,
        )

    def get_accounts(self, owner_id: int):
        # Return in same tuple structure as SQLite version
        docs = list(self.coll.find({"owner_id": owner_id}))
        results = []
        for idx, d in enumerate(docs, start=1):
            results.append((
                idx,
                d.get("phone"),
                d.get("chat_id"),
                d.get("reserved", 0),
                d.get("session_string"),
                d.get("active", 0),
            ))
        return results

    def remove_account(self, account_id: int):
        # account_id corresponds to phone (unique)
        self.coll.delete_one({"phone": account_id})

    def set_account_active(self, account_id: int, active: int):
        self.coll.update_one({"phone": account_id}, {"$set": {"active": active}})


# Initialize DB
db = Database(uri="mongodb://Celestial_Guard:Rrahaman%400000@ac-p8nqtpp-shard-00-00.8lwwa1m.mongodb.net:27017,ac-p8nqtpp-shard-00-01.8lwwa1m.mongodb.net:27017,ac-p8nqtpp-shard-00-02.8lwwa1m.mongodb.net:27017/?ssl=true&replicaSet=atlas-fopv4u-shard-0&authSource=admin&retryWrites=true&w=majority&appName=AutoGuess69")
db.init_db()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== CONFIGURATION =====
# Bot credentials
API_ID = 21453458
API_HASH = '565cac9ed11ff64ca7e2626f7b1b18b2'
BOT_TOKEN = '8459680405:AAGxmZF8bJL9kxOKwIFKWnf8jPaa_d5CoiU'
ADMIN_USER_ID = 5621201759
LOG_CHANNEL_ID = -1002874694180

# Bot settings
SESSION_NAME = "pokemon_guesser_bot"
WORKERS = 4

from pyrogram import enums as _enums

# Authorization decorator (also restricts to private chats)
def authorized_only(func):
    async def wrapper(client, message):
        # Only allow in private chats
        if getattr(message, "chat", None) and message.chat.type != _enums.ChatType.PRIVATE:
            # Silently ignore in groups/channels
            return
        # Authorization check
        if message.from_user.id not in AUTHORIZED_USERS:
            await message.reply(" You are not authorized to use this command.")
            return
        return await func(client, message)
    return wrapper

# Load authorized users from file
def load_authorized_users():
    if os.path.exists('authorized_users.json'):
        with open('authorized_users.json', 'r') as f:
            return set(json.load(f))
    return {ADMIN_USER_ID}

# Save authorized users to file
def save_authorized_users():
    with open('authorized_users.json', 'w') as f:
        json.dump(list(AUTHORIZED_USERS), f)

# Load authorized users
AUTHORIZED_USERS = load_authorized_users()
if ADMIN_USER_ID not in AUTHORIZED_USERS:
    AUTHORIZED_USERS.add(ADMIN_USER_ID)
    save_authorized_users()

# Initialize the Pyrogram client
app = Client(
    name=SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=WORKERS,
    plugins=dict(root="handlers")
)

# Print startup info
print("="*50)
print(f"Starting {SESSION_NAME}...")
print(f"API_ID: {API_ID}")
print(f"API_HASH: {API_HASH}")
print(f"BOT_TOKEN: {BOT_TOKEN[:10]}...")
print(f"ADMIN_USER_ID: {ADMIN_USER_ID}")
print(f"LOG_CHANNEL_ID: {LOG_CHANNEL_ID}")
print("="*50)

# Database setup
DB_NAME = 'bot_data.db'

# Global variables
login_states = {}
account_clients = {}
account_tasks = {}
chat_states = {}

# Helper functions
async def is_admin(user_id: int) -> bool:
    """Check if a user is an admin."""
    return user_id == ADMIN_USER_ID

# Admin commands
@app.on_message(filters.command("auth") & filters.user(ADMIN_USER_ID))
@authorized_only
async def auth_commands(client, message):
    if len(message.command) < 3:
        await message.reply(
            "Usage:\n"
            "/auth add <user_id> - Authorize a user\n"
            "/auth remove <user_id> - Remove user authorization\n"
            "/auth list - List all authorized users"
        )
        return

    cmd = message.command[1].lower()
    
    if cmd == "add":
        try:
            user_id = int(message.command[2])
            if user_id in AUTHORIZED_USERS:
                await message.reply(f"User {user_id} is already authorized.")
            else:
                AUTHORIZED_USERS.add(user_id)
                save_authorized_users()
                await message.reply(f"âœ… User {user_id} has been authorized.")
        except (ValueError, IndexError):
            await message.reply("âŒ Invalid user ID format.")
            
    elif cmd == "remove":
        try:
            user_id = int(message.command[2])
            if user_id == ADMIN_USER_ID:
                await message.reply("âŒ Cannot remove owner's authorization.")
            elif user_id in AUTHORIZED_USERS:
                AUTHORIZED_USERS.remove(user_id)
                save_authorized_users()
                await message.reply(f"âœ… User {user_id} has been removed from authorized users.")
            else:
                await message.reply(f"âŒ User {user_id} is not in the authorized list.")
        except (ValueError, IndexError):
            await message.reply("âŒ Invalid user ID format.")
            
    elif cmd == "list":
        users_list = "\n".join([f"- `{uid}`" for uid in sorted(AUTHORIZED_USERS)])
        await message.reply(f"ðŸ” Authorized Users:\n{users_list}")
    else:
        await message.reply("âŒ Unknown command. Use /auth for help.")

@app.on_message(filters.command('start'))
async def start_cmd(client, message: Message):
    user = message.from_user
    try:
        # Collect user details
        user_id = user.id
        first_name = user.first_name or "Not provided"
        username = f"@{user.username}" if getattr(user, 'username', None) else "Not provided"

        # Send user info to the user who started the bot
        info_text = (
            "User {} started the bot\n"
            "Name: {}\n"
            "Username: {}\n"
            "User ID: <code>{}</code>"
        ).format(user.username or user_id, first_name, username, user_id)
        await message.reply(info_text, parse_mode=enums.ParseMode.HTML)

        # If not authorized, notify admin with copiable command
        if user_id not in AUTHORIZED_USERS:
            admin_text = (
                "User {} started the bot\n"
                "Name: {}\n"
                "Username: {}\n"
                "User ID: <code>{}</code>\n\n"
                "Use <code>/auth add {}</code>\n\n"
                "to authorize the user"
            ).format(user.username or user_id, first_name, username, user_id, user_id)
            try:
                await app.send_message(ADMIN_USER_ID, admin_text, parse_mode=enums.ParseMode.HTML)
                await message.reply("â„¹ï¸ Your details were sent to the admin for approval.")
            except Exception as e:
                print(f"Could not notify admin: {e}")
        else:
            # Authorized users (including admin) get the normal welcome
            await message.reply("ðŸ‘‹ Welcome! Use /help to see commands.")

    except Exception as e:
        print(f"Error in start_cmd: {e}")
        await message.reply("âŒ An error occurred. Please try again later.")

@app.on_message(filters.command('help'))
@authorized_only
async def help_cmd(client, message: Message):
    await message.reply(
        "/login - Add a new account\n"
        "/accounts - List your accounts\n"
        "/logout - Log out an account\n"
        "/remove - Remove an account\n"
        "/startall - Start guessing for all accounts\n"
        "/stopall - Stop all guessing\n"
        "/status - Show status\n"
        "/help - Show this help message"
    )

async def cleanup_login_state(user_id):
    """Clean up login state and disconnect any active clients."""
    state = login_states.pop(user_id, None)
    if state and 'telethon_client' in state:
        try:
            await state['telethon_client'].disconnect()
        except:
            pass
    return state

@app.on_message(filters.command('cancel') & filters.private)
@authorized_only
async def cancel_cmd(client, message: Message):
    """Cancel the current login process."""
    user_id = message.from_user.id
    if user_id in login_states:
        state = await cleanup_login_state(user_id)
        if state and 'phone' in state:
            await message.reply(f"âŒ Login process for {state['phone']} has been cancelled.")
        else:
            await message.reply("âŒ Login process cancelled.")
    else:
        await message.reply("No active login process to cancel.")

@app.on_message(filters.command('login') & filters.private)
@authorized_only
async def login_cmd(client, message: Message):
    user_id = message.from_user.id
    
    # Clean up any existing state
    await cleanup_login_state(user_id)
    
    login_states[user_id] = {'step': 'phone', 'retry_count': 0}
    await message.reply(
        "ðŸ”‘ Please enter your phone number (with country code, e.g., +1234567890):\n"
        "You can type /cancel at any time to abort the login process."
    )

@app.on_message(filters.text & ~filters.command(['start', 'help', 'login', 'accounts', 'logout', 'remove', 'startall', 'stopall', 'status', 'cancel']))
@authorized_only
async def login_flow_handler(client, message: Message):
    user_id = message.from_user.id
        
    state = login_states.get(user_id)
    if not state:
        return
        
    step = state.get('step')
    if not step:
        login_states.pop(user_id, None)
        return
        
    try:
        if step == 'phone':
            phone = message.text.strip()
            if not phone:
                await message.reply("âŒ Please enter a valid phone number (e.g., +1234567890):")
                return
                
            state['phone'] = phone
            try:
                telethon_client = TClient(StringSession(), API_ID, API_HASH)
                await telethon_client.connect()
                sent = await telethon_client.send_code_request(phone)
                
                state['telethon_client'] = telethon_client
                state['sent'] = sent
                state['step'] = 'otp'
                state['retry_count'] = 0
                
                await message.reply("ðŸ”‘ Please enter the 5-digit OTP you received (format: 1 2 3 4 5):")
                
            except Exception as e:
                error_msg = str(e).lower()
                if 'flood' in error_msg:
                    await message.reply("âŒ Too many attempts. Please try again later.")
                elif 'phone' in error_msg:
                    await message.reply("âŒ Invalid phone number. Please try again with a valid number.")
                else:
                    await message.reply(f"âŒ Error sending code: {e}")
                
                login_states.pop(user_id, None)
                try:
                    if 'telethon_client' in locals():
                        await telethon_client.disconnect()
                except:
                    pass
                
        elif step == 'otp':
            otp_raw = message.text.strip()
            
            # More flexible OTP format handling
            if not re.fullmatch(r'[0-9\s]{5,10}', otp_raw):
                state['retry_count'] = state.get('retry_count', 0) + 1
                if state['retry_count'] >= 3:
                    await message.reply("âŒ Too many invalid attempts. Please restart with /login.")
                    login_states.pop(user_id, None)
                    return
                await message.reply("âŒ Invalid OTP format. Please enter exactly 5 digits (e.g., '12345' or '1 2 3 4 5'):")
                return
                
            otp = re.sub(r'\s+', '', otp_raw)
            telethon_client = state.get('telethon_client')
            
            if not telethon_client:
                await message.reply("âŒ Session expired. Please restart with /login.")
                login_states.pop(user_id, None)
                return
                
            try:
                phone = state['phone']
                sent = state.get('sent')
                if not sent:
                    raise Exception("Session data missing")
                    
                await telethon_client.sign_in(phone=phone, code=otp, phone_code_hash=sent.phone_code_hash)
                state['step'] = 'group_id'
                state['retry_count'] = 0  # Reset retry counter
                await message.reply(
                    f"âœ… Verification successful!\n"
                    f"Now, please provide the group ID where you want to use this account.\n"
                    "You can get the group ID by adding @username_to_id_bot to the group and sending /id"
                )
                
            except SessionPasswordNeededError:
                state['step'] = 'password'
                await message.reply("ðŸ”’ Please enter your 2FA password:")
                
            except Exception as e:
                error_msg = str(e).lower()
                if 'code' in error_msg or 'invalid' in error_msg:
                    state['retry_count'] = state.get('retry_count', 0) + 1
                    if state['retry_count'] >= 3:
                        await message.reply("âŒ Too many failed attempts. Please restart with /login.")
                        login_states.pop(user_id, None)
                        try:
                            await telethon_client.disconnect()
                        except:
                            pass
                        return
                    await message.reply(f"âŒ Invalid code. Please try again ({state['retry_count']}/3):")
                else:
                    await message.reply(f"âŒ Error: {e}\nPlease restart with /login.")
                    login_states.pop(user_id, None)
                    try:
                        await telethon_client.disconnect()
                    except:
                        pass
                        
        elif step == 'password':
            password = message.text.strip()
            telethon_client = state.get('telethon_client')
            
            if not telethon_client:
                await message.reply("âŒ Session expired. Please restart with /login.")
                login_states.pop(user_id, None)
                return
                
            try:
                if not password or password == '.':
                    await message.reply("âŒ 2FA password is required. Please enter your password:")
                    return
                    
                await telethon_client.sign_in(password=password)
                state['step'] = 'group_id'
                state['retry_count'] = 0  # Reset retry counter
                await message.reply(
                    f"âœ… 2FA verified!\n"
                    f"Now, please provide the group ID where you want to use {state['phone']}.\n"
                    "You can get the group ID by adding @username_to_id_bot to the group and sending /id"
                )
                
            except Exception as e:
                error_msg = str(e).lower()
                if 'password' in error_msg or 'invalid' in error_msg:
                    state['retry_count'] = state.get('retry_count', 0) + 1
                    if state['retry_count'] >= 3:
                        await message.reply("âŒ Too many failed attempts. Please restart with /login.")
                        login_states.pop(user_id, None)
                        try:
                            await telethon_client.disconnect()
                        except:
                            pass
                        return
                    await message.reply(f"âŒ Incorrect password. Please try again ({state['retry_count']}/3):")
                else:
                    await message.reply(f"âŒ Error: {e}\nPlease restart with /login.")
                    login_states.pop(user_id, None)
                    try:
                        await telethon_client.disconnect()
                    except:
                        pass
                        
        elif step == 'group_id':
            try:
                group_id = int(message.text.strip())
                telethon_client = state.get('telethon_client')
                
                if not telethon_client:
                    await message.reply("âŒ Session expired. Please restart with /login.")
                    login_states.pop(user_id, None)
                    return
                
                # Test if we can access the group
                try:
                    chat = await telethon_client.get_entity(group_id)
                    if not chat:
                        raise Exception("Group not found")
                    
                    # Check if bot is admin in the group
                    try:
                        me = await telethon_client.get_me()
                        participant = await telethon_client.get_permissions(group_id, me)
                        if not (participant.is_admin or participant.is_creator):
                            raise Exception("Bot must be an admin in the group")
                    except Exception as e:
                        await message.reply(
                            "âŒ I need to be an admin in the group to function properly. "
                            "Please make me an admin and try again with the group ID:"
                        )
                        return
                except Exception as e:
                    await message.reply(f"âŒ Cannot access group {group_id}. Please make sure the bot is added to the group and the group ID is correct.")
                    return
                
                # Save the session
                session_string = telethon_client.session.save()
                
                # Check if account already exists
                existing_accounts = db.get_accounts(ADMIN_USER_ID)
                for acc in existing_accounts:
                    if acc[1] == state['phone']:
                        db.remove_account(acc[0])
                
                # Add new account
                db.add_account(ADMIN_USER_ID, state['phone'], group_id, 0, session_string)
                
                await message.reply(
                    f"âœ… Successfully logged in!\n"
                    f"ðŸ“± Account: {state['phone']}\n"
                    f"ðŸ‘¥ Group: {chat.title if hasattr(chat, 'title') else group_id}\n\n"
                    "You can now use /startall to begin guessing!"
                )
                
            except ValueError:
                await message.reply("âŒ Invalid group ID. Please enter a valid numeric group ID:")
                return
            except Exception as e:
                await message.reply(f"âŒ Error: {e}\nPlease try again with a valid group ID:")
                return
            finally:
                try:
                    if 'telethon_client' in state:
                        await state['telethon_client'].disconnect()
                except:
                    pass
                login_states.pop(user_id, None)
                
    except Exception as e:
        await message.reply(f"âŒ An unexpected error occurred: {e}\nPlease try again with /login.")
        login_states.pop(user_id, None)
        try:
            if 'telethon_client' in state:
                await state['telethon_client'].disconnect()
        except:
            pass

@app.on_message(filters.command('accounts'))
@authorized_only
async def accounts_cmd(client, message: Message):
    accounts = db.get_accounts(ADMIN_USER_ID)
    if not accounts:
        await message.reply("No accounts found.")
        return
    msg = "<b>Accounts:</b>\n"
    for acc in accounts:
        msg += f"â€¢ <b>Phone:</b> {acc[1]} | <b>Chat ID:</b> {acc[2]} | <b>Active:</b> {'âœ”' if acc[5] else 'âŒ'}\n"
    await message.reply(msg, parse_mode=enums.ParseMode.HTML)

@app.on_message(filters.command('logout'))
@authorized_only
async def logout_cmd(client, message: Message):
    """Log out and remove a specific account."""
        
    args = message.text.split()
    if len(args) < 2:
        await message.reply("âŒ Usage: /logout <phone>")
        return
        
    phone = args[1].strip()
    accounts = db.get_accounts(ADMIN_USER_ID)
    
    # Check if the account is currently active in guessing tasks
    global account_tasks, account_clients
    if phone in account_tasks:
        try:
            # Cancel the task
            account_tasks[phone].cancel()
            # Remove from active tasks
            del account_tasks[phone]
            # Disconnect the client if it exists
            if phone in account_clients:
                try:
                    if account_clients[phone].is_connected():
                        await account_clients[phone].disconnect()
                    del account_clients[phone]
                except Exception as e:
                    print(f"Error disconnecting client {phone}: {e}")
        except Exception as e:
            print(f"Error stopping task for {phone}: {e}")
    
    # Remove from database
    removed = False
    for acc in accounts:
        if acc[1] == phone:
            db.remove_account(acc[0])
            removed = True
            break
    
    if removed:
        await message.reply(f"âœ… Successfully logged out and removed account: {phone}")
    else:
        await message.reply(f"âŒ No account found with phone: {phone}")

@app.on_message(filters.command('remove'))
@authorized_only
async def remove_cmd(client, message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("Usage: /remove <phone>")
        return
    phone = args[1]
    accounts = db.get_accounts(ADMIN_USER_ID)
    for acc in accounts:
        if acc[1] == phone:
            db.remove_account(acc[0])
            await message.reply(f"âœ… Removed account {phone}.")
            return
    await message.reply(f"âŒ No account found for {phone}.")

async def get_account_clients():
    global account_clients
    # Clear any disconnected clients
    account_clients = {k: v for k, v in account_clients.items() if hasattr(v, 'is_connected') and v.is_connected()}
    
    # Initialize any new accounts
    accounts = db.get_accounts(ADMIN_USER_ID)
    for acc in accounts:
        phone, session_string = acc[1], acc[4]
        if phone not in account_clients:
            try:
                client = TClient(StringSession(session_string), API_ID, API_HASH)
                await client.connect()
                if not await client.is_user_authorized():
                    await client.start()
                account_clients[phone] = client
            except Exception as e:
                print(f"Failed to start client for {phone}: {str(e)}")
    
    return account_clients

async def log_message(chat_id, msg):
    """Helper function to log messages to console and admin."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    log_msg = f"[{timestamp}] [Chat {chat_id}] {msg}"
    print(log_msg)
    try:
        await app.send_message(ADMIN_USER_ID, log_msg)
    except:
        pass

async def guessing_logic(client, chat_id, phone):
    """Main guessing logic for the Pokemon guessing game."""
    # Variables to track response and retries
    last_guess_time = 0
    guess_timeout = 15  # Time to wait for a response after /guess
    pending_guess = False  # Track if waiting for a response
    retry_lock = asyncio.Lock()  # Prevent concurrent retries

    async def send_guess_command():
        """Send the /guess command and track the time."""
        nonlocal last_guess_time, pending_guess
        try:
            await client.send_message(entity=chat_id, message='/guess')
            await log_message(chat_id, "Sent /guess command.")
            last_guess_time = time.time()
            pending_guess = True  # Mark as awaiting response
            return True
        except Exception as e:
            error_msg = f"Error in sending /guess: {e}"
            await log_message(chat_id, error_msg)
            return False

    # Detect "Who's that PokÃ©mon?" game logic and respond
    @client.on(events.NewMessage(chats=chat_id, pattern="Who's that pokemon", incoming=True))
    async def guess_pokemon(event):
        nonlocal pending_guess
        try:
            pending_guess = False  # Reset pending status on valid response
            
            # Check if message has photo
            if hasattr(event.message, 'photo') and event.message.photo:
                for size in event.message.photo.sizes:
                    if isinstance(size, PhotoStrippedSize):
                        size_str = str(size)
                        # Check cache for matching PokÃ©mon
                        cache_dir = "cache"
                        if os.path.exists(cache_dir):
                            for file in os.listdir(cache_dir):
                                if file.endswith('.txt'):
                                    with open(os.path.join(cache_dir, file), 'r') as f:
                                        file_content = f.read()
                                    if file_content == size_str:
                                        pokemon_name = file.split(".txt")[0]
                                        await client.send_message(chat_id, f"{pokemon_name}")
                                        await log_message(chat_id, f"Guessed: {pokemon_name}")
                                        await asyncio.sleep(10)
                                        await send_guess_command()
                                        return
                        
                        # Cache the size for new PokÃ©mon
                        os.makedirs("saitama", exist_ok=True)
                        with open("saitama/cache.txt", 'w') as file:
                            file.write(size_str)
                        await log_message(chat_id, "New PokÃ©mon detected, cached photo signature")
            
        except Exception as e:
            await log_message(chat_id, f"Error in guessing PokÃ©mon: {e}")

    # Save PokÃ©mon data when the game reveals the answer
    @client.on(events.NewMessage(chats=chat_id, pattern="The pokemon was", incoming=True))
    async def save_pokemon(event):
        
     
       nonlocal pending_guess
       try:
           pending_guess = False  # Reset pending status
           
           # Extract PokÃ©mon name
           message_text = event.message.text or ''
           pokemon_name = None
           
           # Try different patterns to extract PokÃ©mon name
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
               await log_message(chat_id, f"The PokÃ©mon was: {pokemon_name}")
               
               # Check if we have a cached photo for this PokÃ©mon
               if os.path.exists("saitama/cache.txt"):
                   try:
                       with open("saitama/cache.txt", 'r') as inf:
                           cont = inf.read().strip()
                       
                       if cont:
                           # Save to cache with PokÃ©mon name
                           cache_dir = "cache"
                           os.makedirs(cache_dir, exist_ok=True)
                           cache_path = os.path.join(cache_dir, f"{pokemon_name.lower()}.txt")
                           
                           with open(cache_path, 'w') as file:
                               file.write(cont)
                           
                           await log_message(chat_id, f"Saved {pokemon_name} to cache")
                           
                           # Clean up temporary cache
                           try:
                               os.remove("saitama/cache.txt")
                           except:
                               pass
                   
                   except Exception as e:
                       await log_message(chat_id, f"Error processing cache file: {e}")
           
           # Always restart guessing after a few seconds, regardless of reward
           await log_message(chat_id, "Pokemon revealed, restarting guessing in 2 seconds")
           await asyncio.sleep(2)
           await send_guess_command()
               
       except Exception as e:
           await log_message(chat_id, f"Error in saving PokÃ©mon data: {e}")

    # Handle "There is already a guessing game being played" message
    @client.on(events.NewMessage(chats=chat_id, pattern="There is already a guessing game being played", incoming=True))
    async def handle_active_game(event):
        nonlocal pending_guess
        await log_message(chat_id, "Game already active. Retrying shortly...")
        pending_guess = False
        await asyncio.sleep(5)  # Wait 10 seconds before retrying
        await send_guess_command()

    # Function to monitor bot behavior and retry if no response
    async def monitor_responses():
        nonlocal last_guess_time, pending_guess
        while True:
            try:
                async with retry_lock:  # Prevent multiple retries
                    # Retry if no response within the timeout period
                    if pending_guess and (time.time() - last_guess_time > guess_timeout):
                        await log_message(chat_id, "No response detected after /guess. Retrying...")
                        await send_guess_command()
                await asyncio.sleep(4)  # Check every 6 seconds
            except Exception as e:
                await log_message(chat_id, f"Error in monitoring responses: {e}")
                await asyncio.sleep(4)

    # Start the main guessing process
    try:
        await log_message(chat_id, f"Starting guessing logic for phone: {phone}")
        
        # Ensure connection
        if not client.is_connected():
            await client.connect()
        
        # Start monitoring and guessing
        monitor_task = asyncio.create_task(monitor_responses())
        await send_guess_command()
        
        # Keep the task running
        while True:
            await asyncio.sleep(3600)  # Sleep for a long time
            
    except asyncio.CancelledError:
        await log_message(chat_id, "Guessing task was cancelled")
    except Exception as e:
        await log_message(chat_id, f"Error in guessing loop: {e}")
    finally:
        # Clean up
        if 'monitor_task' in locals():
            monitor_task.cancel()
            try:
                await monitor_task
            except:
                pass

async def is_task_running(task):
    """Check if an asyncio task is still running."""
    if task is None:
        return False
    return not (task.done() or task.cancelled())

@app.on_message(filters.command('startall'))
@authorized_only
async def startall_cmd(client, message: Message):
    """Start the guessing process for all accounts."""
    try:
        accounts = db.get_accounts(ADMIN_USER_ID)
        if not accounts:
            await message.reply("No accounts found. Use /login to add an account first.")
            return
        
        global account_tasks
        account_clients = await get_account_clients()
        started_count = 0
        errors = []
        
        for acc in accounts:
            phone, chat_id, acc_id = acc[1], acc[2], acc[0]
            try:
                # Skip if already running
                if phone in account_tasks and await is_task_running(account_tasks[phone]):
                    continue
                    
                if phone in account_clients:
                    client = account_clients[phone]
                    
                    # Connect client if not connected
                    try:
                        if not client.is_connected():
                            await client.connect()
                            
                        # Check authorization
                        if not await client.is_user_authorized():
                            errors.append(f"âŒ Account {phone} not authorized. Please log in again.")
                            continue
                            
                    except Exception as e:
                        errors.append(f"âŒ Failed to connect {phone}: {str(e)}")
                        continue
                    
                    # Create and store the task
                    try:
                        # Cancel existing task if it exists
                        if phone in account_tasks:
                            task = account_tasks[phone]
                            if not task.done():
                                task.cancel()
                                try:
                                    await task
                                except asyncio.CancelledError:
                                    pass
                                
                        task = asyncio.create_task(guessing_logic(client, chat_id, phone))
                        account_tasks[phone] = task
                        db.set_account_active(acc_id, 1)
                        started_count += 1
                        await log_message(chat_id, f"Started guessing for {phone}")
                        
                        # Add a small delay between starting accounts
                        await asyncio.sleep(1)
                        
                    except Exception as e:
                        errors.append(f"âŒ Error starting {phone}: {str(e)}")
                        if phone in account_tasks:
                            del account_tasks[phone]
                
            except Exception as e:
                error_msg = f"âŒ Error processing {phone}: {str(e)}"
                errors.append(error_msg)
                await log_message(chat_id, error_msg)
        
        # Send final status
        status_msg = []
        if started_count > 0:
            status_msg.append(f"âœ… Started guessing for {started_count} account{'s' if started_count != 1 else ''}.")
        if errors:
            status_msg.append("\nErrors:" + "\nâ€¢ ".join([""] + errors))
        
        if not status_msg:
            status_msg.append("âŒ No accounts were started. All accounts might already be running.")
            
        await message.reply("\n".join(status_msg))
        
    except Exception as e:
        error_msg = f"âŒ Error in startall_cmd: {str(e)}"
        print(error_msg)
        await message.reply(error_msg)

@app.on_message(filters.command('stopall'))
@authorized_only
async def stopall_cmd(client, message: Message):
    """Stop all running guessing tasks."""
    try:
        global account_tasks
        stopped = 0
        errors = []
        
        for phone, task in list(account_tasks.items()):
            try:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    
                # Update database
                accs = db.get_accounts(ADMIN_USER_ID)
                for acc in accs:
                    if acc[1] == phone:
                        db.set_account_active(acc[0], 0)
                        stopped += 1
                        break
                        
            except Exception as e:
                errors.append(f"âŒ Error stopping {phone}: {str(e)}")
            finally:
                # Clean up client connection
                if phone in account_clients and account_clients[phone].is_connected():
                    await account_clients[phone].disconnect()
                del account_tasks[phone]
        
        # Send status
        status_msg = [f"â¹ï¸ Stopped guessing for {stopped} accounts."]
        if errors:
            status_msg.append("\nErrors:" + "\nâ€¢ ".join([""] + errors))
            
        await message.reply("\n".join(status_msg))
        
    except Exception as e:
        error_msg = f"âŒ Error in stopall_cmd: {str(e)}"
        print(error_msg)
        await message.reply(error_msg)

@app.on_message(filters.command('status'))
@authorized_only
async def status_cmd(client, message: Message):
    accounts = db.get_accounts(ADMIN_USER_ID)
    if not accounts:
        await message.reply("No accounts found.")
        return
    
    global account_tasks
    msg = "<b>Status:</b>\n"
    for acc in accounts:
        phone = acc[1]
        is_running = phone in account_tasks and not account_tasks[phone].done()
        msg += f"â€¢ <b>Phone:</b> {phone} | <b>Active:</b> {'âœ”' if is_running else 'âŒ'}\n"
    
    await message.reply(msg, parse_mode=enums.ParseMode.HTML)

if __name__ == "__main__":
    # Create necessary directories
    os.makedirs("cache", exist_ok=True)
    os.makedirs("saitama", exist_ok=True)
    
    print("Starting bot...")
    app.run()
    print("Bot stopped.")