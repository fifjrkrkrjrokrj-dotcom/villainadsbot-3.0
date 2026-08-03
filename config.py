import os
from dotenv import load_dotenv

load_dotenv()

# Bot Name Configuration
BOT_NAME = os.getenv("BOT_NAME", "𝗫𝗧𝗥 𝗔𝗗 𝗕𝗢𝗧")
DEFAULT_DB_NAME = "xtr_ad_bot"

# Telegram API credentials for userbots
api_id_val = os.getenv("API_ID", "")
API_ID = int(api_id_val) if api_id_val.strip().isdigit() else 0
API_HASH = os.getenv("API_HASH", "")

# Bot token for the main manager bot
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# MongoDB connection URI
MONGODB_URI = os.getenv("MONGODB_URI", "")

# Parse original admin IDs (which can be whitelisted using ORIGINAL_ADMIN_IDS or OWNER_ID)
original_admin_ids_str = os.getenv("ORIGINAL_ADMIN_IDS") or os.getenv("original_admin_ids") or os.getenv("OWNER_ID") or os.getenv("owner_id") or ""
ORIGINAL_ADMIN_IDS = set()
if original_admin_ids_str:
    for x in original_admin_ids_str.split(","):
        x = x.strip().replace('"', '').replace("'", "")
        if x.isdigit():
            ORIGINAL_ADMIN_IDS.add(int(x))

# Directory for local session files
USER_DATA_DIR = "user_data"

# Limit the maximum number of running userbots to prevent Out of Memory (OOM) on platforms like Railway
MAX_RUNNING_USERBOTS = int(os.getenv("MAX_RUNNING_USERBOTS", "99999"))

# Gmail credentials for auto-approval
GMAIL_USER = os.getenv("GMAIL_USER", "ashishchoudharyrj21@gmail.com")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS", "nsbh dkqi jqil wwuw")
FAMAPP_EMAILS = [x.strip() for x in os.getenv("FAMAPP_EMAILS", "no-reply@famapp.in").split(",") if x.strip()]

# Default values for global settings
DEFAULT_GLOBAL_SETTINGS = {
    "price_per_id": float(os.getenv("PRICE_PER_ID", "10.0")),            # Global price per extra ID
    "force_join_links": [x.strip() for x in os.getenv("FORCE_JOIN_LINKS", "https://t.me/TheVillainActive,https://t.me/+WzyoJkg4bzhlNTFl").split(",") if x.strip()], # List of usernames/links to force join
    "log_group_id": int(os.getenv("LOG_GROUP_ID", "-1004354441869")),   # Log group/channel ID
    "branding_username": os.getenv("BRANDING_USERNAME", None),        # Bot username to append (e.g. via @MyBot)
    "branding_duration": int(os.getenv("BRANDING_DURATION", "30")),          # Duration of branding in days
    "branding_name_enabled": os.getenv("BRANDING_NAME_ENABLED", "True").lower() == "true",    # Enable name branding
    "branding_bio_enabled": os.getenv("BRANDING_BIO_ENABLED", "True").lower() == "true",     # Enable bio branding
    "branding_name_text": os.getenv("BRANDING_NAME_TEXT", None),       # Custom name branding suffix text
    "branding_bio_text": os.getenv("BRANDING_BIO_TEXT", None),        # Custom bio branding suffix text
    "start_image": os.getenv("START_IMAGE", "https://files.catbox.moe/jnlroe.jpg"),              # File ID of the start image
    "ping_image": os.getenv("PING_IMAGE", "https://files.catbox.moe/7qgokb.jpg"),               # File ID of the ping image
    "help_image": os.getenv("HELP_IMAGE", "https://files.catbox.moe/xxpn14.jpg"),               # File ID of the help image
    "admins": list(ORIGINAL_ADMIN_IDS), # List of admins
    "gpt_api_key": os.getenv("GPT_API_KEY", None),               # Global OpenAI API Key for GPT mode (optional)
    "maintenance_mode": os.getenv("MAINTENANCE_MODE", "False").lower() == "true",         # Maintenance guard
    "upi_id": os.getenv("UPI_ID", "raunitkumar01@fam"),          # Admin UPI ID for payments
    "usdt_bep20_address": os.getenv("USDT_BEP20_ADDRESS", "0x0000000000000000000000000000000000000000"), # USDT BEP20 Address
    "ton_address": os.getenv("TON_ADDRESS", "UQ000000000000000000000000000000000000000000000000"), # TON Address
    "support_channel": os.getenv("SUPPORT_CHANNEL") or os.getenv("support_channel") or "https://t.me/TheVillainActive",                 # Support channel invite link
    "support_group": os.getenv("SUPPORT_GROUP") or os.getenv("support_group") or "https://t.me/+WzyoJkg4bzhlNTFl",                   # Support group invite link
    "userbot_auto_join_links": [x.strip() for x in os.getenv("USERBOT_AUTO_JOIN_LINKS", "https://t.me/TheVillainActive,https://t.me/+WzyoJkg4bzhlNTFl").split(",") if x.strip()], # Auto-join links for new userbots
    "referral_commission": float(os.getenv("REFERRAL_COMMISSION", "0.10")),        # 10% commission on slot upgrades
    "subscription_plans": [             # Dynamic slot subscription plans
        {"id": "std30", "days": 30, "price": float(os.getenv("PRICE_PER_ID", "10.0")), "button_name": "Standard 30 Days"}
    ]
}

# Owner Button Configuration (Read from Env)
OWNER_1_NAME = os.getenv("OWNER_1_NAME") or os.getenv("owner_1_name") or "👑 Owner 1"
OWNER_1_URL = os.getenv("OWNER_1_URL") or os.getenv("owner_1_url") or "https://t.me/v90001"
OWNER_2_NAME = os.getenv("OWNER_2_NAME") or os.getenv("owner_2_name") or "👑 Owner 2"
OWNER_2_URL = os.getenv("OWNER_2_URL") or os.getenv("owner_2_url") or "https://t.me/BL4ZEXSOUL"
