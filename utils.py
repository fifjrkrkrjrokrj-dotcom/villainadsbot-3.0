import os
import logging
from typing import Optional
from telethon import Button
import config
import database
from translations import TRANSLATIONS

logger = logging.getLogger(__name__)

def styled_button(text: str, callback_data: str, style: str = "primary"):
    """
    Creates an inline button. Handles cases where the current Telethon library version
    does not support a 'style' parameter natively in Button.inline by attaching it as an attribute.
    """
    try:
        return Button.inline(text, data=callback_data, style=style)
    except TypeError:
        # Standard Telethon fallback
        btn = Button.inline(text, data=callback_data)
        setattr(btn, "style", style)
        return btn

def get_text(key: str, lang: Optional[str] = "en", **kwargs) -> str:
    """
    Retrieves the localized text for the given key and language.
    Falls back to English if the translation is missing.
    """
    lang = lang or "en"
    if lang not in TRANSLATIONS:
        lang = "en"
        
    text = TRANSLATIONS[lang].get(key) or TRANSLATIONS["en"].get(key)
    if text is None:
        return f"[{key}]"
        
    try:
        return text.format(**kwargs)
    except Exception as e:
        logger.error(f"Formatting error for translation key '{key}': {e}")
        return text

async def show_help(event, action_key: str, user_id: int) -> bool:
    """
    Displays the 'How to Use' hint for a button click.
    Uses event.answer(text, alert=...) to present a popup to the user.
    """
    user = database.get_user(user_id)
    lang = user.get("language") if user else "en"
    
    # Form the translation key
    help_key = f"help_{action_key}"
    hint = get_text(help_key, lang)
    
    # Fallback to generic message if missing
    if hint.startswith(f"[help_"):
        return False
        
    # Determine alert level: alert=True for risky/important actions, False for subtle
    show_alert = False
    risky_keywords = ["delete", "stop", "reject", "remove", "danger", "cancel"]
    if any(k in action_key.lower() for k in risky_keywords):
        show_alert = True
        
    try:
        await event.answer(hint, alert=show_alert)
        return True
    except Exception as e:
        logger.error(f"Failed to show callback help alert: {e}")
        return False

def ensure_user_dir(user_id: int) -> str:
    """
    Ensures that the user's sessions directory exists locally and returns the path.
    """
    path = os.path.join(config.USER_DATA_DIR, str(user_id), "sessions")
    os.makedirs(path, exist_ok=True)
    return path

import time

# In-memory cache for force subscribe checks
# Format: {user_id: (expiry_timestamp, not_joined_list)}
_force_sub_cache = {}

async def check_force_sub(client, user_id: int) -> list:
    """
    Checks if a user is subscribed to all force subscribe channels defined by admin.
    Caches successful checks to make the bot faster and prevent rate limits.
    """
    now = time.time()
    
    # Check cache
    if user_id in _force_sub_cache:
        expiry, cached_not_joined = _force_sub_cache[user_id]
        if now < expiry:
            return cached_not_joined
            
    from telethon.tl.functions.channels import GetParticipantRequest
    from telethon.errors import UserNotParticipantError
    
    not_joined = []
    channels = database.get_force_channels()
    for ch in channels:
        ch_id = ch["channel_id"]
        try:
            peer = int(ch_id) if (ch_id.startswith("-") or ch_id.isdigit()) else ch_id
            await client(GetParticipantRequest(
                channel=peer,
                participant=user_id
            ))
        except (UserNotParticipantError, Exception) as e:
            logger.debug(f"User {user_id} not in channel {ch_id}: {e}")
            not_joined.append(ch)
            
    # Cache the result to reduce Telegram API calls and improve latency.
    # If they joined everything, cache it for 300 seconds (5 minutes).
    # If they are missing some channels, cache it for 60 seconds (1 minute).
    cache_duration = 300 if not not_joined else 60
    _force_sub_cache[user_id] = (now + cache_duration, not_joined)
    
    return not_joined

async def send_force_sub_msg(event, not_joined: list, lang: str):
    """
    Sends a restricted-access message with URLs to join and a verify button.
    """
    buttons = []
    for i, ch in enumerate(not_joined, 1):
        label = ch.get("channel_name") or f"Channel {i}"
        link = ch.get("channel_link")
        buttons.append([Button.url(f"➕ Join {label}", url=link)])
        
    buttons.append([styled_button("✅ I've Joined — Verify", "verify_sub", style="success")])
    
    lines = [
        "⚠️ **Access Restricted**\n━━━━━━━━━━━━━━━━━━━━\nJoin these channels to use the bot:\n"
    ]
    for ch in not_joined:
        lines.append(f"• {ch.get('channel_name') or ch.get('channel_id')}")
    lines.append("\n━━━━━━━━━━━━━━━━━━━━\n_Tap Verify after joining._")
    
    await event.respond("\n".join(lines), buttons=buttons)

async def guard(event, client) -> bool:
    """
    Applies security filters to commands/callbacks: bans, maintenance mode, and force subs.
    Returns True if execution should be blocked.
    """
    user_id = event.sender_id
    if not user_id:
        return False
        
    user = database.get_user(user_id)
    if not user:
        import models
        user = models.create_default_user(user_id)
        database.save_user(user)
        
    # 1. Ban Guard
    if user.get("is_banned", False):
        txt = "🚫 You are banned from using this bot."
        if hasattr(event, "answer"):
            try:
                await event.answer(txt, alert=True)
            except Exception:
                await event.respond(txt)
        else:
            await event.respond(txt)
        return True
        
    # 2. Maintenance Guard
    global_settings = database.get_global_settings()
    admins = global_settings.get("admins", [])
    is_admin = user_id in admins or user_id in config.ORIGINAL_ADMIN_IDS
    
    if global_settings.get("maintenance_mode", False) and not is_admin:
        txt = "🔧 Bot is under maintenance. Please try again later."
        if hasattr(event, "answer"):
            try:
                await event.answer(txt, alert=True)
            except Exception:
                await event.respond(txt)
        else:
            await event.respond(txt)
        return True
        
    # 3. Force Subscribe Guard
    if not is_admin:
        not_joined = await check_force_sub(client, user_id)
        if not_joined:
            await send_force_sub_msg(event, not_joined, user.get("language", "en") or "en")
            return True
            
    return False

def get_allowed_slots(user_id: int) -> int:
    """
    Calculates the user's allowed bot slots dynamically based on active subscriptions.
    Automatically cleans up expired subscriptions.
    """
    user = database.get_user(user_id)
    if not user:
        return 1
        
    import time
    now = time.time()
    subs = user.get("subscriptions", [])
    active_subs = []
    active_slots = 0
    
    for sub in subs:
        if sub.get("expires_at", 0) > now:
            active_slots += sub.get("qty", 0)
            active_subs.append(sub)
            
    # Save cleaned up list to DB if changes occurred
    if len(active_subs) != len(subs):
        user["subscriptions"] = active_subs
        database.save_user(user)
        
    return 1 + active_slots

def allocate_slots_subscription(user_id: int, qty: int, days: int, plan_name: str, payment_id: str) -> float:
    """
    Allocates slot subscription to a user and returns the expires_at timestamp.
    """
    user = database.get_user(user_id)
    if not user:
        return 0.0
        
    import time
    subs = user.setdefault("subscriptions", [])
    expires_at = time.time() + (days * 86400)
    
    subs.append({
        "subscription_id": payment_id,
        "qty": qty,
        "expires_at": expires_at,
        "plan_name": plan_name
    })
    
    database.save_user(user)
    return expires_at

# ─── TELETHON PATTERN MATCH MONKEYPATCH ──────────────────────────────────
# Intercepts Telethon's CallbackQuery.Event pattern_match and wraps it
# so that all matched groups are automatically decoded from bytes to str.
# This prevents binary/bytes data from corrupting JSON database files.
from telethon import events

class DecodedMatch:
    def __init__(self, original_match):
        self._match = original_match
        
    def group(self, *args):
        res = self._match.group(*args)
        if isinstance(res, bytes):
            return res.decode("utf-8")
        if isinstance(res, tuple):
            return tuple(x.decode("utf-8") if isinstance(x, bytes) else x for x in res)
        return res
        
    def groups(self, default=None):
        res = self._match.groups(default)
        return tuple(x.decode("utf-8") if isinstance(x, bytes) else x for x in res)
        
    def __getattr__(self, name):
        return getattr(self._match, name)

def get_pattern_match(self):
    orig = self.__dict__.get("pattern_match")
    if orig is not None:
        return DecodedMatch(orig)
    return None

def set_pattern_match(self, value):
    self.__dict__["pattern_match"] = value

events.CallbackQuery.Event.pattern_match = property(get_pattern_match, set_pattern_match)


# ─── BUTTON STYLING MONKEYPATCH ──────────────────────────────────────────
# Intercepts all inline keyboards sent by the bot (via send_message, edit_message, or send_file)
# and automatically styles inline buttons in a repeating pattern (danger -> primary -> success).
# Destructive buttons (stop, delete, cancel, reject) remain danger (red), and
# constructive buttons (start, approve, accept) remain success (green).

def style_keyboard(buttons):
    if not buttons:
        return buttons
        
    # Standardize buttons list structure (Telethon accepts single button, 1D row, or 2D grid)
    is_2d = True
    if not isinstance(buttons, (list, tuple)):
        grid = [[buttons]]
        is_2d = False
    elif len(buttons) > 0 and not isinstance(buttons[0], (list, tuple)):
        grid = [list(buttons)]
        is_2d = False
    else:
        grid = [list(row) for row in buttons]
        
    # Style pattern loop: danger -> primary -> success
    styles = ["danger", "primary", "success"]
    
    for row_idx, row in enumerate(grid):
        # Determine the color style for this row based on its row index
        style = styles[row_idx % len(styles)]
        
        for btn in row:
            # Check if it is a button (has text attribute)
            if hasattr(btn, "text"):
                # Assign the style using KeyboardButtonStyle if supported by Telethon
                try:
                    from telethon.tl.types import KeyboardButtonStyle
                    # Preserve existing icon if present
                    icon = None
                    if hasattr(btn, "style") and btn.style and hasattr(btn.style, "icon"):
                        icon = btn.style.icon
                    
                    if style == "primary":
                        btn.style = KeyboardButtonStyle(bg_primary=True, icon=icon)
                    elif style == "danger":
                        btn.style = KeyboardButtonStyle(bg_danger=True, icon=icon)
                    elif style == "success":
                        btn.style = KeyboardButtonStyle(bg_success=True, icon=icon)
                    else:
                        btn.style = None
                except (ImportError, AttributeError, TypeError):
                    # Backward compatibility for older Telethon versions where style is a custom attribute string
                    try:
                        btn.style = style
                    except AttributeError:
                        setattr(btn, "style", style)
                        
    if not is_2d:
        if not isinstance(buttons, (list, tuple)):
            return grid[0][0]
        else:
            return grid[0]
    return grid

from telethon import TelegramClient

orig_send_message = TelegramClient.send_message
orig_edit_message = TelegramClient.edit_message
orig_send_file = TelegramClient.send_file

async def patched_send_message(self, entity, message=None, *args, **kwargs):
    if "buttons" in kwargs:
        kwargs["buttons"] = style_keyboard(kwargs["buttons"])
    return await orig_send_message(self, entity, message, *args, **kwargs)

async def patched_edit_message(self, entity, message=None, *args, **kwargs):
    if "buttons" in kwargs:
        kwargs["buttons"] = style_keyboard(kwargs["buttons"])
    return await orig_edit_message(self, entity, message, *args, **kwargs)

async def patched_send_file(self, entity, file, *args, **kwargs):
    if "buttons" in kwargs:
        kwargs["buttons"] = style_keyboard(kwargs["buttons"])
    return await orig_send_file(self, entity, file, *args, **kwargs)

TelegramClient.send_message = patched_send_message
TelegramClient.edit_message = patched_edit_message
TelegramClient.send_file = patched_send_file

def normalize_text(text: str) -> str:
    """
    Normalizes compatibility characters (such as mathematical bold/italic styled fonts)
    to their standard representation to bypass anti-spam filters.
    """
    return text

def parse_spintax(text: str) -> str:
    """
    Randomly chooses options in curly braces separated by vertical bars.
    Example: "{Hello|Hi|Hey} there" -> "Hi there"
    """
    import re
    import random
    if not isinstance(text, str) or not text:
        return text
    pattern = re.compile(r'\{([^{}]+)\}')
    while True:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split('|')
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
    return text

def make_message_unique(text: str) -> str:
    """
    Strips harmful zero-width unicode control characters that trigger Telegram server-side spambot filters.
    Returns clean text suitable for safe broadcasting.
    """
    if not isinstance(text, str) or not text:
        return text
    # Remove any zero-width characters (\u200b, \u200c, \u200d, \ufeff) that trigger Telegram anti-spam filters
    for zw in ['\u200b', '\u200c', '\u200d', '\ufeff']:
        text = text.replace(zw, '')
    return text


def get_device_profile(session_id: str) -> dict:
    """
    Generates a deterministic mobile/desktop device header profile based on the session ID.
    This ensures that the userbot session always connects with the same device model on every launch.
    """
    import hashlib
    if not session_id:
        return {"device_model": "Samsung SM-S908B", "system_version": "Android 13", "app_version": "9.6.1"}
        
    hasher = hashlib.md5(str(session_id).encode('utf-8'))
    digest = hasher.hexdigest()
    val = int(digest, 16)
    
    profiles = [
        {"device_model": "Samsung SM-S908B", "system_version": "Android 13", "app_version": "9.6.1"},
        {"device_model": "Xiaomi 12 Pro", "system_version": "Android 12", "app_version": "9.3.3"},
        {"device_model": "OnePlus 10 Pro", "system_version": "Android 13", "app_version": "9.5.4"},
        {"device_model": "Google Pixel 7 Pro", "system_version": "Android 13", "app_version": "9.4.2"},
        {"device_model": "iPhone 14 Pro", "system_version": "iOS 16.1", "app_version": "9.6.2"},
        {"device_model": "iPhone 13", "system_version": "iOS 15.7", "app_version": "9.2.1"},
        {"device_model": "Huawei P50 Pro", "system_version": "HarmonyOS 2.0", "app_version": "8.9.3"},
        {"device_model": "Samsung SM-G991B", "system_version": "Android 12", "app_version": "9.1.2"},
        {"device_model": "Redmi Note 11", "system_version": "Android 11", "app_version": "8.8.4"},
        {"device_model": "POCO F4 GT", "system_version": "Android 12", "app_version": "9.0.0"}
    ]
    
    return profiles[val % len(profiles)]

