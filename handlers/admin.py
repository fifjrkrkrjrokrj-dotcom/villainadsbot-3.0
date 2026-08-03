import logging
from telethon import events
import database
import config
import utils

logger = logging.getLogger(__name__)

# In-memory dictionary containing active prompt states for administrator actions
# Structure: { user_id: str } (where value is the WAITING_FOR_... action)
_admin_action_states = {}
_admin_plan_temp = {}

def check_admin(user_id: int) -> bool:
    """
    Checks if a user is an administrator (either defined in config env or DB).
    """
    global_settings = database.get_global_settings()
    admins = global_settings.get("admins", [])
    return user_id in admins or user_id in config.ORIGINAL_ADMIN_IDS

async def show_admin_panel(event, user_id: int):
    """
    Renders the administrator control panel.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    if not check_admin(user_id):
        await event.respond(utils.get_text("error_not_admin", lang))
        return
        
    global_settings = database.get_global_settings()
    maint_text = "🔴 Disable Maintenance" if global_settings.get("maintenance_mode", False) else "🟢 Enable Maintenance"
    
    text = utils.get_text("admin_title", lang)
    buttons = [
        [
            utils.styled_button(utils.get_text("btn_manage_plans", lang), "admin_manage_plans", style="primary"),
            utils.styled_button(utils.get_text("btn_set_fj", lang), "admin_set_fj", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_set_lg", lang), "admin_set_lg", style="primary"),
            utils.styled_button(utils.get_text("btn_set_bu", lang), "admin_set_bu", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_set_bd", lang), "admin_set_bd", style="primary"),
            utils.styled_button(utils.get_text("btn_set_imgs", lang), "admin_set_imgs", style="primary")
        ],
        [
            utils.styled_button("🎨 Branding Settings", "admin_branding_settings", style="primary")
        ],
        [
            utils.styled_button("🏦 Set UPI ID", "admin_set_upi", style="primary"),
            utils.styled_button("🪙 Set USDT", "admin_set_usdt", style="primary"),
            utils.styled_button("💎 Set TON", "admin_set_ton", style="primary")
        ],
        [
            utils.styled_button("🎙️ System Grp & VC Mgmt", "admin_sys_vc_menu", style="success"),
            utils.styled_button("🔗 Auto-Joins", "admin_set_ub_joins", style="primary"),
            utils.styled_button("👤 User Manager", "admin_manage_users", style="primary")
        ],
        [
            utils.styled_button("👑 Control All UserBots (Owner)", "admin_owner_all_bots", style="success")
        ],
        [
            utils.styled_button("📊 Set Commission", "admin_set_comm", style="primary"),
            utils.styled_button("📢 Broadcast", "admin_broadcast", style="primary"),
            utils.styled_button(maint_text, "admin_toggle_maint", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_manage_admins", lang), "admin_manage_admins", style="primary"),
            utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")
        ]
    ]
    
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)

def register_handlers(client):
    
    # ------------------ Navigation ------------------
    @client.on(events.NewMessage(pattern="/admin"))
    async def admin_cmd(event):
        if not event.is_private:
            return
        import utils
        if await utils.guard(event, client):
            return
        await show_admin_panel(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^menu_admin$"))
    async def admin_menu_callback(event):
        await show_admin_panel(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^admin_sys_vc_menu$"))
    async def admin_sys_vc_menu_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        all_sessions = database.get_sessions()
        total = len(all_sessions)
        
        text = (
            f"🎙️ **SYSTEM-WIDE BOT MANAGEMENT**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"> **Total Bots in DB**: **{total}**\n\n"
            f"⚠️ **WARNING**: These actions will command ALL {total} bots in the database.\n"
            f"If a bot is currently STOPPED, it will be temporarily started to execute the action.\n\n"
            f"👥 **System Group Actions**:\n"
            f"• Join Group: All {total} bots join a group via link.\n"
            f"• Leave Group: All {total} bots leave a group/channel.\n\n"
            f"🎙️ **System VC Actions**:\n"
            f"• Join VC: Connect all {total} bots to group voice chat.\n"
            f"• Leave VC: Disconnect all {total} bots from voice chat.\n\n"
            f"🎵 **System Media Actions**:\n"
            f"• Play Song: Stream audio/video on all {total} bots."
        )
        
        buttons = [
            [
                utils.styled_button("🔗 System Join Group", "admin_sys_join_grp", style="success"),
                utils.styled_button("❌ System Leave Group", "admin_sys_leave_grp", style="danger")
            ],
            [
                utils.styled_button("🎙️ System Join VC", "admin_sys_join_vc", style="success"),
                utils.styled_button("🔴 System Leave VC", "admin_sys_leave_vc", style="danger")
            ],
            [
                utils.styled_button("🎵 System Play Song", "admin_sys_play_song", style="primary")
            ],
            [
                utils.styled_button("🔙 Back to Admin Panel", "menu_admin", style="primary")
            ]
        ]
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    # Reusable prompt function for system actions
    async def _prompt_sys_action(event, action_key, title, instructions):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = action_key
        prompt_text = (
            f"👑 **{title}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{instructions}\n\n"
            f"⚠️ **ALL userbot sessions in the database (whether ON or OFF)** will execute this!"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_sys_vc_menu", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_sys_join_grp$"))
    async def admin_sys_join_grp_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_JOIN_GRP", "System Join Group", "> Send the **Group invite link** or **Username** below.")

    @client.on(events.CallbackQuery(pattern="^admin_sys_leave_grp$"))
    async def admin_sys_leave_grp_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_LEAVE_GRP", "System Leave Group", "> Send the **Group invite link** or **Username/ID** below to leave.")

    @client.on(events.CallbackQuery(pattern="^admin_sys_join_vc$"))
    async def admin_sys_join_vc_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_JOIN_VC", "System Join VC", "> Send the **Group invite link** or **Username/ID** below to join its active Voice Chat.")

    @client.on(events.CallbackQuery(pattern="^admin_sys_leave_vc$"))
    async def admin_sys_leave_vc_callback(event):
        # Leave VC does not need a prompt since it just disconnects from current VC
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = "EXECUTE_SYS_LEAVE_VC"
        prompt_text = "⚠️ Are you sure you want to disconnect ALL bots from their current Voice Chats?"
        buttons = [
            [utils.styled_button("✅ Confirm Leave VC (All)", "confirm_sys_leave_vc", style="danger")],
            [utils.styled_button("🔙 Cancel", "admin_sys_vc_menu", style="primary")]
        ]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^confirm_sys_leave_vc$"))
    async def confirm_sys_leave_vc_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id) or _admin_action_states.get(user_id) != "EXECUTE_SYS_LEAVE_VC":
            return
        
        all_sessions = database.get_sessions()
        total = len(all_sessions)
        progress_msg = await event.reply(f"⏳ **Disconnecting {total} bots from Voice Chats...**")
        
        import userbot_manager
        success_count = 0
        for s in all_sessions:
            phone_num = s["phone"]
            if userbot_manager.is_bot_running(phone_num):
                bot_obj = userbot_manager._running_bots.get(phone_num)
                if bot_obj and getattr(bot_obj, "current_vc_chat_id", None):
                    await bot_obj.leave_voice_chat()
                    success_count += 1
                    
        await progress_msg.delete()
        _admin_action_states.pop(user_id, None)
        await event.reply(f"✅ **System Leave VC Complete**\nDisconnected {success_count} bots.")
        await admin_sys_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_sys_play_song$"))
    async def admin_sys_play_song_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_PLAY_SONG", "System Play Song", "> Send the **Song Name**, **YouTube Link**, or `/play <name>` below to stream on all connected bots.")

    @client.on(events.CallbackQuery(pattern="^admin_owner_all_bots$"))
    async def admin_owner_all_bots_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        from handlers.my_bots import show_all_slots_dashboard
        await show_all_slots_dashboard(event, user_id, flash_message="👑 **Owner Panel**: Controlling ALL userbots in system!", fetch_all=True)

    @client.on(events.CallbackQuery(pattern="^cancel_admin_plan$"))
    async def cancel_admin_plan_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states.pop(user_id, None)
        _admin_plan_temp.pop(user_id, None)
        await admin_manage_plans_callback(event)

    @client.on(events.CallbackQuery(pattern="^cancel_admin_setting$"))
    async def cancel_admin_setting_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states.pop(user_id, None)
        await show_admin_panel(event, user_id)


    @client.on(events.CallbackQuery(pattern="^admin_manage_plans$"))
    async def admin_manage_plans_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        
        text = "📅 **Subscription Plans Management**\n\nConfigure custom duration-based slot options for your users.\n\n"
        if not plans:
            text += "_No plans configured yet._"
        else:
            text += "**Active Plans:**\n"
            for i, p in enumerate(plans, 1):
                text += f"{i}. **{p.get('button_name')}**\n" \
                        f"   • ID: `{p.get('id')}`\n" \
                        f"   • Duration: **{p.get('days')} days**\n" \
                        f"   • Price/account: **₹{p.get('price'):.2f}**\n\n"
                        
        buttons = [
            [
                utils.styled_button("➕ Add Plan", "admin_add_plan_start", style="success"),
                utils.styled_button("❌ Remove Plan", "admin_remove_plan_start", style="danger")
            ],
            [utils.styled_button("🔙 Back to Admin Panel", "menu_admin", style="primary")]
        ]
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_add_plan_start$"))
    async def admin_add_plan_start_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        _admin_action_states[user_id] = "WAITING_FOR_PLAN_DAYS"
        _admin_plan_temp[user_id] = {}
        
        prompt_text = utils.get_text("prompt_plan_days", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)


    @client.on(events.CallbackQuery(pattern="^admin_remove_plan_start$"))
    async def admin_remove_plan_start_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        
        if not plans:
            buttons = [[utils.styled_button("🔙 Back to Plans", "admin_manage_plans", style="primary")]]
            await event.respond("❌ **No subscription plans are currently configured.**", buttons=buttons)
            return
            
        text = "❌ **Select Plan to Remove**\n\nTap on any plan button below to delete it immediately:"
        buttons = []
        for plan in plans:
            btn_label = f"🗑️ {plan['button_name']} (₹{plan['price']:.0f} / {plan['days']} days)"
            buttons.append([
                utils.styled_button(btn_label, f"admin_remplan_id_{plan['id']}", style="danger")
            ])
        buttons.append([utils.styled_button("🔙 Back to Plans", "admin_manage_plans", style="primary")])
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_remplan_id_(.+)$"))
    async def admin_remplan_id_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        plan_id = event.pattern_match.group(1)
        
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        original_len = len(plans)
        global_settings["subscription_plans"] = [p for p in plans if p["id"] != plan_id]
        
        if len(global_settings["subscription_plans"]) < original_len:
            database.save_global_settings(global_settings)
            await event.answer("✅ Plan removed successfully!", alert=True)
        else:
            await event.answer("❌ Plan ID not found.", alert=True)
            
        await admin_remove_plan_start_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_manage_admins$"))
    async def manage_admins_menu(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        admins = global_settings.get("admins", [])
        admin_list = "\n".join([f"• `{a}`" for a in admins])
        text = f"👑 **Administrator Management**\n\n**Current Admins:**\n{admin_list}\n\nChoose an option below:"
        buttons = [
            [
                utils.styled_button("➕ Add Admin", "admin_add_admin", style="success"),
                utils.styled_button("➖ Remove Admin", "admin_rem_admin", style="danger")
            ],
            [utils.styled_button("🔙 Back to Admin Panel", "menu_admin", style="primary")]
        ]
        await event.respond(text, buttons=buttons)

    # ------------------ Button Actions ------------------
    @client.on(events.CallbackQuery(pattern="^admin_toggle_maint$"))
    async def admin_toggle_maint_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        global_settings = database.get_global_settings()
        global_settings["maintenance_mode"] = not global_settings.get("maintenance_mode", False)
        database.save_global_settings(global_settings)
        
        status_word = "enabled" if global_settings["maintenance_mode"] else "disabled"
        await event.answer(f"🔧 Maintenance Mode is now {status_word}.", alert=True)
        await show_admin_panel(event, user_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_(set_(price|fj|lg|bu|bd|imgs|upi|usdt|ton|ub_joins|comm)|join_all_sessions|add_admin|rem_admin|broadcast)$"))
    async def admin_setting_callback(event):
        action = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        # Register prompt state
        _admin_action_states[user_id] = f"WAITING_FOR_{action.upper()}"
        
        # Select prompt message key
        prompt_keys = {
            "set_price": "prompt_set_price",
            "set_fj": "prompt_set_fj",
            "set_lg": "prompt_set_lg",
            "set_bu": "prompt_set_bu",
            "set_bd": "prompt_set_bd",
            "set_imgs": "prompt_set_imgs",
            "set_upi": "prompt_set_upi",
            "set_usdt": "prompt_set_usdt",
            "set_ton": "prompt_set_ton",
            "set_ub_joins": "prompt_set_ub_joins",
            "set_comm": "prompt_set_comm",
            "add_admin": "prompt_add_admin",
            "rem_admin": "prompt_rem_admin"
        }
        
        prompt_key = prompt_keys.get(action, "error_generic")
        
        # Custom prompt display helper
        if action == "set_upi":
            prompt_text = "🏦 Send the new Admin UPI ID (e.g. `merchant@upi`):"
        elif action == "set_usdt":
            prompt_text = "🪙 Send the new USDT wallet address:"
        elif action == "set_ton":
            prompt_text = "💎 Send the new TON wallet address:"
        elif action == "set_ub_joins":
            prompt_text = "🔗 Send the new list of userbot auto-join links, separated by commas (or 'none'):"
        elif action == "join_all_sessions":
            prompt_text = "👥 **Join All Sessions**\n\nSend the invite link/username of the group or channel that all logged-in accounts should join:"
        elif action == "set_comm":
            prompt_text = "📊 Send the new referral commission rate (0.01 - 0.99 for 1%-99%):"
        elif action == "broadcast":
            prompt_text = "📢 **Global Broadcast**\n\nSend the message you want to broadcast to all users. You can send text, links, formatting, or media (photos/videos)."
        else:
            prompt_text = utils.get_text(prompt_key, lang)
            
        buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_setting", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)


    # ------------------ Admin Message Input Listener ------------------
    @client.on(events.NewMessage)
    async def admin_text_input_handler(event):
        if not event.is_private:
            return
            
        user_id = event.sender_id
        if user_id not in _admin_action_states:
            return
            
        if event.text.startswith("/start"):
            _admin_action_states.pop(user_id, None)
            _admin_plan_temp.pop(user_id, None)
            return
            
        action = _admin_action_states.pop(user_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.reply(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        success = False
        val_str = event.text.strip()
        
        try:
            # 0.1 Plan Days
            if action == "WAITING_FOR_PLAN_DAYS":
                days = int(val_str)
                if days <= 0:
                    raise ValueError("Days must be positive")
                _admin_plan_temp.setdefault(user_id, {})["days"] = days
                _admin_action_states[user_id] = "WAITING_FOR_PLAN_SLOTS"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
                await event.reply(utils.get_text("prompt_plan_slots", lang), buttons=buttons)
                return
                
            # 0.1.b Plan Slots
            elif action == "WAITING_FOR_PLAN_SLOTS":
                slots = int(val_str)
                if slots <= 0:
                    raise ValueError("Slots must be positive")
                _admin_plan_temp.setdefault(user_id, {})["slots"] = slots
                _admin_action_states[user_id] = "WAITING_FOR_PLAN_PRICE"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
                await event.reply(utils.get_text("prompt_plan_price", lang), buttons=buttons)
                return
                
            # 0.2 Plan Price
            elif action == "WAITING_FOR_PLAN_PRICE":
                price = float(val_str)
                if price <= 0:
                    raise ValueError("Price must be positive")
                _admin_plan_temp.setdefault(user_id, {})["price"] = price
                _admin_action_states[user_id] = "WAITING_FOR_PLAN_NAME"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
                await event.reply(utils.get_text("prompt_plan_name", lang), buttons=buttons)
                return
                
            # 0.3 Plan Name
            elif action == "WAITING_FOR_PLAN_NAME":
                name = val_str
                if not name:
                    raise ValueError("Name cannot be empty")
                
                temp_data = _admin_plan_temp.pop(user_id, None)
                if not temp_data or "days" not in temp_data or "slots" not in temp_data or "price" not in temp_data:
                    await event.reply("❌ State lost. Please start over.")
                    # Show manage plans sub-menu
                    class MockEvent:
                        def __init__(self, uid, ev):
                            self.sender_id = uid
                            self.respond = ev.respond
                            self.edit = ev.respond
                        async def answer(self, *args, **kwargs):
                            pass
                    await admin_manage_plans_callback(MockEvent(user_id, event))
                    return
                    
                days = temp_data["days"]
                slots = temp_data["slots"]
                price = temp_data["price"]
                
                import uuid
                plan_id = "plan_" + str(uuid.uuid4())[:6]
                
                plans = global_settings.setdefault("subscription_plans", [])
                plans.append({
                    "id": plan_id,
                    "days": days,
                    "slots": slots,
                    "price": price,
                    "button_name": name
                })
                database.save_global_settings(global_settings)
                
                await event.reply(
                    f"✅ **Subscription Plan Added Successfully!**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Plan ID: `{plan_id}`\n"
                    f"Name: **{name}**\n"
                    f"Days: **{days}**\n"
                    f"Slots count: **{slots}**\n"
                    f"Total Price: **₹{price:.2f}**"
                )
                
                # Show manage plans sub-menu
                class MockEvent:
                    def __init__(self, uid, ev):
                        self.sender_id = uid
                        self.respond = ev.respond
                        self.edit = ev.respond
                    async def answer(self, *args, **kwargs):
                        pass
                await admin_manage_plans_callback(MockEvent(user_id, event))
                return

            # 1. Set global Price per extra ID
            elif action == "WAITING_FOR_SET_PRICE":
                global_settings["price_per_id"] = float(val_str)
                success = True
                
            # 2. Set Force Join channels
            elif action == "WAITING_FOR_SET_FJ":
                if val_str.lower() == "none":
                    global_settings["force_join_links"] = []
                else:
                    global_settings["force_join_links"] = [x.strip() for x in val_str.split(",") if x.strip()]
                success = True
                
            # 3. Set log group ID
            elif action == "WAITING_FOR_SET_LG":
                # Must be an integer ID
                global_settings["log_group_id"] = int(val_str)
                success = True
                
            # 4. Set branding username
            elif action == "WAITING_FOR_SET_BU":
                if val_str.lower() == "none":
                    global_settings["branding_username"] = None
                else:
                    # Strip @ if present
                    global_settings["branding_username"] = val_str.replace("@", "")
                success = True
                
            # 5. Set branding duration
            elif action == "WAITING_FOR_SET_BD":
                global_settings["branding_duration"] = int(val_str)
                success = True
                
            # Set branding name suffix text
            elif action == "WAITING_FOR_BRAND_NAME_TXT":
                if val_str.lower() == "none":
                    global_settings["branding_name_text"] = None
                else:
                    global_settings["branding_name_text"] = val_str
                success = True
                
            # Set branding bio suffix text
            elif action == "WAITING_FOR_BRAND_BIO_TXT":
                if val_str.lower() == "none":
                    global_settings["branding_bio_text"] = None
                else:
                    global_settings["branding_bio_text"] = val_str
                success = True
                
            # 6. Set Images (Start, Ping, Help)
            elif action == "WAITING_FOR_SET_IMGS":
                parts = [p.strip() for p in val_str.split(",") if p.strip()]
                if len(parts) == 3:
                    global_settings["start_image"] = parts[0] if parts[0].lower() != "none" else None
                    global_settings["ping_image"] = parts[1] if parts[1].lower() != "none" else None
                    global_settings["help_image"] = parts[2] if parts[2].lower() != "none" else None
                    success = True
                else:
                    raise ValueError("Must provide 3 comma-separated URLs or File IDs (or 'none').")
                    
            # 6.1 Set UPI ID
            elif action == "WAITING_FOR_SET_UPI":
                global_settings["upi_id"] = val_str
                success = True
                
            # 6.2 Set USDT address
            elif action == "WAITING_FOR_SET_USDT":
                global_settings["usdt_bep20_address"] = val_str
                success = True
                
            # 6.2.1 Set TON address
            elif action == "WAITING_FOR_SET_TON":
                global_settings["ton_address"] = val_str
                success = True
                
            # 6.2.2 Set custom userbot auto-join links
            elif action == "WAITING_FOR_SET_UB_JOINS":
                if val_str.lower() == "none":
                    global_settings["userbot_auto_join_links"] = []
                else:
                    global_settings["userbot_auto_join_links"] = [x.strip() for x in val_str.split(",") if x.strip()]
                success = True
                
            # System-wide collective tasks for ALL DB accounts (running or stopped)
            elif action in ["WAITING_FOR_SYS_JOIN_GRP", "WAITING_FOR_SYS_LEAVE_GRP", "WAITING_FOR_SYS_JOIN_VC", "WAITING_FOR_SYS_PLAY_SONG"]:
                link = val_str
                all_sessions = database.get_sessions()
                total = len(all_sessions)
                if not total:
                    await event.reply("❌ No userbot sessions found in database.")
                    await admin_sys_vc_menu_callback(event)
                    return
                    
                action_name_map = {
                    "WAITING_FOR_SYS_JOIN_GRP": "Join Group",
                    "WAITING_FOR_SYS_LEAVE_GRP": "Leave Group",
                    "WAITING_FOR_SYS_JOIN_VC": "Join Voice Chat",
                    "WAITING_FOR_SYS_PLAY_SONG": "Play Song"
                }
                action_name = action_name_map[action]
                
                progress_msg = await event.reply(f"⏳ **Executing '{action_name}' on all {total} bots...**\nPlease wait, this may take a while as offline bots will be temporarily started.")
                
                from userbot import join_channel_single, leave_channel_single
                import userbot_manager
                
                async def _sys_action_one_db_account(s):
                    phone_num = s["phone"]
                    if not userbot_manager.is_bot_running(phone_num):
                        await userbot_manager.start_userbot(phone_num)
                    
                    bot_obj = userbot_manager._running_bots.get(phone_num)
                    if not bot_obj or not bot_obj.client:
                        return False
                        
                    if action == "WAITING_FOR_SYS_JOIN_GRP":
                        return await join_channel_single(bot_obj.client, link)
                    elif action == "WAITING_FOR_SYS_LEAVE_GRP":
                        return await leave_channel_single(bot_obj.client, link)
                    elif action == "WAITING_FOR_SYS_JOIN_VC":
                        try:
                            # Note: join_voice_chat sends progress messages by default, we catch any text prints or errors.
                            await bot_obj.join_voice_chat(link)
                            return True
                        except Exception as e:
                            logger.error(f"Error in System Join VC for {phone_num}: {e}")
                            return False
                    elif action == "WAITING_FOR_SYS_PLAY_SONG":
                        try:
                            # Use query processing from userbot (youtube download or direct play)
                            # We can just call play_song directly.
                            await bot_obj.play_song(link)
                            return True
                        except Exception as e:
                            logger.error(f"Error in System Play Song for {phone_num}: {e}")
                            return False
                    return False
                    
                results = await asyncio.gather(*[_sys_action_one_db_account(s) for s in all_sessions], return_exceptions=True)
                await progress_msg.delete()
                
                success_count = sum(1 for r in results if not isinstance(r, Exception) and r)
                fail_count = total - success_count
                
                report = (
                    f"📊 **System Action Report: {action_name}**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Input: {link}\n"
                    f"Total Accounts in DB: **{total}**\n"
                    f"✅ Success: **{success_count}**\n"
                    f"❌ Failed: **{fail_count}**"
                )
                await event.reply(report)
                _admin_action_states.pop(user_id, None)
                await admin_sys_vc_menu_callback(event)
                return
                
            # 6.3 Set referral commission
            elif action == "WAITING_FOR_SET_COMM":
                val = float(val_str)
                if 0.0 <= val <= 1.0:
                    global_settings["referral_commission"] = val
                    success = True
                else:
                    raise ValueError("Commission must be between 0.0 and 1.0")
                    
            # 6.3.b Global Broadcast
            elif action == "WAITING_FOR_BROADCAST":
                await event.reply("📢 **Starting broadcast...**\nPlease wait, sending the message to all users.")
                
                all_users = database.get_all_users()
                total_users = len(all_users)
                success_count = 0
                fail_count = 0
                
                import asyncio
                from telethon.errors import FloodWaitError
                
                for u in all_users:
                    uid = u.get("user_id")
                    if not uid:
                        continue
                    try:
                        await client.send_message(uid, event.message)
                        success_count += 1
                        await asyncio.sleep(0.05)  # Short delay to prevent flooding
                    except FloodWaitError as fwe:
                        logger.warning(f"Flood wait during broadcast: sleeping for {fwe.seconds}s")
                        await asyncio.sleep(fwe.seconds)
                        try:
                            await client.send_message(uid, event.message)
                            success_count += 1
                        except Exception as retry_err:
                            logger.error(f"Failed retry to {uid}: {retry_err}")
                            fail_count += 1
                    except Exception as err:
                        logger.warning(f"Failed to send broadcast to {uid}: {err}")
                        fail_count += 1
                        
                report = (
                    f"📢 **Broadcast Completion Report**\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"👥 Total Target Users: **{total_users}**\n"
                    f"✅ Successfully Sent: **{success_count}**\n"
                    f"❌ Failed (Blocked/Deleted): **{fail_count}**"
                )
                await event.reply(report)
                await show_admin_panel(event, user_id)
                return
                    
            # 6.4 User Management Search / Actions
            elif action == "WAITING_FOR_USR_STATS":
                await process_admin_usr_search(event, val_str, "stats")
                return
            elif action == "WAITING_FOR_USR_BAN":
                await process_admin_usr_search(event, val_str, "ban")
                return
            elif action == "WAITING_FOR_USR_UNBAN":
                await process_admin_usr_search(event, val_str, "unban")
                return
            elif action == "WAITING_FOR_USR_BAL":
                await process_admin_usr_search(event, val_str, "bal")
                return
                
            # 6.5 User Balance Editing (+/-)
            elif action.startswith("WAITING_FOR_EDITBAL_"):
                parts = action.split("_")
                edit_type = parts[3]
                target_uid = int(parts[4])
                
                try:
                    amount = float(val_str)
                    if amount <= 0:
                        raise ValueError("Amount must be positive")
                except ValueError:
                    await event.reply("❌ Invalid amount. Please send a valid positive number.")
                    return
                    
                target_user = database.get_user(target_uid)
                if not target_user:
                    await event.reply("❌ Target user not found in database.")
                    return
                    
                current_bal = target_user.get("wallet_balance", 0.0)
                if edit_type == "ADDBAL":
                    new_bal = current_bal + amount
                    success_msg = f"✅ **Successfully added ₹{amount:.2f} to user's wallet!**\nNew Balance: **₹{new_bal:.2f}**"
                else:
                    new_bal = max(0.0, current_bal - amount)
                    success_msg = f"✅ **Successfully subtracted ₹{amount:.2f} from user's wallet!**\nNew Balance: **₹{new_bal:.2f}**"
                    
                target_user["wallet_balance"] = new_bal
                database.save_user(target_user)
                
                await event.reply(success_msg)
                
                # Re-render balance management screen
                class MockEvent:
                    def __init__(self):
                        self.sender_id = user_id
                        self.respond = event.reply
                        self.edit = event.reply
                    async def answer(self, *args, **kwargs):
                        pass
                await process_admin_usr_search(MockEvent(), str(target_uid), "bal")
                return
                    
            # 7. Add Administrator
            elif action == "WAITING_FOR_ADD_ADMIN":
                new_admin = int(val_str)
                admins_list = global_settings.setdefault("admins", [])
                if new_admin not in admins_list:
                    admins_list.append(new_admin)
                success = True
                
            # 8. Remove Administrator
            elif action == "WAITING_FOR_REM_ADMIN":
                rem_admin = int(val_str)
                if rem_admin in config.ORIGINAL_ADMIN_IDS:
                    await event.reply("❌ Original administrators cannot be removed.")
                else:
                    admins_list = global_settings.setdefault("admins", [])
                    if rem_admin in admins_list:
                        admins_list.remove(rem_admin)
                    success = True
                    
        except Exception as e:
            logger.error(f"Failed to update admin settings: {e}")
            await event.reply(utils.get_text("admin_invalid", lang))
            
        if success:
            database.save_global_settings(global_settings)
            await event.reply(utils.get_text("admin_updated", lang))
            if action in ("WAITING_FOR_BRAND_NAME_TXT", "WAITING_FOR_BRAND_BIO_TXT"):
                await show_branding_settings(event, user_id)
                return
            
        # Return to admin panel
        await show_admin_panel(event, user_id)

    @client.on(events.CallbackQuery(pattern="^admin_manage_users$"))
    async def admin_manage_users_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        text = (
            "👥 **User Management Panel**\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Manage user accounts, view detailed stats, and apply bans/unbans."
        )
        buttons = [
            [
                utils.styled_button("📊 View User Stats", "admin_usr_stats_start", style="primary"),
                utils.styled_button("👛 Check User Balance", "admin_usr_bal_start", style="success")
            ],
            [
                utils.styled_button("🚫 Ban User", "admin_usr_ban_start", style="danger"),
                utils.styled_button("🟢 Unban User", "admin_usr_unban_start", style="success")
            ],
            [
                utils.styled_button("🎮 Control Userbots", "admin_usr_ctrl_start", style="success")
            ],
            [utils.styled_button("🔙 Back to Admin Panel", "menu_admin", style="primary")]
        ]
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_(stats|ban|unban|bal)_start$"))
    async def admin_usr_action_start(event):
        action = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        _admin_action_states[user_id] = f"WAITING_FOR_USR_{action.upper()}"
        
        prompts = {
            "stats": "🔍 Send the User ID or Username of the user to view stats:",
            "ban": "🚫 Send the User ID or Username of the user to BAN:",
            "unban": "🟢 Send the User ID or Username of the user to UNBAN:",
            "bal": "👛 Send the User ID or Username of the user to view and edit balance:",
            "ctrl": "🎮 Send the User ID or Username of the user to open their UserBot Dashboard:"
        }
        
        buttons = [[utils.styled_button("🔙 Cancel", "admin_manage_users", style="danger")]]
        try:
            await event.edit(prompts[action], buttons=buttons)
        except Exception:
            await event.respond(prompts[action], buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_tglban_(\d+)$"))
    async def admin_tglban_callback(event):
        target_uid = int(event.pattern_match.group(1))
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        target_user = database.get_user(target_uid)
        if target_user:
            target_user["is_banned"] = not target_user.get("is_banned", False)
            database.save_user(target_user)
            status_str = "banned 🔴" if target_user["is_banned"] else "unbanned 🟢"
            await event.answer(f"User {target_uid} has been {status_str}.", alert=True)
            # Re-render stats for this user
            await process_admin_usr_search(event, str(target_uid), "stats")
        else:
            await event.answer("❌ User not found.", alert=True)

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_opendashtrg_(\d+)$"))
    async def admin_usr_opendashtrg_callback(event):
        target_uid = int(event.pattern_match.group(1))
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        
        from handlers.my_bots import show_all_slots_dashboard, set_admin_impersonation
        set_admin_impersonation(user_id, target_uid)
        await show_all_slots_dashboard(event, target_uid, flash_message=f"👑 **Admin Access**: Controlling UserBots for User `{target_uid}`")

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_(addbal|subbal)_(\d+)$"))
    async def admin_usr_editbal_callback(event):
        action = event.pattern_match.group(1)
        target_uid = int(event.pattern_match.group(2))
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        target_user = database.get_user(target_uid)
        if not target_user:
            await event.answer("❌ User not found.", alert=True)
            return
            
        _admin_action_states[user_id] = f"WAITING_FOR_EDITBAL_{action.upper()}_{target_uid}"
        
        prompt_text = (
            f"👛 **{'Add' if action == 'addbal' else 'Subtract'} Balance**\n"
            f"User ID: `{target_uid}`\n"
            f"Current Balance: **₹{target_user.get('wallet_balance', 0.0):.2f}**\n\n"
            f"Send the amount in ₹ to {'add' if action == 'addbal' else 'subtract'}:"
        )
        
        buttons = [[utils.styled_button("🔙 Cancel", f"admin_usr_stats_back_{target_uid}", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_stats_back_(\d+)$"))
    async def admin_usr_stats_back_callback(event):
        target_uid = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        await process_admin_usr_search(event, str(target_uid), "bal")

    @client.on(events.CallbackQuery(pattern="^admin_branding_settings$"))
    async def admin_branding_settings_callback(event):
        await show_branding_settings(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_tgl_brand_(name|bio)_opt$"))
    async def admin_toggle_branding_opt_callback(event):
        element = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        global_settings = database.get_global_settings()
        key = f"branding_{element}_enabled"
        global_settings[key] = not global_settings.get(key, True)
        database.save_global_settings(global_settings)
        
        await show_branding_settings(event, user_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_set_brand_(name|bio)_txt$"))
    async def admin_set_branding_text_callback(event):
        element = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        _admin_action_states[user_id] = f"WAITING_FOR_BRAND_{element.upper()}_TXT"
        
        prompt_text = (
            f"✏️ **Set {element.capitalize()} Branding Suffix**\n\n"
            f"Send the suffix text to be appended to all userbots' {element}s (or send `none` to disable suffix):\n\n"
            f"Example: ` via @BotUsername`"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_branding_settings", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)


async def show_branding_settings(event, user_id: int):
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    if not check_admin(user_id):
        await event.respond(utils.get_text("error_not_admin", lang))
        return
        
    global_settings = database.get_global_settings()
    brand_name_val = "✅ ON" if global_settings.get("branding_name_enabled", True) else "❌ OFF"
    brand_bio_val = "✅ ON" if global_settings.get("branding_bio_enabled", True) else "❌ OFF"
    name_text = global_settings.get("branding_name_text") or "Not Set (Fallback:  via @BotUsername)"
    bio_text = global_settings.get("branding_bio_text") or "Not Set (Fallback:  via @BotUsername)"
    
    text = (
        "🎨 **Branding Configurations**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📛 **Name Branding**: {brand_name_val}\n"
        f"Suffix: `{name_text}`\n\n"
        f"📝 **Bio Branding**: {brand_bio_val}\n"
        f"Suffix: `{bio_text}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Configure global branding text appended to userbots."
    )
    
    buttons = [
        [
            utils.styled_button("📛 Toggle Name Branding", "admin_tgl_brand_name_opt", style="primary"),
            utils.styled_button("📝 Toggle Bio Branding", "admin_tgl_brand_bio_opt", style="primary")
        ],
        [
            utils.styled_button("✏️ Set Name Suffix", "admin_set_brand_name_txt", style="primary"),
            utils.styled_button("✏️ Set Bio Suffix", "admin_set_brand_bio_txt", style="primary")
        ],
        [
            utils.styled_button("🔙 Back to Admin Panel", "menu_admin", style="primary")
        ]
    ]
    
    try:
        if hasattr(event, "edit"):
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


async def process_admin_usr_search(event, search_query: str, action: str):
    user_id = event.sender_id
    search_query = search_query.strip()
    
    # 1. Resolve target user
    target_user = None
    if search_query.isdigit():
        target_user = database.get_user(int(search_query))
    else:
        target_user = database.get_user_by_username(search_query)
        
    if not target_user:
        buttons = [[utils.styled_button("🔙 Back", "admin_manage_users", style="primary")]]
        await event.reply("❌ **User not found.** Please verify the User ID or Username.", buttons=buttons)
        return
        
    target_id = target_user["user_id"]
    username = target_user.get("username") or "None"
    first_name = target_user.get("first_name") or ""
    last_name = target_user.get("last_name") or ""
    
    # Execute Action
    if action == "stats":
        # Get active userbots count
        sessions = database.get_sessions(target_id)
        active_userbots = sum(1 for s in sessions if s.get("status") == "running")
        
        # Count referred users
        referred_count = database.count_referred_users(target_id)
        
        is_banned = target_user.get("is_banned", False)
        
        stats_text = (
            f"👤 **User Statistics Report**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 User ID: `{target_id}`\n"
            f"🔗 Username: @{username}\n"
            f"🏷️ Name: **{first_name} {last_name}**\n"
            f"🚪 TOS Accepted: **{'Yes' if target_user.get('tos_accepted') else 'No'}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Slots Limit: **{target_user.get('allowed_slots', 1)}**\n"
            f"👛 Wallet Balance: **₹{target_user.get('wallet_balance', 0.0):.2f}**\n"
            f"👥 Total Referred: **{referred_count}**\n"
            f"🧑‍🤝‍🧑 Referred By: `{target_user.get('referred_by') or 'Direct'}`\n"
            f"🚫 Banned: **{'Yes 🔴' if is_banned else 'No 🟢'}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Total Accounts: **{len(sessions)}**\n"
            f"🟢 Active Userbots: **{active_userbots}**"
        )
        
        buttons = [
            [utils.styled_button("🚫 Ban User" if not is_banned else "🟢 Unban User", f"admin_tglban_{target_id}", style="danger" if not is_banned else "success")],
            [utils.styled_button("🎮 Control Userbots", f"admin_usr_opendashtrg_{target_id}", style="success")],
            [utils.styled_button("🔙 Back to User Management", "admin_manage_users", style="primary")]
        ]
        await event.reply(stats_text, buttons=buttons)
        
    elif action == "ctrl":
        from handlers.my_bots import show_all_slots_dashboard, set_admin_impersonation
        set_admin_impersonation(user_id, target_id)
        await show_all_slots_dashboard(event, target_id, flash_message=f"👑 **Admin Access**: Controlling UserBots for User `{target_id}`")
        
    elif action == "ban":
        if target_id in config.ORIGINAL_ADMIN_IDS:
            await event.reply("❌ Administrators cannot be banned.")
            return
        target_user["is_banned"] = True
        database.save_user(target_user)
        await event.reply(f"✅ **User Banned successfully!**\nUser ID: `{target_id}`\nUsername: @{username}")
        # Re-render menu
        class MockEvent:
            def __init__(self):
                self.sender_id = user_id
                self.respond = event.respond
                self.edit = event.respond
            async def answer(self, *args, **kwargs):
                pass
        await admin_manage_users_callback(MockEvent())
        
    elif action == "unban":
        target_user["is_banned"] = False
        database.save_user(target_user)
        await event.reply(f"✅ **User Unbanned successfully!**\nUser ID: `{target_id}`\nUsername: @{username}")
        # Re-render menu
        class MockEvent:
            def __init__(self):
                self.sender_id = user_id
                self.respond = event.respond
                self.edit = event.respond
            async def answer(self, *args, **kwargs):
                pass
        await admin_manage_users_callback(MockEvent())

    elif action == "bal":
        wallet_bal = target_user.get("wallet_balance", 0.0)
        bal_text = (
            f"👛 **User Balance Management**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 User ID: `{target_id}`\n"
            f"🔗 Username: @{username}\n"
            f"🏷️ Name: **{first_name} {last_name}**\n"
            f"👛 Current Balance: **₹{wallet_bal:.2f}**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Choose an option below to add or subtract balance:"
        )
        buttons = [
            [
                utils.styled_button("➕ Add Balance", f"admin_usr_addbal_{target_id}", style="success"),
                utils.styled_button("➖ Subtract Balance", f"admin_usr_subbal_{target_id}", style="danger")
            ],
            [utils.styled_button("🔙 Back to User Management", "admin_manage_users", style="primary")]
        ]
        await event.reply(bal_text, buttons=buttons)
