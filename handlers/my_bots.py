import logging
import asyncio
import os
from typing import Optional, Set
from telethon import events, Button
import database
import models
import utils
import config
import userbot_manager
from userbot import join_vc_by_link, leave_chat_single, join_channel_single

logger = logging.getLogger(__name__)

# Global in-memory autoplay state per user (also referenced in callback handlers)
_np_autoplay_state = {}


def extract_media_info(msg):
    """
    Extracts file path, title, and duration from a message containing audio/voice/video.
    """
    media = msg.audio or msg.voice or msg.video or getattr(msg, "gif", None)
    if not media and msg.document:
        mime = getattr(msg.document, "mime_type", "")
        if mime.startswith("audio/") or mime.startswith("video/"):
            media = msg.document
            
    if not media:
        return None, None, 30
        
    title = "Uploaded Media"
    duration = 30
    
    # Extract title
    if msg.audio:
        title = getattr(msg.audio, "title", None) or getattr(msg.audio, "file_name", None) or "Audio File"
    elif msg.voice:
        title = "Voice Note"
    elif msg.video:
        title = getattr(msg.video, "file_name", None) or "Video File"
    elif msg.document:
        title = getattr(msg.document, "file_name", None) or "Document Media"
        
    # Extract duration
    for attr in getattr(media, "attributes", []):
        if hasattr(attr, "duration"):
            duration = attr.duration
            break
            
    return media, title, duration

import time

_last_progress_updates = {}

def download_progress_sync(current, total, msg_to_edit, operation_name="Downloading"):
    now = time.time()
    msg_id = id(msg_to_edit)
    last_time = _last_progress_updates.get(msg_id, 0.0)
    
    percent = (current / total) * 100 if total else 0.0
    
    # Only update at most once every 3.0 seconds to prevent Telegram Flood Wait
    if now - last_time < 3.0 and percent < 100.0:
        return
        
    _last_progress_updates[msg_id] = now
    
    # Define an async task to edit the message safely on the main loop
    async def _do_edit():
        filled = int(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)
        text = (
            f"📥 **{operation_name}...**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 Size: `{total / (1024*1024):.2f} MB`\n"
            f"📊 Progress: `[{bar}] {percent:.1f}%`"
        )
        try:
            await msg_to_edit.edit(text)
        except Exception:
            pass
            
    asyncio.create_task(_do_edit())

# In-memory dictionary containing active prompt states for user interaction
# Structure: { user_id: { "phone": str, "action": str } }
_bot_action_states = {}
_admin_impersonation = {}

def set_admin_impersonation(admin_id, target_id):
    _admin_impersonation[admin_id] = target_id


async def show_mock_dashboard(event, user_id: int, flash_message: Optional[str] = None):
    """
    Renders a mock UserBot control dashboard for unlogged users.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    text = ""
    if flash_message:
        text += f"{flash_message}\n\n"
        
    text += f"🤖 **UserBot Dashboard** (Demo Mode)\nStatus: 🔴 **Stopped / Not Logged In**\n\n__Choose an option below to manage this bot's services:__\n\n⚠️ **{utils.get_text('account_login_first', lang)}**"
    
    buttons = [
        [
            utils.styled_button("➕ Add / Login Bot", "menu_add_bot", style="success")
        ],
        [
            utils.styled_button(utils.get_text("btn_start_bot", lang), "no_login_start", style="success"),
            utils.styled_button(utils.get_text("btn_stop_bot", lang), "no_login_stop", style="danger")
        ],
        [
            utils.styled_button(utils.get_text("btn_set_broadcast", lang), "no_login_broadcast", style="primary"),
            utils.styled_button(utils.get_text("btn_set_welcome", lang), "no_login_welcome", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_toggle_spam", lang, state="❌ OFF"), "no_login_spam", style="primary"),
            utils.styled_button(utils.get_text("btn_toggle_welcome", lang, state="❌ OFF"), "no_login_welcome_toggle", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_clone_profile", lang), "no_login_clone", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_help", lang), "help_bot_no_login", style="primary"),
            utils.styled_button(utils.get_text("btn_how_to_use", lang), "how_to_use_no_login", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_change_name", lang), "no_login_name", style="primary"),
            utils.styled_button(utils.get_text("btn_set_interval", lang), "no_login_interval", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_refresh_stats", lang), "no_login_stats", style="primary"),
            utils.styled_button(utils.get_text("btn_delete_bot", lang), "no_login_delete", style="danger")
        ],
        [
            utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")
        ]
    ]
    
    try:
        if hasattr(event, "edit"):
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)

async def show_bots_list(event, user_id: int, flash_message: Optional[str] = None):
    """
    Renders the list of added accounts (UserBots) for the user.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    sessions = database.get_sessions(user_id)
    if not sessions:
        await show_mock_dashboard(event, user_id, flash_message)
        return
        
    text = ""
    if flash_message:
        text += f"{flash_message}\n\n"
        
    text += "📱 **Your Connected UserBots**:\n\n"
    buttons = [
        [
            utils.styled_button(utils.get_text("btn_all_slots", lang), "menu_all_slots", style="success")
        ]
    ]
    
    for s in sessions:
        phone = s.get("phone")
        # Sync status dynamically
        is_running = userbot_manager.is_bot_running(phone)
        status = "running" if is_running else "stopped"
        if s.get("status") != status:
            s["status"] = status
            database.save_session(s)
            
        status_emoji = "🟢" if status == "running" else "🔴"
        name = s.get("name") or "UserBot"
        username = s.get("username")
        user_display = f"@{username}" if username else phone
        
        text += f"{status_emoji} **{name}** ({user_display})\n"
        
        # Add a selection button for this bot
        buttons.append([
            utils.styled_button(
                f"{status_emoji} {name} ({user_display})", 
                f"select_bot_{phone}", 
                style="primary"
            )
        ])
        
    buttons.append([utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")])
    
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)

async def show_bot_dashboard(event, phone: str, user_id: int, flash_message: Optional[str] = None):
    """
    Displays the detailed control dashboard for a single UserBot.
    """
    try:
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        if not sess or str(sess.get("user_id")) != str(user_id):
            text = "❌ Session not found."
            if flash_message:
                text = f"{flash_message}\n\n" + text
            try:
                await event.edit(text)
            except Exception:
                await event.respond(text)
            return
            
        # Sync status dynamically with manager memory running state
        is_running = userbot_manager.is_bot_running(phone)
        status = "running" if is_running else "stopped"
        
        if sess.get("status") != status:
            sess["status"] = status
            database.save_session(sess)
            
        status_emoji = "🟢" if status == "running" else "🔴"
        status_text = "Running" if status == "running" else "Stopped"
        
        name = sess.get("name") or "UserBot"
        username = sess.get("username") or "None"
        
        settings = sess.get("settings", {})
        auto_spam = "✅ ON" if settings.get("auto_spam") else "❌ OFF"
        auto_welcome = "✅ ON" if settings.get("auto_welcome") else "❌ OFF"
        auto_reply = "✅ ON" if settings.get("auto_reply") else "❌ OFF"
        auto_add_contact = "✅ ON" if settings.get("auto_add_contact") else "❌ OFF"
        
        text = ""
        if flash_message:
            text += f"{flash_message}\n\n"
            
        text += utils.get_text(
            "bot_dashboard", 
            lang, 
            name=name, 
            username=username, 
            status_emoji=status_emoji, 
            status=status_text
        )
        text += f"\n💬 **Tag Auto-Reply**: {auto_reply}\n👥 **Auto-Contact**: {auto_add_contact}"
        
        # Configure dashboard buttons (Large full-width layout)
        buttons = []
        rows = []
        
        # Row 0: Start and Stop side-by-side
        rows.append([
            ("btn_start_bot", f"start_bot_{phone}"),
            ("btn_stop_bot", f"stop_bot_{phone}")
        ])
        
        # Row 0.5: Restart Bot
        rows.append([
            ("btn_restart_bot", f"restart_bot_{phone}")
        ])
            
        # Row 1: Set Broadcast Message (Full width)
        rows.append([
            ("btn_set_broadcast", f"set_broadcast_{phone}")
        ])

        # Row 1.2: Set DM Welcome Message (Full width)
        rows.append([
            ("btn_set_welcome", f"set_welcome_{phone}")
        ])
        
        # Row 1.5: Set Tag Auto-Reply Messages (Full width)
        rows.append([
            ("btn_set_auto_reply", f"set_auto_reply_{phone}")
        ])
        
        # Row 1.6: Set Run Timer (Full width)
        rows.append([
            ("⏱️ Set Run Timer", f"set_run_timer_{phone}")
        ])
        
        # Row 1.8: Voice Chat (VC) Menu
        rows.append([
            ("btn_vc_menu", f"vc_menu_{phone}")
        ])
        
        # Row 2: Auto Feature Toggles (Spam, Welcome, Tag Reply, Contact)
        rows.append([
            ("btn_toggle_spam", f"toggle_spam_{phone}", auto_spam),
            ("btn_toggle_welcome", f"toggle_welcome_{phone}", auto_welcome)
        ])
        rows.append([
            ("btn_toggle_auto_reply", f"toggle_reply_{phone}", auto_reply),
            ("btn_toggle_add_contact", f"toggle_add_contact_{phone}", auto_add_contact)
        ])
        
        # Row 3: Profile & Timing Settings
        rows.append([
            ("btn_clone_profile", f"clone_profile_{phone}"),
            ("btn_change_name", f"change_name_{phone}")
        ])
        rows.append([
            ("btn_set_interval", f"set_interval_{phone}")
        ])
        
        # Row 4: Help & Info
        rows.append([
            ("btn_help", f"help_bot_{phone}"),
            ("btn_how_to_use", f"how_to_use_{phone}")
        ])
        rows.append([
            ("btn_settings_info", f"view_settings_info_{phone}")
        ])
        
        # Row 5: Refresh Stats & Delete Bot
        rows.append([
            ("btn_refresh_stats", f"refresh_stats_{phone}")
        ])
        rows.append([
            ("btn_delete_bot", f"delete_bot_{phone}", None, "danger")
        ])
        
        # Row 6: Back to Bots
        rows.append([
            ("btn_back_to_bots", "menu_my_bots", None, "primary")
        ])


        styles = ["success", "danger", "primary"]
        for i, row in enumerate(rows):
            row_style = styles[i % len(styles)]
            row_buttons = []
            for item in row:
                key = item[0]
                callback = item[1]
                state = item[2] if len(item) > 2 else None
                override_style = item[3] if len(item) > 3 else None
                
                if key == "btn_start_bot":
                    style = "success"
                elif key in ("btn_stop_bot", "btn_delete_bot"):
                    style = "danger"
                elif key == "btn_restart_bot":
                    style = None
                elif override_style:
                    style = override_style
                else:
                    style = row_style
                    
                if key == "btn_vc_menu":
                    label = "🎙️ VC + GRP JOINING"
                elif state is not None:
                    label = utils.get_text(key, lang, state=state)
                else:
                    label = utils.get_text(key, lang)
                    
                row_buttons.append(utils.styled_button(label, callback, style=style))
            buttons.append(row_buttons)
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)
            
    except Exception as e:
        logger.exception("Error rendering bot dashboard")
        err_msg = f"❌ **Error rendering dashboard:** {e}"
        try:
            await event.edit(err_msg)
        except Exception:
            await event.respond(err_msg)

async def show_all_slots_dashboard(event, user_id: int, flash_message: Optional[str] = None, fetch_all: bool = False):
    """
    Renders the dashboard for controlling all userbots at once.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    if fetch_all:
        sessions = database.get_sessions(None)
    else:
        sessions = database.get_sessions(user_id)
        
    if not sessions:
        text = "⚠️ **All Slots Dashboard**\n\nNo connected UserBots found."
        buttons = [[utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")]]
        try:
            if hasattr(event, "edit"):
                await event.edit(text, buttons=buttons)
            else:
                await event.respond(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)
        return
        
    total_slots = len(sessions)
    running_bots = sum(1 for s in sessions if userbot_manager.is_bot_running(s["phone"]))
    stopped_bots = total_slots - running_bots
    
    any_spam_on = any(s.get("settings", {}).get("auto_spam", False) for s in sessions)
    any_welcome_on = any(s.get("settings", {}).get("auto_welcome", False) for s in sessions)
    any_reply_on = any(s.get("settings", {}).get("auto_reply", False) for s in sessions)
    any_add_contact_on = any(s.get("settings", {}).get("auto_add_contact", False) for s in sessions)
    
    spam_state_display = "🟢 ON" if any_spam_on else "🔴 OFF"
    welcome_state_display = "🟢 ON" if any_welcome_on else "🔴 OFF"
    reply_state_display = "🟢 ON" if any_reply_on else "🔴 OFF"
    add_contact_state_display = "🟢 ON" if any_add_contact_on else "🔴 OFF"
    
    text = ""
    if flash_message:
        text += f"{flash_message}\n\n"
        
    text += (
        f"👥 **All Slots Control Dashboard**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Overview**:\n"
        f"• Total Linked Bots: **{total_slots}**\n"
        f"• Running: **🟢 {running_bots}** | Stopped: **🔴 {stopped_bots}**\n"
        f"• Auto-Spam (All): **{spam_state_display}**\n"
        f"• Auto-Welcome (All): **{welcome_state_display}**\n"
        f"• Tag Auto-Reply (All): **{reply_state_display}**\n"
        f"• Auto-Contact (All): **{add_contact_state_display}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ _Control all your UserBots simultaneously from this panel._"
    )
    
    buttons = [
        # Row 0: Start All, Stop All
        [
            utils.styled_button("🟢 Start All", "all_slots_start", style="success"),
            utils.styled_button("🔴 Stop All", "all_slots_stop", style="danger")
        ],
        # Row 0.5: Restart All
        [
            utils.styled_button("🔄 Restart All UserBots", "all_slots_restart", style="primary")
        ],
        # Row 1: Set All Broadcast
        [
            utils.styled_button("✉️ Set All Broadcast Text", "all_slots_set_broadcast", style="primary")
        ],
        # Row 1.2: Set All Welcome & Multi-Welcome
        [
            utils.styled_button("👋 Set All Welcome", "all_slots_set_welcome", style="primary"),
            utils.styled_button("👋 Set All Multi-Welcome", "all_slots_set_multi_welcome", style="primary")
        ],
        # Row 1.5: Set All Tag Auto-Reply
        [
            utils.styled_button("💬 Set All Tag Auto-Reply", "all_slots_set_auto_reply", style="primary")
        ],
        # Row 1.6: Set All Run Timer
        [
            utils.styled_button("⏱️ Set Run Timer (All)", "all_slots_set_run_timer", style="primary")
        ],
        # Row 1.8: Voice Chat (VC) Menu (All)
        [
            utils.styled_button("🎙️ VC + GRP JOINING (All)", "all_slots_vc_menu", style="success")
        ],
        # Row 2: Auto Feature Toggles (All)
        [
            utils.styled_button(f"🔄 Auto-Spam (All): {spam_state_display}", "all_slots_toggle_spam", style="primary"),
            utils.styled_button(f"👋 Auto-Welcome (All): {welcome_state_display}", "all_slots_toggle_welcome", style="primary")
        ],
        [
            utils.styled_button(f"💬 Tag Auto-Reply (All): {reply_state_display}", "all_slots_toggle_reply", style="primary"),
            utils.styled_button(f"👥 Auto-Contact (All): {add_contact_state_display}", "all_slots_toggle_add_contact", style="primary")
        ],
        # Row 3: Clone Profile (All)
        [
            utils.styled_button("👤 Clone Profile (All)", "all_slots_clone_profile", style="primary"),
            utils.styled_button("✏️ Change Name (All)", "all_slots_change_name", style="primary")
        ],
        # Row 4: Timing & Help
        [
            utils.styled_button("⏱️ Set Timing & Delays (All)", "all_slots_set_interval", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_help", lang), "all_slots_help", style="primary"),
            utils.styled_button(utils.get_text("btn_how_to_use", lang), "all_slots_how_to_use", style="primary")
        ],
        # Row 5: Refresh Stats & Delete
        [
            utils.styled_button("🔄 Refresh Stats (All)", "all_slots_refresh_stats", style="primary")
        ],
        [
            utils.styled_button("🗑️ Delete All UserBots", "all_slots_delete", style="danger")
        ],
        # Row 6: Back to Bots
        [
            utils.styled_button(utils.get_text("btn_back_to_bots", lang), "menu_my_bots", style="danger")
        ]
    ]
    
    try:
        if hasattr(event, "edit"):
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


def register_handlers(client):
    
    # ------------------ New Features / Handlers ------------------
    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_reply$"))
    async def all_slots_toggle_reply_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
        any_reply_on = any(s.get("settings", {}).get("auto_reply", False) for s in sessions)
        target_state = not any_reply_on
        for s in sessions:
            s.setdefault("settings", {})["auto_reply"] = target_state
            database.save_session(s)
            userbot_manager.reload_bot_settings(s["phone"])
        word = "ENABLED" if target_state else "DISABLED"
        await show_all_slots_dashboard(event, user_id, flash_message=f"💬 **Tag Auto-Reply {word} for all userbots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_set_auto_reply$"))
    async def all_slots_set_auto_reply_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_AUTO_REPLY_MSGS"
        }
        prompt_text = utils.get_text("prompt_set_auto_reply", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^menu_all_slots$"))
    async def menu_all_slots_callback(event):
        
        user_id = event.sender_id
        if user_id in _bot_action_states:
            _bot_action_states.pop(user_id)
        effective_id = _admin_impersonation.get(user_id, user_id)
        await show_all_slots_dashboard(event, effective_id)


    @client.on(events.CallbackQuery(pattern=r"^vc_menu_(.+)$"))
    async def vc_menu_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            try:
                await event.answer("⚠️ Userbot is not running.", alert=True)
            except Exception:
                pass
            return
            
        vc_chat_id = getattr(bot_obj, "current_vc_chat_id", None)
        vc_status = "✅ Connected" if vc_chat_id else "❌ Disconnected"
        
        text = (
            f"🎙️ **VC + GRP JOINING MENU**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"> **Current Status**: **{vc_status}**\n\n"
            f"👥 **Group Joining Module**:\n"
            f"• Join Group: Userbot joins a group via invite link.\n"
            f"• Leave Group: Userbot leaves a group/channel.\n\n"
            f"🎙️ **VC Module**:\n"
            f"• Join VC: Connects userbot to group voice chat.\n"
            f"• Leave VC: Disconnects userbot from group voice chat.\n\n"
            f"🎵 **Playing Module**:\n"
            f"• Play Song: Stream audio/video or play uploaded files."
        )
        
        buttons = [
            [
                utils.styled_button("🔗 Join Group", f"vc_join_grp_{phone}", style="success"),
                utils.styled_button("❌ Leave Group", f"vc_leave_grp_{phone}", style="danger")
            ],
            [
                utils.styled_button("🎙️ Join VC", f"vc_join_{phone}", style="success"),
                utils.styled_button("🔴 Leave VC", f"vc_leave_{phone}", style="danger")
            ],
            [
                utils.styled_button("🎵 Play Song", f"play_song_{phone}", style="primary")
            ],
            [
                utils.styled_button("🔙 Back to Dashboard", f"select_bot_{phone}", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_menu$"))
    async def all_slots_vc_menu_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            try:
                await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            except Exception:
                pass
            return
            
        vc_connected_count = sum(
            1 for p in running_phones
            if getattr(userbot_manager._running_bots.get(p), "current_vc_chat_id", None)
        )
        
        text = (
            f"🎙️ **VC + GRP JOINING MENU (ALL SLOTS)**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"> **VC Connected Bots**: **{vc_connected_count} / {len(running_phones)}**\n\n"
            f"👥 **Group Joining Module (All)**:\n"
            f"• Join Group: All running userbots join a group.\n"
            f"• Leave Group: All running userbots leave a group.\n\n"
            f"🎙️ **VC Module (All)**:\n"
            f"• Join VC: Connect all running userbots to VC.\n"
            f"• Leave VC: Disconnect all running userbots from VC.\n\n"
            f"🎵 **Playing Module (All)**:\n"
            f"• Play Song: Stream on all running userbots."
        )
        
        buttons = [
            [
                utils.styled_button("🔗 Join Group (All)", "all_slots_vc_join_grp", style="success"),
                utils.styled_button("❌ Leave Group (All)", "all_slots_vc_leave_grp", style="danger")
            ],
            [
                utils.styled_button("🎙️ Join VC (All)", "all_slots_vc_join", style="success"),
                utils.styled_button("🔴 Leave VC (All)", "all_slots_vc_leave", style="danger")
            ],
            [
                utils.styled_button("🎵 Play Song (All)", "all_slots_play_song", style="primary")
            ],
            [
                utils.styled_button("🔙 Back", "menu_all_slots", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_leave_(.+)$"))
    async def vc_leave_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Leaving Voice Chat...**")
        success, msg = await bot_obj.leave_voice_chat()
        await progress_msg.delete()
        
        from .my_bots import show_bot_dashboard
        await show_bot_dashboard(event, phone, user_id, flash_message=msg)

    @client.on(events.CallbackQuery(pattern=r"^vc_leave_grp_(.+)$"))
    async def vc_leave_grp_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to leave a group.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_LEAVE_GRP"
        }
        
        prompt_text = (
            "❌ **Leave Group / Channel**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send the **Group invite link**, **Username**, or **Chat ID** of the group you want the userbot to leave.\n\n"
            "✍️ **Send the link or ID below:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_mute_(.+)$"))
    async def vc_mute_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
        success, msg = await bot_obj.mute_mic()
        await event.answer(msg, alert=True)
        await vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^vc_unmute_(.+)$"))
    async def vc_unmute_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
        success, msg = await bot_obj.unmute_mic()
        await event.answer(msg, alert=True)
        await vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^stop_song_(.+)$"))
    async def stop_song_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
        success, msg = await bot_obj.stop_song()
        await event.answer(msg, alert=True)
        await vc_menu_callback(event)

    # ---------- Now Playing Inline Buttons (skip/end/autoplay) ----------
    @client.on(events.CallbackQuery(pattern=r"^np_skip_all_(\d+)$"))
    async def np_skip_all_callback(event):
        """Skip current song on all active VC userbots (triggered from Now Playing message)."""
        user_id = int(event.pattern_match.group(1))
        if event.sender_id != user_id:
            await event.answer("⛔ This button is not for you.", alert=True)
            return
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        vc_bots = [(p, userbot_manager._running_bots[p]) for p in running_phones
                   if getattr(userbot_manager._running_bots[p], "current_vc_chat_id", None)]
        if not vc_bots:
            await event.answer("⚠️ No active VC bots found.", alert=True)
            return
        await asyncio.gather(*[bot.stop_song() for _, bot in vc_bots], return_exceptions=True)
        await event.answer("⏭️ Skipped on all active VCs!", alert=True)
        try:
            await event.edit(buttons=None)
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^np_end_all_(\d+)$"))
    async def np_end_all_callback(event):
        """End song and mute all active VC userbots (triggered from Now Playing message)."""
        user_id = int(event.pattern_match.group(1))
        if event.sender_id != user_id:
            await event.answer("⛔ This button is not for you.", alert=True)
            return
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        vc_bots = [(p, userbot_manager._running_bots[p]) for p in running_phones
                   if getattr(userbot_manager._running_bots[p], "current_vc_chat_id", None)]
        if not vc_bots:
            await event.answer("⚠️ No active VC bots found.", alert=True)
            return
        await asyncio.gather(*[bot.stop_song() for _, bot in vc_bots], return_exceptions=True)
        await event.answer("🛑 Stopped playback on all active VCs!", alert=True)
        try:
            await event.edit(buttons=None)
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^np_autoplay_(\d+)$"))
    async def np_autoplay_callback(event):
        """Toggle autoplay loop for all active VC userbots."""
        user_id = int(event.pattern_match.group(1))
        if event.sender_id != user_id:
            await event.answer("⛔ This button is not for you.", alert=True)
            return
        current = _np_autoplay_state.get(user_id, False)
        _np_autoplay_state[user_id] = not current
        state_text = "🔁 ON" if _np_autoplay_state[user_id] else "➡️ OFF"
        await event.answer(f"Autoplay set to {state_text}", alert=True)


    @client.on(events.CallbackQuery(pattern="^all_slots_vc_mute$"))
    async def all_slots_vc_mute_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        async def _mute_one(p):
            bot_obj = userbot_manager._running_bots[p]
            return await bot_obj.mute_mic()
            
        await asyncio.gather(*[_mute_one(p) for p in running_phones], return_exceptions=True)
        await event.answer("🔇 Muted mic on all running userbots!", alert=True)
        await all_slots_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_unmute$"))
    async def all_slots_vc_unmute_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        async def _unmute_one(p):
            bot_obj = userbot_manager._running_bots[p]
            return await bot_obj.unmute_mic()
            
        await asyncio.gather(*[_unmute_one(p) for p in running_phones], return_exceptions=True)
        await event.answer("🔊 Unmuted mic on all running userbots!", alert=True)
        await all_slots_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_stop_song$"))
    async def all_slots_stop_song_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        async def _stop_one(p):
            bot_obj = userbot_manager._running_bots[p]
            return await bot_obj.stop_song()
            
        await asyncio.gather(*[_stop_one(p) for p in running_phones], return_exceptions=True)
        await event.answer("🛑 Stopped playback on all running userbots!", alert=True)
        await all_slots_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_leave_grp$"))
    async def all_slots_vc_leave_grp_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_LEAVE_GRP"
        }
        
        prompt_text = (
            "❌ **Leave Group / Channel (All Slots)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send the **Group invite link**, **Username**, or **Chat ID** of the group you want ALL running userbots to leave.\n\n"
            "✍️ **Send the link or ID below:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_leave$"))
    async def all_slots_vc_leave_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Leaving Voice Chats concurrently on all running userbots...**")
        
        async def _leave_one(p):
            bot_obj = userbot_manager._running_bots[p]
            success, msg = await bot_obj.leave_voice_chat()
            return success
            
        results = await asyncio.gather(*[_leave_one(p) for p in running_phones], return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        await progress_msg.delete()
        await show_all_slots_dashboard(event, user_id, flash_message=f"🔴 **Left VC on {success_count}/{len(running_phones)} userbots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_restart$"))
    async def all_slots_restart_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Restarting userbots sequentially to optimize memory...**")
        
        restarted = 0
        limit_reached = False
        import config
        max_running = getattr(config, "MAX_RUNNING_USERBOTS", 99999)

        for s in sessions:
            phone = s["phone"]
            await userbot_manager.stop_userbot(phone)
            await asyncio.sleep(1.0) # Let the slot free up
            
            if not userbot_manager.can_start_more_bots():
                limit_reached = True
                continue # We continue stopping the rest, but won't restart them

            success = await userbot_manager.start_userbot(phone)
            if success:
                restarted += 1
            await asyncio.sleep(1.0)
            
        await progress_msg.delete()
        
        if limit_reached:
            flash = f"🔄 **Restarted {restarted} userbots!**\n⚠️ *Some bots stopped but couldn't restart as the server limit of {max_running} active bots was reached.*"
        else:
            flash = f"🔄 **Restarted {restarted} userbots!**"
            
        await show_all_slots_dashboard(event, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern="^all_slots_clone_profile$"))
    async def all_slots_clone_profile_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        text = (
            "👥 **Bulk Profile Cloning Options**\n\n"
            "Choose which aspect of the target profile you would like to clone to ALL your running Userbots:"
        )
        
        buttons = [
            [
                utils.styled_button("👥 Complete Profile Clone", "all_slots_clone_opt_complete", style="success")
            ],
            [
                utils.styled_button("✏️ Clone Name Only", "all_slots_clone_opt_name", style="primary"),
                utils.styled_button("📝 Clone Bio Only", "all_slots_clone_opt_bio", style="primary")
            ],
            [
                utils.styled_button("🖼️ Clone Photo Only", "all_slots_clone_opt_photo", style="primary")
            ],
            [utils.styled_button("🔙 Cancel", "menu_all_slots", style="danger")]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^all_slots_clone_opt_(complete|name|bio|photo)$"))
    async def all_slots_clone_opt_callback(event):
        clone_type = event.pattern_match.group(1)
        user_id = event.sender_id
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_CLONE_TARGET",
            "clone_type": clone_type
        }
        
        type_display = {
            "complete": "Complete Profile",
            "name": "Name Only",
            "bio": "Bio Only",
            "photo": "Photo Only"
        }.get(clone_type, "Complete Profile")
        
        prompt_text = (
            f"👥 **Bulk Clone Profile ({type_display})**\n\n"
            f"Enter the username (e.g. `@username`) or User ID of the target profile you want to clone for ALL userbots:"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_clone_profile", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_help$"))
    async def all_slots_help_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("help_dashboard_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_all_slots", style="primary")]]
        global_settings = database.get_global_settings()
        help_image = global_settings.get("help_image")
        try:
             if help_image:
                 await event.respond(text, file=help_image, buttons=buttons)
             else:
                 await event.edit(text, buttons=buttons)
        except Exception:
             await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_how_to_use$"))
    async def all_slots_how_to_use_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("how_to_use_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_all_slots", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_change_name$"))
    async def all_slots_change_name_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_NAME"
        }
        prompt_text = utils.get_text("prompt_name", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_interval$"))
    async def all_slots_set_interval_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        text = (
            f"⏱️ **Bulk Timing Settings**\n\n"
            f"Configure timing settings for ALL slots simultaneously:\n\n"
            f"• **Group-to-Group Delay**: Delay between messages to different groups.\n"
            f"• **Loop Repeat Interval**: Delay between repeating the broadcast loop.\n\n"
            f"__Choose an option to modify:__"
        )
        buttons = [
            [
                utils.styled_button("⏱️ Set Group Delay (All)", "all_slots_set_delay", style="primary"),
                utils.styled_button("🔄 Set Loop Interval (All)", "all_slots_set_loop_interval", style="primary")
            ],
            [
                utils.styled_button("🔙 Back", "menu_all_slots", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_loop_interval$"))
    async def all_slots_set_loop_interval_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("interval_title", lang)
        buttons = [
            [
                utils.styled_button(utils.get_text("btn_int_val", lang, val=300), "all_slots_int_val_300", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=500), "all_slots_int_val_500", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=600), "all_slots_int_val_600", style="primary")
            ],
            [
                utils.styled_button(utils.get_text("btn_int_custom", lang), "all_slots_int_custom", style="primary"),
                utils.styled_button("🔙 Back", "all_slots_set_interval", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^all_slots_int_val_(\d+)$"))
    async def all_slots_int_val_callback(event):
        val = int(event.pattern_match.group(1))
        user_id = event.sender_id
        sessions = database.get_sessions(user_id)
        for s in sessions:
            s.setdefault("settings", {})["broadcast_interval"] = val
            database.save_session(s)
            userbot_manager.reload_bot_settings(s["phone"])
        await show_all_slots_dashboard(event, user_id, flash_message=f"⏱️ **Interval updated to {val}s for all bots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_int_custom$"))
    async def all_slots_int_custom_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_CUSTOM_INTERVAL"
        }
        prompt_text = utils.get_text("prompt_custom_interval", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_loop_interval", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_delay$"))
    async def all_slots_set_delay_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("inter_delay_title", lang)
        
        buttons = [
            [
                utils.styled_button(utils.get_text("btn_int_val", lang, val=20), "all_slots_del_val_20", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=30), "all_slots_del_val_30", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=60), "all_slots_del_val_60", style="primary")
            ],
            [
                utils.styled_button(utils.get_text("btn_del_custom", lang), "all_slots_del_custom", style="primary"),
                utils.styled_button("🔙 Back", "all_slots_set_interval", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^all_slots_del_val_(\d+)$"))
    async def all_slots_del_val_callback(event):
        val = int(event.pattern_match.group(1))
        user_id = event.sender_id
        sessions = database.get_sessions(user_id)
        for s in sessions:
            s.setdefault("settings", {})["inter_group_delay"] = val
            database.save_session(s)
            userbot_manager.reload_bot_settings(s["phone"])
        await show_all_slots_dashboard(event, user_id, flash_message=f"⏱️ **Inter-Group Delay updated to {val}s for all bots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_del_custom$"))
    async def all_slots_del_custom_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_CUSTOM_DELAY"
        }
        prompt_text = utils.get_text("prompt_custom_inter_delay", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_delay", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_refresh_stats$"))
    async def all_slots_refresh_stats_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_bots = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_bots:
            await event.answer("⚠️ Start at least one userbot first!", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Refreshing statistics for all running userbots concurrently...**")
        
        async def _refresh_one(phone):
            bot_obj = userbot_manager._running_bots[phone]
            try:
                await bot_obj.get_groups(force_refresh=True)
                return True
            except Exception:
                return False
                
        results = await asyncio.gather(*[_refresh_one(p) for p in running_bots], return_exceptions=True)
        refreshed = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        await progress_msg.delete()
        await show_all_slots_dashboard(event, user_id, flash_message=f"🔄 **Refreshed stats for {refreshed} userbots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_delete$"))
    async def all_slots_delete_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        text = (
            "⚠️ **Delete All UserBots**\n\n"
            "Are you absolutely sure you want to delete **ALL** connected userbots? "
            "This will delete all Telegram sessions from disk and database. This action cannot be undone!"
        )
        buttons = [
            [utils.styled_button("🗑️ Yes, Delete All", "all_slots_delete_confirm", style="danger")],
            [utils.styled_button("❌ Cancel", "menu_all_slots", style="primary")]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_delete_confirm$"))
    async def all_slots_delete_confirm_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        
        async def _delete_one(s):
            await userbot_manager.remove_userbot(s["phone"])
            return True
            
        results = await asyncio.gather(*[_delete_one(s) for s in sessions], return_exceptions=True)
        deleted = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        from .my_bots import show_bots_list
        await show_bots_list(event, user_id, flash_message=f"🗑️ **Deleted {deleted} userbot sessions successfully.**")

    @client.on(events.CallbackQuery(pattern="^all_slots_start$"))
    async def all_slots_start_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Starting userbots sequentially to optimize memory...**")
        
        started = 0
        limit_reached = False
        import config
        max_running = getattr(config, "MAX_RUNNING_USERBOTS", 99999)

        for s in sessions:
            phone = s["phone"]
            if not userbot_manager.is_bot_running(phone):
                if not userbot_manager.can_start_more_bots():
                    limit_reached = True
                    break
                success = await userbot_manager.start_userbot(phone)
                if success:
                    started += 1
                await asyncio.sleep(1.0)
                
        await progress_msg.delete()
        
        if limit_reached:
            flash = f"🟢 **Started {started} userbots!**\n⚠️ *Some userbots could not start because the server limit of {max_running} active bots was reached.*"
        else:
            flash = f"🟢 **Started {started} userbots!**"
            
        await show_all_slots_dashboard(event, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern="^all_slots_stop$"))
    async def all_slots_stop_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Stopping all userbots concurrently...**")
        
        async def _stop_one(s):
            phone = s["phone"]
            if userbot_manager.is_bot_running(phone):
                await userbot_manager.stop_userbot(phone)
                return True
            return False
            
        results = await asyncio.gather(*[_stop_one(s) for s in sessions], return_exceptions=True)
        stopped = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        await progress_msg.delete()
        await show_all_slots_dashboard(event, user_id, flash_message=f"🔴 **Stopped {stopped} userbots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_join$"))
    async def all_slots_vc_join_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_VC_LINK"
        }
        
        prompt_text = utils.get_text("prompt_all_vc_link", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_broadcast$"))
    async def all_slots_set_broadcast_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        text = (
            f"✉️ **Bulk Broadcast Settings**\n\n"
            f"Configure broadcasting settings for ALL userbots simultaneously:\n"
        )
        buttons = [
            [
                utils.styled_button("✉️ Set Single Message (All)", "all_slots_set_single_msg", style="primary"),
                utils.styled_button("📚 Set Multiple Messages (All)", "all_slots_set_multi_msg", style="primary")
            ],
            [
                utils.styled_button("🔄 Toggle Broadcast Mode (All)", "all_slots_toggle_broadcast_mode", style="primary")
            ],
            [
                utils.styled_button("🔙 Back", "menu_all_slots", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_single_msg$"))
    async def all_slots_set_single_msg_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_BROADCAST"
        }
        prompt_text = utils.get_text("prompt_broadcast", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_broadcast", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_multi_msg$"))
    async def all_slots_set_multi_msg_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_MULTI_MSG"
        }
        prompt_text = (
            "📚 **Set Multiple Messages (All Slots)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send your multiple broadcast messages separated by commas `,`. The bot will randomly pick one message for each group.\n\n"
            "💡 **Example Input**:\n"
            "`Hey check this out!, Join our channel now!, Best deals today! www.example.com`\n\n"
            "✍️ **Send your comma-separated message list below:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_broadcast", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_broadcast_mode$"))
    async def all_slots_toggle_broadcast_mode_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        for s in sessions:
            settings = s.setdefault("settings", {})
            current_mode = settings.get("broadcast_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["broadcast_mode"] = new_mode
            database.save_session(s)
            if userbot_manager.is_bot_running(s["phone"]):
                userbot_manager.reload_bot_settings(s["phone"])
        await show_all_slots_dashboard(event, user_id, flash_message="🔄 **Toggled broadcast mode on all userbots!**")

    @client.on(events.CallbackQuery(pattern="^all_slots_set_welcome$"))
    async def all_slots_set_welcome_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_WELCOME"
        }
        
        prompt_text = utils.get_text("prompt_all_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_multi_welcome$"))
    async def all_slots_set_multi_welcome_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_MULTI_WELCOME"
        }
        
        prompt_text = utils.get_text("prompt_multi_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_(spam|welcome|add_contact)$"))
    async def all_slots_toggles_callback(event):
        feature = event.pattern_match.group(1)
        user_id = event.sender_id
        
        sessions = database.get_sessions(user_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        key_map = {
            "spam": "auto_spam",
            "welcome": "auto_welcome",
            "add_contact": "auto_add_contact"
        }
        db_key = key_map[feature]
        
        any_on = any(s.get("settings", {}).get(db_key, False) for s in sessions)
        new_state = not any_on
        
        for s in sessions:
            s.setdefault("settings", {})[db_key] = new_state
            database.save_session(s)
            userbot_manager.reload_bot_settings(s["phone"])
            
        state_word = "ON" if new_state else "OFF"
        await show_all_slots_dashboard(event, user_id, flash_message=f"⚙️ **{feature.upper()} turned {state_word} for all bots!**")

    @client.on(events.CallbackQuery(pattern=r"^set_run_timer_(.+)$"))
    async def set_run_timer_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_RUN_TIMER"
        }
        
        prompt_text = "⏱️ **Set Run Timer**\n\nSend the number of hours (or minutes using 'm') you want the userbot to run before automatically stopping.\nExample:\n`2` (for 2 hours)\n`30m` (for 30 minutes)\n`0` (to disable timer)"
        buttons = [[utils.styled_button("🔙 Cancel", f"bot_dashboard_{phone}", style="primary")]]
        
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_run_timer$"))
    async def all_slots_set_run_timer_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_RUN_TIMER"
        }
        
        prompt_text = "⏱️ **Set Run Timer (All Bots)**\n\nSend the number of hours (or minutes using 'm') you want all your userbots to run before automatically stopping.\nExample:\n`2` (for 2 hours)\n`30m` (for 30 minutes)\n`0` (to disable timer)"
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_join_(.+)$"))
    async def vc_join_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to join a VC.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_VC_LINK"
        }
        
        prompt_text = utils.get_text("prompt_vc_link", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_join_grp_(.+)$"))
    async def vc_join_grp_callback(event):
        """Join Group via invite link AND its VC in one step (single bot)."""
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to join a group/VC.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_VC_GRP_LINK"
        }
        
        prompt_text = (
            "🔗 **Join Group + VC (Auto)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send your **group invite link** (e.g. `https://t.me/+xxxx`) or group username.\n\n"
            "✅ The userbot will:\n"
            "1. **Auto-join** the group/channel via the link.\n"
            "2. **Immediately join** the active Voice Chat in that group.\n\n"
            "✍️ **Send the group link or username below:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_join_grp$"))
    async def all_slots_vc_join_grp_callback(event):
        """Join Group via invite link for ALL userbots (running or stopped)."""
        user_id = event.sender_id
        user = database.get_user(user_id)
        
        sessions = database.get_sessions(user_id)
        if not sessions:
            await event.answer("⚠️ No userbot slots found. Please login a bot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_VC_GRP_LINK"
        }
        
        prompt_text = (
            "🔗 **Join Group — All Slots (Auto)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"> **All {len(sessions)} userbots** (running or stopped) will:\n"
            "1. **Auto-start** (if currently stopped).\n"
            "2. **Auto-join** the group/channel via your link.\n\n"
            "✍️ **Send the group invite link or username below:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^no_login_"))
    async def no_login_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        alert_text = utils.get_text("account_login_first", lang)
        await event.answer(alert_text, alert=True)

    @client.on(events.CallbackQuery(pattern=r"^help_bot_(.+)$"))
    async def help_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("help_dashboard_text", lang)
        buttons = [[utils.styled_button("🔙 Back", f"select_bot_{phone}", style="primary")]]
        
        global_settings = database.get_global_settings()
        help_image = global_settings.get("help_image")
        try:
            if help_image:
                await event.respond(text, file=help_image, buttons=buttons)
            else:
                await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^how_to_use_(.+)$"))
    async def how_to_use_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("how_to_use_text", lang)
        buttons = [[utils.styled_button("🔙 Back", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^help_bot_no_login$"))
    async def help_bot_no_login_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("help_dashboard_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_my_bots", style="primary")]]
        
        global_settings = database.get_global_settings()
        help_image = global_settings.get("help_image")
        try:
            if help_image:
                await event.respond(text, file=help_image, buttons=buttons)
            else:
                await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^how_to_use_no_login$"))
    async def how_to_use_no_login_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("how_to_use_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_my_bots", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^clone_profile_(.+)$"))
    async def clone_profile_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to clone a profile.", alert=True)
            return
            
        text = (
            "👤 **Profile Cloning Options**\n\n"
            "Choose which aspect of the target profile you would like to clone to your Userbot:"
        )
        
        sess = database.get_session(phone)
        buttons = [
            [
                utils.styled_button("👤 Complete Profile Clone", f"clone_opt_complete_{phone}", style="success")
            ],
            [
                utils.styled_button("✏️ Clone Name Only", f"clone_opt_name_{phone}", style="primary"),
                utils.styled_button("📝 Clone Bio Only", f"clone_opt_bio_{phone}", style="primary")
            ],
            [
                utils.styled_button("🖼️ Clone Photo Only", f"clone_opt_photo_{phone}", style="primary")
            ]
        ]
        
        if sess and "original_first_name" in sess:
            buttons.append([utils.styled_button("🔄 Return to Original Profile", f"restore_profile_{phone}", style="success")])
            
        buttons.append([utils.styled_button("🔙 Back", f"select_bot_{phone}", style="danger")])
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^clone_opt_(complete|name|bio|photo)_(.+)$"))
    async def clone_opt_callback(event):
        clone_type = event.pattern_match.group(1)
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to clone a profile.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_CLONE_TARGET",
            "clone_type": clone_type
        }
        
        type_display = {
            "complete": "Complete Profile",
            "name": "Name Only",
            "bio": "Bio Only",
            "photo": "Photo Only"
        }.get(clone_type, "Complete Profile")
        
        prompt_text = (
            f"👤 **Clone Profile ({type_display})**\n\n"
            f"Enter the username (e.g. `@username` or `username`) or User ID of the target profile you want to clone:"
        )
        
        buttons = [[utils.styled_button("🔙 Cancel", f"clone_profile_{phone}", style="danger")]]
        
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^restore_profile_(.+)$"))
    async def restore_profile_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to restore a profile.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ **Restoring original profile, please wait...**")
        success, msg = await userbot_manager.restore_original_profile(phone)
        await progress_msg.delete()
        
        if success:
            flash = f"✅ **Profile restored!**\n{msg}"
        else:
            flash = f"❌ **Restoration failed:** {msg}"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ Navigation ------------------
    @client.on(events.CallbackQuery(pattern="^menu_my_bots$"))
    async def bots_list_callback(event):
        await show_bots_list(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern=r"^select_bot_(.+)$"))
    async def select_bot_callback(event):
        phone = event.pattern_match.group(1)
        await show_bot_dashboard(event, phone, event.sender_id)

    # ------------------ Core Controls ------------------
    @client.on(events.CallbackQuery(pattern=r"^start_bot_(.+)$"))
    async def start_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        # Check if already running
        if userbot_manager.is_bot_running(phone):
            await show_bot_dashboard(event, phone, user_id, flash_message="🟢 **Userbot is already running.**")
            return

        # Limit check removed

        # Start bot in background
        success = await userbot_manager.start_userbot(phone)
        if success:
            flash = "🟢 **Userbot successfully started!**"
        else:
            flash = "❌ **Failed to start Userbot. Check Telegram session/auth.**"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^stop_bot_(.+)$"))
    async def stop_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        # Stop bot
        await userbot_manager.stop_userbot(phone)
        await show_bot_dashboard(event, phone, user_id, flash_message="🔴 **Userbot stopped.**")

    @client.on(events.CallbackQuery(pattern=r"^restart_bot_(.+)$"))
    async def restart_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        # Stop
        await userbot_manager.stop_userbot(phone)
        await asyncio.sleep(1.0) # wait a moment for the bot to fully stop and release slots
        
        # Limit check removed

        # Start
        success = await userbot_manager.start_userbot(phone)
        if success:
            flash = "🔄 **Userbot successfully restarted!**"
        else:
            flash = "❌ **Failed to start Userbot after stopping.**"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^view_settings_info_(.+)$"))
    async def view_settings_info_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if not sess or str(sess.get("user_id")) != str(user_id):
            await event.answer("❌ Session error.", alert=True)
            return
            
        settings = sess.get("settings", {})
        
        spam_status = "🟢 ON" if settings.get("auto_spam") else "🔴 OFF"
        welcome_status = "🟢 ON" if settings.get("auto_welcome") else "🔴 OFF"
        add_contact_status = "🟢 ON" if settings.get("auto_add_contact") else "🔴 OFF"
        interval = settings.get("broadcast_interval", 300)
        spam_msg = settings.get("broadcast_msg", "None")
        welcome_msg = settings.get("welcome_msg", "None")
        welcome_msgs = ", ".join(settings.get("welcome_messages", [])) or "None"
        
        # Format a clean message
        text = (
            f"ℹ️ **UserBot Settings Info**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📞 Account: `{phone}`\n"
            f"🏷️ Name: **{sess.get('name', 'Userbot')}**\n"
            f"🔗 Username: @{sess.get('username', 'None')}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 **Auto-Spam Settings**:\n"
            f"• Status: {spam_status}\n"
            f"• Interval: **{interval} seconds**\n"
            f"• Broadcast Message:\n"
            f"  `{spam_msg}`\n\n"
            f"👋 **Auto-Welcome Settings**:\n"
            f"• Status: {welcome_status}\n"
            f"• Welcome Message:\n"
            f"  `{welcome_msg}`\n"
            f"• Multiple Welcome Messages:\n"
            f"  `{welcome_msgs}`\n\n"
            f"👥 **Auto-Contact Settings**:\n"
            f"• Status: {add_contact_status}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"_Use the dashboard controls to edit these values._"
        )
        
        buttons = [[utils.styled_button("🔙 Back to Dashboard", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^delete_bot_(.+)$"))
    async def delete_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        await userbot_manager.remove_userbot(phone)
        await show_bots_list(event, user_id, flash_message="🗑️ **Userbot session successfully deleted.**")

    # ------------------ Toggles ------------------
    @client.on(events.CallbackQuery(pattern=r"^toggle_(spam|welcome|add_contact|reply)_(.+)$"))
    async def toggles_callback(event):
        feature = event.pattern_match.group(1)
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        flash = None
        if sess and str(sess.get("user_id")) == str(user_id):
            settings = sess.setdefault("settings", {})
            
            key_map = {
                "spam": "auto_spam",
                "welcome": "auto_welcome",
                "add_contact": "auto_add_contact",
                "reply": "auto_reply"
            }
            db_key = key_map[feature]
            settings[db_key] = not settings.get(db_key, False)
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
            state_word = "ON" if settings[db_key] else "OFF"
            feature_name = feature.upper()
            flash = f"⚙️ **{feature_name} is now {state_word}**"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ Stats Refresh ------------------
    @client.on(events.CallbackQuery(pattern=r"^refresh_stats_(.+)$"))
    async def refresh_stats_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        flash = None
        if sess and str(sess.get("user_id")) == str(user_id):
            if userbot_manager.is_bot_running(phone):
                bot_obj = userbot_manager._running_bots[phone]
                try:
                    # Force refresh the groups cache, which also updates the DB stats
                    groups = await bot_obj.get_groups(force_refresh=True)
                    sess = database.get_session(phone)
                    users = sess["stats"]["user_count"]
                    
                    flash = f"🔄 **Stats refreshed! Groups: {len(groups)} | Contacts: {users}**"
                except Exception as e:
                    logger.error(f"Error refreshing stats: {e}")
                    flash = f"❌ **Error during refresh: {e}**"
            else:
                flash = "⚠️ **Bot must be running to refresh statistics.**"
                
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ Text Prompts ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_broadcast_(.+)$"))
    async def set_broadcast_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        settings = sess.get("settings", {}) if sess else {}
        mode = settings.get("broadcast_mode", "single")
        single_msg = settings.get("broadcast_msg")
        multi_msgs = settings.get("broadcast_messages", [])
        
        mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "✉️ Single (Normal)"
        single_status = "✅ Set" if single_msg else "❌ Empty"
        multiple_status = f"✅ Set ({len(multi_msgs)} msgs)" if multi_msgs else "❌ Empty"
        
        text = (
            f"✉️ **Broadcast Message Settings**\n"
            f"Configure messages for Userbot `{phone}`:\n\n"
            f"• **Current Mode**: **{mode_display}**\n"
            f"• **Single Msg**: {single_status}\n"
            f"• **Multiple Msgs**: {multiple_status}\n\n"
            f"__How to set multiple messages__: Click 'Set Multiple Messages' and send your messages separated by commas `,`. "
            f"For example:\n`Message A, Message B, Message C`"
        )
        
        buttons = [
            [
                utils.styled_button("✉️ Set Single Message", f"set_single_msg_{phone}", style="primary"),
                utils.styled_button("📚 Set Multiple Messages", f"set_multi_msg_{phone}", style="primary")
            ],
            [
                utils.styled_button(f"🔄 Mode: {mode.upper()}", f"toggle_broadcast_mode_{phone}", style="primary")
            ],
            [
                utils.styled_button("🔙 Back to Dashboard", f"select_bot_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_single_msg_(.+)$"))
    async def set_single_msg_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_BROADCAST"
        }
        prompt_text = utils.get_text("prompt_broadcast", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_broadcast_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_multi_msg_(.+)$"))
    async def set_multi_msg_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_MULTI_MSG"
        }
        prompt_text = (
            "📚 **Set Multiple Messages**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send your multiple broadcast messages separated by commas `,`. The bot will randomly pick one message for each group.\n\n"
            "💡 **Example Input**:\n"
            "`Hey check this out!, Join our channel now!, Best deals today! www.example.com`\n\n"
            "✍️ **Send your comma-separated message list below:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"set_broadcast_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^toggle_broadcast_mode_(.+)$"))
    async def toggle_broadcast_mode_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if sess and str(sess.get("user_id")) == str(user_id):
            settings = sess.setdefault("settings", {})
            current_mode = settings.get("broadcast_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["broadcast_mode"] = new_mode
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
        await set_broadcast_callback(event)

    # ------------------ DM Welcome Message Sub-Menu ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_welcome_(.+)$"))
    async def set_welcome_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        settings = sess.get("settings", {}) if sess else {}
        mode = settings.get("welcome_mode", "single")
        single_msg = settings.get("welcome_msg")
        multi_msgs = settings.get("welcome_messages", [])
        
        mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "👋 Single (Normal)"
        single_status = "✅ Set" if single_msg else "❌ Empty"
        multiple_status = f"✅ Set ({len(multi_msgs)} msgs)" if multi_msgs else "❌ Empty"
        
        text = (
            f"👋 **DM Welcome Message Settings**\n"
            f"Configure DM welcome responses for Userbot `{phone}`:\n\n"
            f"• **Current Mode**: **{mode_display}**\n"
            f"• **Single Welcome**: {single_status}\n"
            f"• **Multiple Welcomes**: {multiple_status}\n\n"
            f"__How to set multiple welcomes__: Click 'Set Multiple Welcomes' and send messages separated by commas `,`.\n"
            f"Example: `Hello welcome!, Hey thanks for messaging!, Hi there!`"
        )
        
        buttons = [
            [
                utils.styled_button("👋 Set Single Welcome", f"set_single_welcome_{phone}", style="primary"),
                utils.styled_button("📚 Set Multiple Welcomes", f"set_multi_welcome_{phone}", style="primary")
            ],
            [
                utils.styled_button(f"🔄 Mode: {mode.upper()}", f"toggle_welcome_mode_{phone}", style="primary")
            ],
            [
                utils.styled_button("🔙 Back to Dashboard", f"select_bot_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_single_welcome_(.+)$"))
    async def set_single_welcome_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_WELCOME"
        }
        prompt_text = utils.get_text("prompt_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_welcome_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_multi_welcome_(.+)$"))
    async def set_multi_welcome_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_MULTI_WELCOME"
        }
        prompt_text = utils.get_text("prompt_multi_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_welcome_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^toggle_welcome_mode_(.+)$"))
    async def toggle_welcome_mode_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if sess and str(sess.get("user_id")) == str(user_id):
            settings = sess.setdefault("settings", {})
            current_mode = settings.get("welcome_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["welcome_mode"] = new_mode
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
        await set_welcome_callback(event)

    # ------------------ Tag Auto-Reply Sub-Menu ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_auto_reply_(.+)$"))
    async def set_auto_reply_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        settings = sess.get("settings", {}) if sess else {}
        mode = settings.get("auto_reply_mode", "single")
        single_msg = settings.get("auto_reply_msg")
        multi_msgs = settings.get("auto_reply_messages", [])
        
        mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "💬 Single (Normal)"
        single_status = "✅ Set" if single_msg else "❌ Empty"
        multiple_status = f"✅ Set ({len(multi_msgs)} msgs)" if multi_msgs else "❌ Empty"
        
        text = (
            f"💬 **Group Tag Auto-Reply Settings**\n"
            f"Configure group tag/mention responses for Userbot `{phone}`:\n\n"
            f"• **Current Mode**: **{mode_display}**\n"
            f"• **Single Reply**: {single_status}\n"
            f"• **Multiple Replies**: {multiple_status}\n\n"
            f"__How to set multiple replies__: Click 'Set Multiple Replies' and send messages separated by commas `,`.\n"
            f"Example: `Hello!, How can I help?, Check out my channel!`"
        )
        
        buttons = [
            [
                utils.styled_button("💬 Set Single Reply", f"set_single_reply_{phone}", style="primary"),
                utils.styled_button("📚 Set Multiple Replies", f"set_multi_reply_{phone}", style="primary")
            ],
            [
                utils.styled_button(f"🔄 Mode: {mode.upper()}", f"toggle_reply_mode_{phone}", style="primary")
            ],
            [
                utils.styled_button("🔙 Back to Dashboard", f"select_bot_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_single_reply_(.+)$"))
    async def set_single_reply_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_AUTO_REPLY_SINGLE"
        }
        prompt_text = "💬 **Send your single group tag auto-reply message below:**"
        buttons = [[utils.styled_button("🔙 Cancel", f"set_auto_reply_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_multi_reply_(.+)$"))
    async def set_multi_reply_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_AUTO_REPLY_MSGS"
        }
        prompt_text = utils.get_text("prompt_set_auto_reply", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_auto_reply_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^toggle_reply_mode_(.+)$"))
    async def toggle_reply_mode_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if sess and str(sess.get("user_id")) == str(user_id):
            settings = sess.setdefault("settings", {})
            current_mode = settings.get("auto_reply_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["auto_reply_mode"] = new_mode
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
        await set_auto_reply_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^change_name_(.+)$"))
    async def change_name_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_NAME"
        }
        prompt_text = utils.get_text("prompt_name", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    # ------------------ Play Song callbacks ------------------
    @client.on(events.CallbackQuery(pattern=r"^play_song_(.+)$"))
    async def play_song_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
            
        if not getattr(bot_obj, "current_vc_chat_id", None):
            await event.answer("⚠️ Userbot is not in any voice chat. Join a VC first!", alert=True)
            return
            
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_SONG"
        }
        
        prompt_text = (
            "🎵 **Voice Chat Music Player**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ **Important Requirement**:\n"
            "> Your Userbot must be joined to a Voice Chat (VC) before playing! If not joined, go back and click **Join VC** first.\n\n"
            "🚀 **Instructions**:\n"
            "• To play audio track: Send `/play <song name>` or link in bot DM.\n"
            "• To play video call: Send `/vplay <video name>` or link in bot DM.\n\n"
            "✍️ **Type your song name or YouTube link below to start playing in audio mode:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_play_song$"))
    async def all_slots_play_song_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = database.get_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        vc_phones = []
        for phone in running_phones:
            bot_obj = userbot_manager._running_bots[phone]
            if getattr(bot_obj, "current_vc_chat_id", None):
                vc_phones.append(phone)
                
        if not vc_phones:
            await event.answer("⚠️ None of the running userbots are in a voice chat. Make them join a VC first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_SONG"
        }
        
        prompt_text = (
            "🎵 **Voice Chat Music Player (All Slots)**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ **Important Requirement**:\n"
            "> Make sure your active userbots have already joined the Voice Chat (VC) before playing!\n\n"
            "🚀 **Instructions**:\n"
            "• To play audio (All): Send `/play <song name>` or link in bot DM.\n"
            "• To play video (All): Send `/vplay <video name>` or link in bot DM.\n\n"
            "✍️ **Type your song name or YouTube link below to start playing on all active voice chats in audio mode:**"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)


    # ------------------ Interval settings ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_interval_(.+)$"))
    async def set_interval_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        settings = sess.get("settings", {}) if sess else {}
        current_delay = settings.get("inter_group_delay", 30.0)
        current_interval = settings.get("broadcast_interval", 300)
        
        text = (
            f"⏱️ **UserBot Timing Settings**\n\n"
            f"Configure delay timings for Userbot `{phone}`:\n\n"
            f"• **Group-to-Group Delay**: `{current_delay}s` (Delay between sending messages to different groups)\n"
            f"• **Loop Repeat Interval**: `{current_interval}s` (Delay between repeating the broadcast loop)\n\n"
            f"__Choose an option to modify:__"
        )
        buttons = [
            [
                utils.styled_button("⏱️ Set Group Delay", f"set_inter_delay_{phone}", style="primary"),
                utils.styled_button("🔄 Set Loop Interval", f"set_loop_interval_{phone}", style="primary")
            ],
            [
                utils.styled_button("🔙 Back to Dashboard", f"select_bot_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_loop_interval_(.+)$"))
    async def set_loop_interval_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("interval_title", lang)
        
        buttons = [
            [
                utils.styled_button(utils.get_text("btn_int_val", lang, val=300), f"int_val_300_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=500), f"int_val_500_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=600), f"int_val_600_{phone}", style="primary")
            ],
            [
                utils.styled_button(utils.get_text("btn_int_custom", lang), f"int_custom_{phone}", style="primary"),
                utils.styled_button("🔙 Back", f"set_interval_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^int_val_(\d+)_(.+)$"))
    async def int_val_callback(event):
        val = int(event.pattern_match.group(1))
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        flash = None
        if sess and str(sess.get("user_id")) == str(user_id):
            sess.setdefault("settings", {})["broadcast_interval"] = val
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = f"⏱️ **Interval updated to {val}s**"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^int_custom_(.+)$"))
    async def int_custom_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_CUSTOM_INTERVAL"
        }
        
        prompt_text = utils.get_text("prompt_custom_interval", lang)
        try:
            buttons = [[utils.styled_button("🔙 Cancel", f"set_loop_interval_{phone}", style="primary")]]
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text)

    @client.on(events.CallbackQuery(pattern=r"^set_inter_delay_(.+)$"))
    async def set_inter_delay_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("inter_delay_title", lang)
        
        buttons = [
            [
                utils.styled_button(utils.get_text("btn_int_val", lang, val=20), f"del_val_20_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=30), f"del_val_30_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=60), f"del_val_60_{phone}", style="primary")
            ],
            [
                utils.styled_button(utils.get_text("btn_del_custom", lang), f"del_custom_{phone}", style="primary"),
                utils.styled_button("🔙 Back", f"set_interval_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^del_val_(\d+)_(.+)$"))
    async def del_val_callback(event):
        val = int(event.pattern_match.group(1))
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        flash = None
        if sess and str(sess.get("user_id")) == str(user_id):
            sess.setdefault("settings", {})["inter_group_delay"] = val
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = f"⏱️ **Inter-Group Delay updated to {val}s**"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^del_custom_(.+)$"))
    async def del_custom_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_CUSTOM_DELAY"
        }
        
        prompt_text = utils.get_text("prompt_custom_inter_delay", lang)
        try:
            buttons = [[utils.styled_button("🔙 Cancel", f"set_inter_delay_{phone}", style="primary")]]
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text)

    # ------------------ Message Input Listeners ------------------
    @client.on(events.NewMessage)
    async def text_input_handler(event):
        if not event.is_private:
            return
            
        user_id = event.sender_id
        
        # Check for any slash command first
        cmd_text = event.text.strip() if event.text else ""
        if cmd_text.startswith("/"):
            # Clear pending state on commands to prevent treating them as song query searches
            if user_id in _bot_action_states:
                _bot_action_states.pop(user_id)
                
            parts = cmd_text.split(" ", 1)
            cmd = parts[0].lower()
            
            # Handle Direct /skip and /end commands
            if cmd in ["/skip", "/end"]:
                sessions = database.get_sessions(user_id)
                if not sessions:
                    await event.reply("❌ You do not have any userbot slots.")
                    return
                running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
                vc_bots = [userbot_manager._running_bots[p] for p in running_phones
                           if getattr(userbot_manager._running_bots.get(p), "current_vc_chat_id", None)]
                if not vc_bots:
                    await event.reply("⚠️ None of your userbots are currently playing anything in Voice Chat.")
                    return
                
                await asyncio.gather(*[bot.stop_song() for bot in vc_bots], return_exceptions=True)
                action_name = "skipped" if cmd == "/skip" else "ended"
                await event.reply(f"✅ Playback {action_name} successfully on all active Voice Chats!")
                return
                
            if cmd in ["/play", "/vplay"]:
                query = parts[1].strip() if len(parts) > 1 else ""
                
                # Retrieve sessions first to prevent NameError
                sessions = database.get_sessions(user_id)
                if not sessions:
                    await event.reply("❌ You do not have any userbot slots.")
                    return
                running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
                
                # Check reply for audio
                replied_audio = None
                local_file_path = None
                audio_title = None
                audio_duration = 30
                
                if event.message.is_reply:
                    reply_msg = await event.get_reply_message()
                    media_obj, audio_title, audio_duration = extract_media_info(reply_msg)
                    if media_obj:
                        replied_audio = reply_msg
                        progress_download = await event.reply("📥 **Downloading replied media file...**\n━━━━━━━━━━━━━━━━━━━━\n📊 Progress: `[░░░░░░░░░░] 0.0%`")
                        try:
                            local_file_path = await client.download_media(
                                replied_audio, 
                                file="downloads/",
                                progress_callback=lambda c, t: download_progress_sync(c, t, progress_download, "Downloading replied media file")
                            )
                        except Exception as dl_err:
                            logger.error(f"Failed to download replied media: {dl_err}")
                            await progress_download.edit(f"❌ **Failed to download media:** {dl_err}")
                            return
                        finally:
                            try:
                                await progress_download.delete()
                            except Exception:
                                pass
                
                if not query and not replied_audio:
                    await event.reply("❌ Please provide a song/video name/link, or reply to an audio file.\nFormat: `/play <songname>` or `/vplay <songname>`")
                    return
                    
                play_type = "video" if cmd == "/vplay" else "audio"
                
                vc_bots = []
                for p in running_phones:
                    bot_obj = userbot_manager._running_bots[p]
                    if getattr(bot_obj, "current_vc_chat_id", None):
                        vc_bots.append((p, bot_obj))
                        
                if not vc_bots:
                    await event.reply("❌ None of your running userbots are in a Voice Chat. Make them join a VC first!")
                    return
                    
                progress_msg = await event.reply(f"⏳ **Starting play on {len(vc_bots)} userbot(s) concurrently...**")
                
                async def _play_one_concurrent(p, bot_obj):
                    return await bot_obj.play_song(query, play_type=play_type, local_file=local_file_path, title=audio_title, duration=audio_duration)
                    
                results = await asyncio.gather(*[_play_one_concurrent(p, bot) for p, bot in vc_bots], return_exceptions=True)
                await progress_msg.delete()
                
                success_count = 0
                song_info_global = None
                error_details = []
                for res in results:
                    if isinstance(res, Exception):
                        error_details.append(str(res))
                    elif res[0]:
                        success_count += 1
                        song_info_global = res[2]
                    else:
                        error_details.append(res[1])
                
                if success_count > 0 and song_info_global:
                    caption = (
                        f"> 🎵 **Now Playing ({play_type.capitalize()} Mode)**\n"
                        f"> \n"
                        f"> • **Title**: `{song_info_global['title']}`\n"
                        f"> • **Duration**: `{song_info_global['duration']}s`\n"
                        f"> • **Requested by**: [{event.sender.first_name or 'User'}](tg://user?id={user_id})\n"
                        f"> \n"
                        f"> 🎧 _Playing on {success_count} userbot(s) in Voice Chats!_"
                    )
                    autoplay_on = _np_autoplay_state.get(user_id, False)
                    autoplay_label = "🔁 Autoplay: ON" if autoplay_on else "➡️ Autoplay: OFF"
                    np_buttons = [
                        [
                            Button.inline("⏭️ Skip Song", data=f"np_skip_all_{user_id}"),
                            Button.inline("🛑 End Song", data=f"np_end_all_{user_id}"),
                        ],
                        [
                            Button.inline(autoplay_label, data=f"np_autoplay_{user_id}"),
                        ],
                    ]
                    sent_msg = None
                    try:
                        sent_msg = await event.reply(caption, file=song_info_global["thumb"], buttons=np_buttons)
                    except Exception:
                        try:
                            sent_msg = await event.reply(caption, buttons=np_buttons)
                        except Exception:
                            pass
                            
                    if sent_msg:
                        _song_duration = song_info_global["duration"]
                        _song_query = query or song_info_global.get("title", "")
                        _song_play_type = play_type
                        _song_file = local_file_path
                        _song_title = audio_title
                        _song_dur_orig = audio_duration
                        
                        async def auto_delete_and_autoplay():
                            await asyncio.sleep(_song_duration)
                            try:
                                await client.delete_messages(event.chat_id, sent_msg.id)
                            except Exception:
                                pass
                            # Autoplay: replay same song if enabled
                            if _np_autoplay_state.get(user_id, False):
                                try:
                                    vc_bots_now = [(p, userbot_manager._running_bots[p]) for p in [
                                        s["phone"] for s in database.get_sessions(user_id)
                                        if userbot_manager.is_bot_running(s["phone"])
                                    ] if getattr(userbot_manager._running_bots.get(p), "current_vc_chat_id", None)]
                                    if vc_bots_now:
                                        await asyncio.gather(
                                            *[bot.play_song(_song_query, play_type=_song_play_type,
                                                            local_file=_song_file, title=_song_title,
                                                            duration=_song_dur_orig)
                                              for _, bot in vc_bots_now],
                                            return_exceptions=True
                                        )
                                except Exception as ap_err:
                                    logger.warning(f"Autoplay loop failed: {ap_err}")
                            file_path_cleanup = song_info_global.get("file_path")
                            if file_path_cleanup and os.path.exists(file_path_cleanup) and "silence" not in file_path_cleanup:
                                try:
                                    os.remove(file_path_cleanup)
                                    logger.info(f"Deleted local song file: {file_path_cleanup}")
                                except Exception as e:
                                    logger.warning(f"Could not delete local file {file_path_cleanup}: {e}")
                        asyncio.create_task(auto_delete_and_autoplay())
                else:
                    detailed_error = error_details[0] if error_details else "Failed to play on any active Voice Chat."
                    await event.reply(f"❌ **Failed to play:** {detailed_error}")
                return

        if user_id not in _bot_action_states:
            return
            
        state = _bot_action_states.pop(user_id)
        phone = state.get("phone")
        action = state["action"]
        
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        flash = None
        
        # --- Handle All Slots Dashboard Text Actions ---
        if action == "WAITING_FOR_ALL_VC_LINK":
            link = event.text.strip()
            if not link:
                await event.reply("❌ Chat ID/Username/Link cannot be empty.")
                return
                
            sessions = database.get_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("❌ No userbots are currently running. Please start your userbots first.")
                return
                
            progress_msg = await event.reply(f"⏳ **Joining Voice Chat on {len(running_phones)} running userbots concurrently...**")
            
            async def _join_vc_concurrent(phone_num):
                bot_obj = userbot_manager._running_bots[phone_num]
                success, msg = await bot_obj.join_voice_chat(link)
                return phone_num, success, msg
                
            results = await asyncio.gather(*[_join_vc_concurrent(p) for p in running_phones], return_exceptions=True)
            await progress_msg.delete()
            
            success_count = 0
            fail_msgs = []
            for res in results:
                if isinstance(res, Exception):
                    fail_msgs.append(f"⚠️ Task Error: {res}")
                    continue
                phone_num, success, msg = res
                if success:
                    success_count += 1
                else:
                    fail_msgs.append(f"📞 `{phone_num}`: {msg}")
                    
            flash = f"🎙️ **VC Join Results**:\nJoined: {success_count}/{len(running_phones)}"
            if fail_msgs:
                flash += f"\nErrors:\n" + "\n".join(fail_msgs)
                
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return

        elif action == "WAITING_FOR_ALL_VC_GRP_LINK":
            link = event.text.strip()
            if not link:
                await event.reply("❌ Group invite link cannot be empty.")
                return
                
            sessions = database.get_sessions(user_id)
            if not sessions:
                await event.reply("❌ No userbot slots found.")
                return
                
            progress_msg = await event.reply(f"⏳ **All {len(sessions)} userbots joining group...**")
            
            async def _join_grp_concurrent(s):
                phone_num = s["phone"]
                # Auto-start if stopped
                if not userbot_manager.is_bot_running(phone_num):
                    await userbot_manager.start_userbot(phone_num)
                    
                bot_obj = userbot_manager._running_bots.get(phone_num)
                if bot_obj:
                    success = await join_channel_single(bot_obj.client, link)
                    return phone_num, success
                return phone_num, False
                
            results = await asyncio.gather(*[_join_grp_concurrent(s) for s in sessions], return_exceptions=True)
            await progress_msg.delete()
            
            success_count = 0
            for res in results:
                if not isinstance(res, Exception) and res[1]:
                    success_count += 1
                    
            flash = f"🔗 **Group Join Results**:\nJoined: {success_count}/{len(sessions)} userbots successfully!"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return

        elif action == "WAITING_FOR_ALL_LEAVE_GRP":
            link = event.text.strip()
            if not link:
                await event.reply("❌ Input cannot be empty.")
                return
                
            sessions = database.get_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("❌ No userbots are currently running.")
                return
                
            progress_msg = await event.reply(f"⏳ **Leaving group concurrently on {len(running_phones)} userbots...**")
            
            async def _leave_grp_concurrent(phone_num):
                bot_obj = userbot_manager._running_bots[phone_num]
                success = await leave_chat_single(bot_obj.client, link)
                return phone_num, success
                
            results = await asyncio.gather(*[_leave_grp_concurrent(p) for p in running_phones], return_exceptions=True)
            await progress_msg.delete()
            
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r[1])
            flash = f"❌ **Group Leave Results**:\nLeft: {success_count}/{len(running_phones)} userbots successfully!"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return
            
        elif action == "WAITING_FOR_ALL_BROADCAST":
            broadcast_msg = event.text
            sessions = database.get_sessions(user_id)
            for s in sessions:
                s.setdefault("settings", {})["broadcast_msg"] = broadcast_msg
                database.save_session(s)
                if userbot_manager.is_bot_running(s["phone"]):
                    userbot_manager.reload_bot_settings(s["phone"])
            flash = "✉️ **Broadcast message updated for all bots!**"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return
            
        elif action == "WAITING_FOR_ALL_WELCOME":
            welcome_msg = event.text
            sessions = database.get_sessions(user_id)
            for s in sessions:
                s.setdefault("settings", {})["welcome_msg"] = welcome_msg
                database.save_session(s)
                if userbot_manager.is_bot_running(s["phone"]):
                    userbot_manager.reload_bot_settings(s["phone"])
            flash = "👋 **Welcome message updated for all bots!**"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return

        elif action == "WAITING_FOR_ALL_MULTI_WELCOME":
            msgs = [x.strip() for x in event.text.split(",") if x.strip()]
            if not msgs:
                await event.reply("❌ Input cannot be empty.")
                return
            sessions = database.get_sessions(user_id)
            for s in sessions:
                s.setdefault("settings", {})["welcome_messages"] = msgs
                database.save_session(s)
                if userbot_manager.is_bot_running(s["phone"]):
                    userbot_manager.reload_bot_settings(s["phone"])
            flash = "👋 **Multiple welcome messages updated for all bots!**"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return
 
        elif action == "WAITING_FOR_ALL_CLONE_TARGET":
            target = event.text.strip()
            if not target:
                await event.reply("❌ Target cannot be empty. Please enter a valid username/ID.")
                return
                
            clone_type = state.get("clone_type", "complete")
            sessions = database.get_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("❌ No userbots are currently running. Please start your userbots first.")
                return
                
            progress_msg = await event.reply(f"⏳ **Cloning profile details on {len(running_phones)} running userbots concurrently...**")
            
            async def _clone_concurrent(phone_num):
                return await userbot_manager.clone_profile(phone_num, target, clone_type=clone_type)
                
            results = await asyncio.gather(*[_clone_concurrent(p) for p in running_phones], return_exceptions=True)
            await progress_msg.delete()
            
            success_count = 0
            for res in results:
                if not isinstance(res, Exception) and res[0]:
                    success_count += 1
                    
            flash = f"👤 **Profile Cloning Results**:\nCloned successfully on {success_count}/{len(running_phones)} userbots!"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return
 
        elif action == "WAITING_FOR_ALL_NAME":
            new_name = event.text.strip()
            if not new_name:
                await event.reply("❌ Name cannot be empty.")
                return
                
            sessions = database.get_sessions(user_id)
            updated_count = 0
            for s in sessions:
                phone_num = s["phone"]
                s["name"] = new_name
                database.save_session(s)
                if userbot_manager.is_bot_running(phone_num):
                    try:
                        from telethon.tl.functions.account import UpdateProfileRequest
                        bot_obj = userbot_manager._running_bots[phone_num]
                        asyncio.create_task(bot_obj.client(UpdateProfileRequest(first_name=new_name)))
                    except Exception:
                        pass
                updated_count += 1
            flash = f"✏️ **Updated name to '{new_name}' for {updated_count} userbots!**"
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return
 
        elif action == "WAITING_FOR_ALL_CUSTOM_INTERVAL":
            val_str = event.text.strip()
            if val_str.isdigit() and int(val_str) >= 60:
                val = int(val_str)
                sessions = database.get_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["broadcast_interval"] = val
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s["phone"])
                flash = f"⏱️ **Interval updated to {val}s for all bots!**"
                await show_all_slots_dashboard(event, user_id, flash_message=flash)
                return
            else:
                await event.reply(utils.get_text("interval_invalid", lang))
                return
 
        elif action == "WAITING_FOR_ALL_CUSTOM_DELAY":
            val_str = event.text.strip()
            if val_str.isdigit() and 2 <= int(val_str) <= 300:
                val = int(val_str)
                sessions = database.get_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["inter_group_delay"] = val
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s["phone"])
                flash = f"⏱️ **Inter-Group Delay updated to {val}s for all bots!**"
                await show_all_slots_dashboard(event, user_id, flash_message=flash)
                return
            else:
                await event.reply(utils.get_text("inter_delay_invalid", lang))
                return
 
        elif action == "WAITING_FOR_ALL_MULTI_MSG":
            raw_text = event.text
            msgs = [m.strip() for m in raw_text.split(",") if m.strip()]
            if msgs:
                sessions = database.get_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["broadcast_messages"] = msgs
                    database.save_session(s)
                    if userbot_manager.is_bot_running(s["phone"]):
                        userbot_manager.reload_bot_settings(s["phone"])
                flash = f"✅ **Randomized messages updated for all bots ({len(msgs)} msgs)!**"
                await show_all_slots_dashboard(event, user_id, flash_message=flash)
                return
            else:
                await event.reply("❌ Message list cannot be empty. Separate messages with commas `,`.")
                return

        elif action == "WAITING_FOR_ALL_AUTO_REPLY_MSGS":
            raw_text = event.text
            msgs = [m.strip() for m in raw_text.split(",") if m.strip()]
            if msgs:
                sessions = database.get_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["auto_reply_messages"] = msgs
                    s["settings"]["auto_reply"] = True
                    database.save_session(s)
                    if userbot_manager.is_bot_running(s["phone"]):
                        userbot_manager.reload_bot_settings(s["phone"])
                flash = f"✅ **Tag Auto-Reply messages updated for all bots ({len(msgs)} msgs)!**"
                await show_all_slots_dashboard(event, user_id, flash_message=flash)
                return
            else:
                await event.reply(utils.get_text("auto_reply_invalid", lang))
                return

        elif action == "WAITING_FOR_ALL_RUN_TIMER":
            raw_text = event.text.strip().lower()
            try:
                if raw_text.endswith("m"):
                    minutes = float(raw_text[:-1])
                    seconds = minutes * 60
                else:
                    hours = float(raw_text)
                    seconds = hours * 3600
                    
                if seconds == 0:
                    sessions = database.get_sessions(user_id)
                    for s in sessions:
                        if "run_expiry" in s:
                            del s["run_expiry"]
                        database.save_session(s)
                    flash = "✅ **Run Timer disabled for all bots!**"
                else:
                    import time
                    expiry_time = time.time() + seconds
                    sessions = database.get_sessions(user_id)
                    for s in sessions:
                        s["run_expiry"] = expiry_time
                        database.save_session(s)
                    flash = f"✅ **Run Timer set! All bots will stop after {raw_text}.**"
                
                await show_all_slots_dashboard(event, user_id, flash_message=flash)
                return
            except ValueError:
                await event.reply("❌ Invalid format. Please send a number (e.g., `2` for hours, `30m` for minutes, `0` to disable).")
                return
        elif action == "WAITING_FOR_ALL_SONG":
            # Extract media info using extract_media_info
            media_obj, audio_title, audio_duration = extract_media_info(event.message)
            is_audio_file = False
            local_file_path = None
            
            if media_obj:
                is_audio_file = True
                progress_msg = await event.reply("📥 **Downloading uploaded media...**\n━━━━━━━━━━━━━━━━━━━━\n📊 Progress: `[░░░░░░░░░░] 0.0%`")
                os.makedirs("downloads", exist_ok=True)
                try:
                    local_file_path = await client.download_media(
                        event.message, 
                        file="downloads/",
                        progress_callback=lambda c, t: download_progress_sync(c, t, progress_msg, "Downloading uploaded media")
                    )
                except Exception as dl_err:
                    logger.error(f"Failed to download media: {dl_err}")
                    await progress_msg.edit(f"❌ **Failed to download media:** {dl_err}")
                    return
                finally:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
            
            query = None
            if not is_audio_file:
                query = event.text.strip() if event.text else ""
                if not query:
                    await event.reply("❌ Please provide a song query or send an audio file.")
                    return
                    
            sessions = database.get_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            
            vc_bots = []
            for p in running_phones:
                bot_obj = userbot_manager._running_bots[p]
                if getattr(bot_obj, "current_vc_chat_id", None):
                    vc_bots.append((p, bot_obj))
                    
            if not vc_bots:
                await event.reply("❌ No running userbots are in a Voice Chat.")
                return
                
            progress_msg = await event.reply(f"⏳ **Starting play on {len(vc_bots)} userbots concurrently...**")
            
            async def _play_one_all_concurrent(p, bot_obj):
                return await bot_obj.play_song(query, play_type="audio", local_file=local_file_path, title=audio_title, duration=audio_duration)
                
            results = await asyncio.gather(*[_play_one_all_concurrent(p, bot) for p, bot in vc_bots], return_exceptions=True)
            await progress_msg.delete()
            
            success_count = 0
            song_info_global = None
            for res in results:
                if not isinstance(res, Exception) and res[0]:
                    success_count += 1
                    song_info_global = res[2]
                    
            if success_count > 0 and song_info_global:
                caption = (
                    f"> 🎵 **Now Playing (All Slots)**\n"
                    f"> \n"
                    f"> • **Title**: `{song_info_global['title']}`\n"
                    f"> • **Duration**: `{song_info_global['duration']}s`\n"
                    f"> • **Requested by**: [{user.get('name', 'User')}](tg://user?id={user_id})\n"
                    f"> \n"
                    f"> 🎧 _Playing on {success_count} userbot(s) in Voice Chats!_"
                )
                sent_msg = None
                try:
                    sent_msg = await event.reply(caption, file=song_info_global["thumb"])
                except Exception:
                    try:
                        sent_msg = await event.reply(caption)
                    except Exception:
                        pass
                
                if sent_msg:
                    async def auto_delete():
                        await asyncio.sleep(song_info_global["duration"])
                        try:
                            await client.delete_messages(event.chat_id, sent_msg.id)
                        except Exception:
                            pass
                        file_path = song_info_global.get("file_path")
                        if file_path and os.path.exists(file_path) and "silence.mp3" not in file_path:
                            try:
                                os.remove(file_path)
                                logger.info(f"Deleted local song file: {file_path}")
                            except Exception as e:
                                logger.warning(f"Could not delete local file {file_path}: {e}")
                    asyncio.create_task(auto_delete())
                    
                flash = f"✅ **Playing song**: {song_info_global['title']}"
            else:
                flash = "❌ **Failed to play song on any userbot.**"
                
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return
            
        # --- Handle Single Bot Actions ---
        if not phone:
            await event.reply("❌ Session not found.")
            return
            
        sess = database.get_session(phone)
        if not sess or str(sess.get("user_id")) != str(user_id):
            await event.reply("❌ Session error.")
            return
            
        # 1. Broadcast Message
        if action == "WAITING_FOR_BROADCAST":
            sess["settings"]["broadcast_msg"] = event.text
            database.save_session(sess)
            flash = "✉️ **Broadcast message updated successfully!**"
            
        elif action == "WAITING_FOR_RUN_TIMER":
            raw_text = event.text.strip().lower()
            try:
                if raw_text.endswith("m"):
                    minutes = float(raw_text[:-1])
                    seconds = minutes * 60
                else:
                    hours = float(raw_text)
                    seconds = hours * 3600
                    
                if seconds == 0:
                    if "run_expiry" in sess:
                        del sess["run_expiry"]
                    database.save_session(sess)
                    flash = "✅ **Run Timer disabled!**"
                else:
                    import time
                    expiry_time = time.time() + seconds
                    sess["run_expiry"] = expiry_time
                    database.save_session(sess)
                    flash = f"✅ **Run Timer set! Bot will stop after {raw_text}.**"
            except ValueError:
                await event.reply("❌ Invalid format. Please send a number (e.g., `2` for hours, `30m` for minutes, `0` to disable).")
                return
            
        # 2. Welcome Message
        elif action == "WAITING_FOR_WELCOME":
            sess["settings"]["welcome_msg"] = event.text
            database.save_session(sess)
            flash = "👋 **Welcome message updated successfully!**"
            
        # 2.b Multiple Welcome Messages
        elif action == "WAITING_FOR_MULTI_WELCOME":
            msgs = [x.strip() for x in event.text.split(",") if x.strip()]
            if not msgs:
                await event.reply("❌ Input cannot be empty.")
                return
            sess["settings"]["welcome_messages"] = msgs
            database.save_session(sess)
            flash = "👋 **Multiple welcome messages updated successfully!**"
            
        # 2.5 Join VC Link
        elif action == "WAITING_FOR_VC_LINK":
            link = event.text.strip()
            if not link:
                await event.reply("❌ Chat ID/Username/Link cannot be empty.")
                return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("❌ Userbot is not running. Please start it first.")
                return
                
            progress_msg = await event.reply("⏳ **Joining Voice Chat, please wait...**")
            bot_obj = userbot_manager._running_bots[phone]
            success, msg = await bot_obj.join_voice_chat(link)
            await progress_msg.delete()
            
            if success:
                flash = f"✅ **Joined Voice Chat!**\n{msg}"
            else:
                flash = f"❌ **Failed to join VC:** {msg}"

        # 2.6 Join Group via Link (Single Bot)
        elif action == "WAITING_FOR_VC_GRP_LINK":
            link = event.text.strip()
            if not link:
                await event.reply("❌ Group invite link cannot be empty.")
                return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("❌ Userbot is not running.")
                return
                
            progress_msg = await event.reply("⏳ **Joining Group, please wait...**")
            bot_obj = userbot_manager._running_bots[phone]
            success = await join_channel_single(bot_obj.client, link)
            await progress_msg.delete()
            
            if success:
                flash = f"✅ **Successfully joined the group!**\nNow you can click '🎙️ Join VC' to enter the Voice Chat."
            else:
                flash = "❌ **Failed to join the group. Make sure the link is valid.**"
                
            await show_bot_dashboard(event, phone, user_id, flash_message=flash)
            return

        # 2.7 Leave Group via Link/ID (Single Bot)
        elif action == "WAITING_FOR_LEAVE_GRP":
            link = event.text.strip()
            if not link:
                await event.reply("❌ Input cannot be empty.")
                return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("❌ Userbot is not running.")
                return
                
            progress_msg = await event.reply("⏳ **Leaving group/channel, please wait...**")
            bot_obj = userbot_manager._running_bots[phone]
            success = await leave_chat_single(bot_obj.client, link)
            await progress_msg.delete()
            
            if success:
                flash = f"✅ **Successfully left the group/channel!**"
            else:
                flash = "❌ **Failed to leave the group. Check link/ID.**"
                
            await show_bot_dashboard(event, phone, user_id, flash_message=flash)
            return
                
        # 3. Clone Profile
        elif action == "WAITING_FOR_CLONE_TARGET":
            target = event.text.strip()
            if not target:
                await event.reply("❌ Target cannot be empty. Please enter a valid username/ID.")
                return
                
            clone_type = state.get("clone_type", "complete")
            progress_msg = await event.reply("⏳ **Cloning profile details, please wait...**")
            success, msg = await userbot_manager.clone_profile(phone, target, clone_type=clone_type)
            await progress_msg.delete()
            
            if success:
                flash = f"✅ **Profile successfully cloned!**\n{msg}"
            else:
                flash = f"❌ **Cloning failed:** {msg}"
 
        # 4. Change Name
        elif action == "WAITING_FOR_NAME":
            new_name = event.text.strip()
            if new_name:
                sess["name"] = new_name
                database.save_session(sess)
                
                # If running, update profile name
                if userbot_manager.is_bot_running(phone):
                    try:
                        from telethon.tl.functions.account import UpdateProfileRequest
                        bot_obj = userbot_manager._running_bots[phone]
                        asyncio.create_task(bot_obj.client(UpdateProfileRequest(first_name=new_name)))
                    except Exception as e:
                        logger.warning(f"Could not change userbot profile name: {e}")
                        
                flash = f"✏️ **Name updated to: {new_name}**"
            else:
                await event.reply("❌ Name cannot be empty.")
                return
                
        # 5. Custom Interval
        elif action == "WAITING_FOR_CUSTOM_INTERVAL":
            val_str = event.text.strip()
            if val_str.isdigit() and int(val_str) >= 60:
                val = int(val_str)
                sess["settings"]["broadcast_interval"] = val
                database.save_session(sess)
                flash = f"⏱️ **Interval updated to {val}s**"
            else:
                await event.reply(utils.get_text("interval_invalid", lang))
                return
 
        # 5.5 Custom Delay
        elif action == "WAITING_FOR_CUSTOM_DELAY":
            val_str = event.text.strip()
            if val_str.isdigit() and 2 <= int(val_str) <= 300:
                val = int(val_str)
                sess.setdefault("settings", {})["inter_group_delay"] = val
                database.save_session(sess)
                flash = f"⏱️ **Inter-Group Delay updated to {val}s**"
            else:
                await event.reply(utils.get_text("inter_delay_invalid", lang))
                return
 
        # 5.6 Multiple Messages
        elif action == "WAITING_FOR_MULTI_MSG":
            raw_text = event.text
            msgs = [m.strip() for m in raw_text.split(",") if m.strip()]
            if msgs:
                sess.setdefault("settings", {})["broadcast_messages"] = msgs
                database.save_session(sess)
                flash = f"✅ **Successfully set {len(msgs)} messages for randomized broadcast!**"
            else:
                await event.reply("❌ Message list cannot be empty. Separate messages with commas `,`.")
                return

        # 5.6.4 Single Auto Reply Message
        elif action == "WAITING_FOR_AUTO_REPLY_SINGLE":
            msg_text = event.text.strip()
            if msg_text:
                sess.setdefault("settings", {})["auto_reply_msg"] = msg_text
                sess["settings"]["auto_reply"] = True
                database.save_session(sess)
                userbot_manager.reload_bot_settings(phone)
                flash = "💬 **Single Tag Auto-Reply message updated!**"
            else:
                await event.reply("❌ Message cannot be empty.")
                return

        # 5.6.5 Auto Reply Messages
        elif action == "WAITING_FOR_AUTO_REPLY_MSGS":
            raw_text = event.text
            msgs = [m.strip() for m in raw_text.split(",") if m.strip()]
            if msgs:
                sess.setdefault("settings", {})["auto_reply_messages"] = msgs
                sess["settings"]["auto_reply"] = True  # Auto-enable when messages are set
                database.save_session(sess)
                userbot_manager.reload_bot_settings(phone)
                flash = utils.get_text("auto_reply_updated", lang, count=len(msgs))
            else:
                await event.reply(utils.get_text("auto_reply_invalid", lang))
                return
 
        # 5.7 Play Song query
        elif action == "WAITING_FOR_SONG":
            # Extract media info using extract_media_info
            media_obj, audio_title, audio_duration = extract_media_info(event.message)
            is_audio_file = False
            local_file_path = None
            
            if media_obj:
                is_audio_file = True
                progress_msg = await event.reply("📥 **Downloading uploaded media...**\n━━━━━━━━━━━━━━━━━━━━\n📊 Progress: `[░░░░░░░░░░] 0.0%`")
                os.makedirs("downloads", exist_ok=True)
                try:
                    local_file_path = await client.download_media(
                        event.message, 
                        file="downloads/",
                        progress_callback=lambda c, t: download_progress_sync(c, t, progress_msg, "Downloading uploaded media")
                    )
                except Exception as dl_err:
                    logger.error(f"Failed to download media: {dl_err}")
                    await progress_msg.edit(f"❌ **Failed to download media:** {dl_err}")
                    return
                finally:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
            
            query = None
            if not is_audio_file:
                query = event.text.strip() if event.text else ""
                if not query:
                    await event.reply("❌ Please provide a song query or send an audio file.")
                    return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("❌ Userbot is not running.")
                return
                
            bot_obj = userbot_manager._running_bots[phone]
            progress_msg = await event.reply("⏳ **Playing song, please wait...**")
            success, msg, song_info = await bot_obj.play_song(query, play_type="audio", local_file=local_file_path, title=audio_title, duration=audio_duration)
            await progress_msg.delete()
            
            if success and song_info:
                caption = (
                    f"> 🎵 **Now Playing**\n"
                    f"> \n"
                    f"> • **Title**: `{song_info['title']}`\n"
                    f"> • **Duration**: `{song_info['duration']}s`\n"
                    f"> • **Requested by**: [{user.get('name', 'User')}](tg://user?id={user_id})\n"
                    f"> \n"
                    f"> 🎧 _Playing in voice chat for userbot `{phone}`_"
                )
                sent_msg = None
                try:
                    sent_msg = await event.reply(caption, file=song_info["thumb"])
                except Exception:
                    try:
                        sent_msg = await event.reply(caption)
                    except Exception:
                        pass
                
                if sent_msg:
                    async def auto_delete():
                        await asyncio.sleep(song_info["duration"])
                        try:
                            await client.delete_messages(event.chat_id, sent_msg.id)
                        except Exception:
                            pass
                        file_path = song_info.get("file_path")
                        if file_path and os.path.exists(file_path) and "silence.mp3" not in file_path:
                            try:
                                os.remove(file_path)
                                logger.info(f"Deleted local song file: {file_path}")
                            except Exception as e:
                                logger.warning(f"Could not delete local file {file_path}: {e}")
                    asyncio.create_task(auto_delete())
                    
                flash = f"✅ **Playing song**: {song_info['title']}"
            else:
                flash = f"❌ **Failed to play**: {msg}"
                
        # Return to dashboard showing updated stats and flash notification
        userbot_manager.reload_bot_settings(phone)
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)
