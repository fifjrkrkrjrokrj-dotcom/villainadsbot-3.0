import os
import asyncio
import logging
import time
import urllib.request
import urllib.error
import json
import random
import sys
from typing import Optional, Set

# Auto-detect and append NodeJS to PATH on Windows if missing
if sys.platform == "win32":
    paths_to_add = [
        r"C:\Program Files\nodejs",
        r"C:\Program Files (x86)\nodejs",
        os.path.expandvars(r"%APPDATA%\npm")
    ]
    current_path = os.environ.get("PATH", "")
    for p in paths_to_add:
        if os.path.exists(p) and p not in current_path:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

from telethon import TelegramClient, events, functions, types
from telethon.errors import (
    PeerFloodError,
    FloodWaitError,
    UserBannedInChannelError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    SlowModeWaitError,
    ChannelInvalidError,
    ChatIdInvalidError,
    UserNotParticipantError,
    UserDeactivatedError,
    AuthKeyUnregisteredError
)
try:
    from telethon.errors import SessionRevokedError
except ImportError:
    SessionRevokedError = None
try:
    from telethon.errors import SessionExpiredError
except ImportError:
    SessionExpiredError = None
import config
import database
import utils

logger = logging.getLogger(__name__)

# --- Pytgcalls Telethon Update AttributeError Monkey-Patch ---
try:
    from pytgcalls.mtproto.telethon_client import TelethonClient
    from telethon.events import Raw
    
    original_init = TelethonClient.__init__
    
    def patched_init(self, cache_duration, client):
        original_add = client.add_event_handler
        
        def patched_add(callback, event=None):
            if isinstance(event, Raw) or (event and event.__class__.__name__ == 'Raw'):
                original_callback = callback
                async def wrapped_callback(evt):
                    try:
                        await original_callback(evt)
                    except (AttributeError, ValueError) as err:
                        err_str = str(err)
                        if "UpdateGroupCall" in err_str or "chat_id" in err_str:
                            pass
                        else:
                            raise err
                    except Exception as e:
                        err_str = str(e)
                        if "UpdateGroupCall" in err_str or "chat_id" in err_str:
                            pass
                        else:
                            raise e
                callback = wrapped_callback
            return original_add(callback, event)
            
        client.add_event_handler = patched_add
        try:
            original_init(self, cache_duration, client)
        finally:
            client.add_event_handler = original_add
            
    TelethonClient.__init__ = patched_init
    logger.info("Successfully applied PyTgCalls TelethonClient monkey-patch.")
except Exception as patch_err:
    logger.error(f"Failed to apply PyTgCalls monkey-patch: {patch_err}")

import aiohttp
from pytgcalls import PyTgCalls
from pytgcalls.types import AudioPiped

API_URL = "https://api.shrutibots.site"
API_KEY = "ShrutiBotsGMiLr8wF1tPbxVV6fRgH"
DOWNLOAD_DIR = "downloads"

async def download_media(query: str, download_type: str = "audio") -> tuple:
    """
    Downloads audio/video for voice call using YouTube API search.
    Returns (file_path: str, title: str, duration: int, thumbnail: str)
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    try:
        import yt_dlp
        
        def _search_sync():
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
                'skip_download': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_query = query if query.startswith("http") else f"ytsearch1:{query}"
                try:
                    res = ydl.extract_info(search_query, download=False)
                    if not res:
                        return None
                    if 'entries' in res:
                        entries = res['entries']
                        if not entries or not entries[0]:
                            return None
                        return entries[0]
                    return res
                except Exception as e:
                    logger.error(f"yt-dlp search extraction failed: {e}")
                    return None

        loop = asyncio.get_running_loop()
        entry = await loop.run_in_executor(None, _search_sync)
        if not entry:
            return None, None, None, None
            
        title = entry.get("title") or "YouTube Video"
        vidid = entry.get("id")
        if not vidid:
            return None, None, None, None
            
        youtube_url = f"https://www.youtube.com/watch?v={vidid}"
        duration_sec = int(entry.get("duration") or 0)
        thumb = entry.get("thumbnail") or f"https://img.youtube.com/vi/{vidid}/0.jpg"

        # Check if file already exists in downloads (with any extension)
        existing_file = None
        if os.path.exists(DOWNLOAD_DIR):
            for fname in os.listdir(DOWNLOAD_DIR):
                if fname.startswith(vidid) and os.path.getsize(os.path.join(DOWNLOAD_DIR, fname)) > 0:
                    existing_file = os.path.join(DOWNLOAD_DIR, fname)
                    break
                
        if existing_file:
            logger.info(f"Using cached file: {existing_file}")
            return existing_file, title, duration_sec, thumb

        # Define file path for remote API download (if used)
        ext = "mp3" if download_type == "audio" else "mp4"
        file_path = os.path.join(DOWNLOAD_DIR, f"{vidid}.{ext}")

        use_local = False
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{API_URL}/download",
                    params={
                        "url": youtube_url,
                        "type": download_type,
                        "api_key": API_KEY
                    },
                    timeout=aiohttp.ClientTimeout(total=300)
                ) as resp:
                    if resp.status == 200:
                        with open(file_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(1024 * 128):
                                f.write(chunk)
                        logger.info(f"Downloaded {download_type} via remote API for: {title}")
                        return file_path, title, duration_sec, thumb
                    else:
                        logger.warning(f"Remote download API failed with status {resp.status}, falling back to local yt-dlp.")
                        use_local = True
        except Exception as api_err:
            logger.warning(f"Remote API download exception: {api_err}, falling back to local yt-dlp.")
            use_local = True
                
        if use_local:
            import yt_dlp
            logger.info(f"Downloading {download_type} locally using yt-dlp for: {title}...")
            
            def _dl_sync():
                try:
                    if download_type == "audio":
                        ydl_opts = {
                            'format': 'bestaudio/best',
                            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{vidid}.%(ext)s"),
                            'postprocessors': [{
                                'key': 'FFmpegExtractAudio',
                                'preferredcodec': 'mp3',
                                'preferredquality': '192',
                            }],
                            'quiet': True,
                            'no_warnings': True,
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([youtube_url])
                    else:
                        ydl_opts = {
                            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{vidid}.%(ext)s"),
                            'quiet': True,
                            'no_warnings': True,
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([youtube_url])
                            
                    # Scan directory to find the downloaded file name
                    for fname in os.listdir(DOWNLOAD_DIR):
                        if fname.startswith(vidid):
                            full_p = os.path.join(DOWNLOAD_DIR, fname)
                            if os.path.getsize(full_p) > 0:
                                return full_p
                    return None
                except Exception as dl_ex:
                    logger.error(f"yt-dlp sync download exception: {dl_ex}")
                    return None
                    
            loop = asyncio.get_running_loop()
            downloaded_file = await loop.run_in_executor(None, _dl_sync)
            if downloaded_file and os.path.exists(downloaded_file):
                logger.info(f"Successfully downloaded {download_type} locally: {downloaded_file}")
                return downloaded_file, title, duration_sec, thumb
            else:
                logger.error(f"Local yt-dlp download failed.")
                return None, None, None, None
    except Exception as e:
        logger.error(f"Download media error: {e}")
        return None, None, None, None

async def call_gpt_api(api_key: str, user_message: str) -> str:
    """
    Calls OpenAI GPT-3.5 API using standard urllib to prevent external library issues.
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a helpful automated assistant."},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 150
    }
    
    def _send():
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode("utf-8"), 
            headers=headers, 
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"].strip()
        except Exception as err:
            logger.error(f"GPT API request error: {err}")
            return "⚠️ GPT Assistant temporarily unavailable."

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send)

async def join_vc(client: TelegramClient, peer_id: int) -> bool:
    """
    Attempts to join the active voice call of a group/channel using JoinGroupCallRequest.
    """
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.functions.phone import JoinGroupCallRequest
        from telethon.tl.types import InputGroupCall, DataJSON, Channel, GroupCallDiscarded
        
        entity = await client.get_entity(peer_id)
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            await client(JoinGroupCallRequest(
                call=InputGroupCall(
                    id=group_call.id,
                    access_hash=group_call.access_hash
                ),
                join_as=await client.get_input_entity('me'),
                params=DataJSON(data='{}'),
                muted=True
            ))
            logger.info(f"Successfully joined VC for peer {peer_id}")
            return True
        else:
            logger.debug(f"No active VC found for peer {peer_id}")
    except Exception as e:
        logger.warning(f"Could not join VC for peer {peer_id}: {e}")
    return False


async def get_peer_from_link(client: TelegramClient, link: str):
    """
    Resolves a group/channel link or username to a chat entity,
    joining the channel/group if the userbot is not already a member.
    """
    link = link.strip()
    if not link:
        return None
        
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.types import ChatInviteAlready, ChatInvite
    
    # Check if link is a numeric ID
    try:
        val = link
        chat_id = None
        if val.startswith("-100") and val[4:].isdigit():
            chat_id = int(val)
        elif val.startswith("-") and val[1:].isdigit():
            chat_id = int(val)
        elif val.isdigit():
            chat_id = int(val)
            
        if chat_id is not None:
            try:
                return await client.get_entity(chat_id)
            except Exception:
                # Fallback: Iterate dialogs to find the correct entity
                async for dialog in client.iter_dialogs():
                    if dialog.id == chat_id:
                        return dialog.entity
    except Exception:
        pass
        
        
    # Check if it is a private invite link
    if "t.me/+" in link or "t.me/joinchat/" in link:
        # Extract join hash
        if "t.me/+" in link:
            hash_val = link.split('+')[-1].split('/')[0].strip()
        else:
            hash_val = link.split('joinchat/')[-1].split('/')[0].strip()
            
        try:
            invite = await client(CheckChatInviteRequest(hash_val))
            if isinstance(invite, ChatInviteAlready):
                return invite.chat
            else:
                # Need to join
                updates = await client(ImportChatInviteRequest(hash_val))
                if updates and hasattr(updates, 'chats') and updates.chats:
                    return updates.chats[0]
        except Exception as e:
            logger.warning(f"Error checking/joining invite link {link}: {e}")
            # Try to get entity directly as a fallback
            try:
                return await client.get_entity(link)
            except Exception:
                pass
    else:
        # It's a username or public link
        username = link.split('/')[-1].strip()
        if username.startswith("@"):
            username = username[1:]
            
        try:
            # Join channel first
            await client(JoinChannelRequest(username))
        except Exception as e:
            logger.warning(f"Error joining public channel {username}: {e}")
            
        try:
            return await client.get_entity(username)
        except Exception as e:
            logger.warning(f"Error getting entity for {username}: {e}")
            
    return None

async def join_vc_by_link(client: TelegramClient, link: str) -> tuple:
    """
    Joins a channel/group from a link and joins its active voice chat.
    Returns (success: bool, message: str)
    """
    try:
        entity = await get_peer_from_link(client, link)
        if not entity:
            return False, "Could not resolve or join the group/channel."
            
        # Try to join VC
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.functions.phone import JoinGroupCallRequest
        from telethon.tl.types import InputGroupCall, DataJSON, Channel, GroupCallDiscarded
        
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            import random
            max_retries = 5
            last_err = None
            for attempt in range(max_retries):
                random_ssrc = random.randint(100000, 999999999)
                try:
                    params_json = f'{{"transport":{{"webrtc":true}},"muted":true,"video_stopped":true,"ssrc":{random_ssrc}}}'
                    await client(JoinGroupCallRequest(
                        call=InputGroupCall(
                            id=group_call.id,
                            access_hash=group_call.access_hash
                        ),
                        join_as=await client.get_input_entity('me'),
                        params=DataJSON(data=params_json),
                        muted=True
                    ))
                    logger.info(f"Successfully joined VC for peer {entity.id} with SSRC {random_ssrc}")
                    chat_title = getattr(entity, 'title', 'Group')
                    return True, f"Successfully joined VC of {chat_title}!", {"group_call": group_call, "ssrc": random_ssrc, "chat_id": entity.id}
                except Exception as join_err:
                    err_str = str(join_err).lower()
                    last_err = join_err
                    if "ssrc" in err_str or "duplicate" in err_str:
                        logger.warning(f"SSRC collision on attempt {attempt + 1}, retrying with new SSRC... Error: {join_err}")
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        break
            raise last_err if last_err else Exception("Failed to join call")
        else:
            chat_title = getattr(entity, 'title', 'Group')
            return False, f"No active voice chat (VC) found in {chat_title}.", None
    except Exception as e:
        logger.warning(f"Error joining VC by link {link}: {e}")
        return False, f"Error: {e}", None


async def generate_silence() -> str:
    """
    Generates a 10-second silence.mp3 file in the downloads folder using FFmpeg.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, "silence.mp3")
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "10", file_path, "-y",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        logger.info(f"Generated silence.mp3 successfully at {file_path}")
    except Exception as e:
        logger.error(f"Failed to generate silence.mp3: {e}")
    return file_path


async def get_group_call_info(client: TelegramClient, link_or_id: str):
    """
    Resolves link/ID/username to the Telegram entity and its active call details.
    """
    entity = await get_peer_from_link(client, link_or_id)
    if not entity:
        return None, None
        
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.messages import GetFullChatRequest
    from telethon.tl.types import Channel, GroupCallDiscarded
    
    try:
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            return entity, group_call
    except Exception as e:
        logger.error(f"Error getting group call info for {link_or_id}: {e}")
    return entity, None



async def join_channel_single(client: TelegramClient, ch: str) -> bool:
    """
    Attempts to join a single channel or group by invite link or username.
    Returns True if successfully joined or already in it, False otherwise.
    """
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest
    import re
    
    ch = ch.strip()
    if not ch:
        return False
    try:
        # Match joinchat or + hash links for t.me, telegram.me, telegram.dog, telegram.org
        join_hash_match = re.search(r'(?:t\.me|telegram\.(?:me|dog|org))/(?:joinchat/|\+)([^?/\s]+)', ch, re.IGNORECASE)
        
        if join_hash_match:
            hash_val = join_hash_match.group(1)
            await client(ImportChatInviteRequest(hash_val))
        else:
            # Extract username from public invite link if present
            username_match = re.search(r'(?:t\.me|telegram\.(?:me|dog|org))/([^?/\s]+)', ch, re.IGNORECASE)
            if username_match:
                username = username_match.group(1)
            else:
                username = ch.replace('@', '').strip()
                
            await client(JoinChannelRequest(username))
            
        logger.info(f"Successfully joined channel/group: {ch}")
        return True
    except Exception as e:
        err_msg = str(e).lower()
        if "already" in err_msg or "already participant" in err_msg or "user_already_participant" in err_msg:
            logger.debug(f"Already participant of: {ch}")
            return True
        logger.warning(f"Failed to join channel {ch}: {e}")
        return False


async def leave_chat_single(client: TelegramClient, ch: str) -> bool:
    """
    Attempts to leave a channel or group by link, username, or ID.
    """
    from telethon.tl.functions.channels import LeaveChannelRequest
    from telethon.tl.functions.messages import DeleteChatUserRequest
    ch = ch.strip()
    if not ch:
        return False
    try:
        entity = await get_peer_from_link(client, ch)
        if not entity:
            return False
            
        from telethon.tl.types import Channel
        if isinstance(entity, Channel):
            await client(LeaveChannelRequest(entity))
        else:
            await client(DeleteChatUserRequest(
                chat_id=entity.id,
                user_id=await client.get_input_entity('me')
            ))
        logger.info(f"Successfully left channel/group: {ch}")
        return True
    except Exception as e:
        logger.warning(f"Failed to leave channel/group {ch}: {e}")
        return False


async def force_join_channels(client: TelegramClient, channels: list, session_id: str = None):
    """
    Forcibly joins the userbot to a list of channels or invite links.
    Uses asyncio.gather with a semaphore for fast parallel joining.
    """
    # Load already joined list if session_id is provided
    sess_data = None
    already_joined = []
    if session_id:
        sess_data = database.get_session(session_id)
        if sess_data:
            already_joined = sess_data.get("joined_channels", [])

    # Filter out channels that are already joined or empty
    pending = []
    for ch in channels:
        ch_clean = ch.strip()
        if not ch_clean:
            continue
        if ch_clean in already_joined:
            logger.info(f"Skipping auto-join for already joined channel: {ch_clean}")
            continue
        pending.append(ch_clean)

    if not pending:
        return

    # Join pending channels sequentially with safe delays between joins (bypasses anti-spam)
    joined_now = []
    for ch_clean in pending:
        success = await join_channel_single(client, ch_clean)
        if success:
            joined_now.append(ch_clean)
        # Sleep 3-5 seconds between channel joins to prevent anti-spam triggers
        await asyncio.sleep(random.uniform(3.0, 5.0))

    # Save newly joined channels to MongoDB
    if sess_data and joined_now:
        sess_data = database.get_session(session_id)
        if sess_data:
            current_joined = sess_data.setdefault("joined_channels", [])
            for c in joined_now:
                if c not in current_joined:
                    current_joined.append(c)
            database.save_session(sess_data)

async def apply_branding(client: TelegramClient, branding_username: str, session_data: dict):
    """
    Appends the branding bot username suffix to the userbot profile's name and bio based on global settings.
    Stores original details in session data for restoration.
    """
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.tl.functions.account import UpdateProfileRequest
    
    try:
        global_settings = database.get_global_settings()
        brand_name_enabled = global_settings.get("branding_name_enabled", True)
        brand_bio_enabled = global_settings.get("branding_bio_enabled", True)
        
        brand_name_text = global_settings.get("branding_name_text")
        brand_bio_text = global_settings.get("branding_bio_text")
        
        full_user = await client(GetFullUserRequest('me'))
        user_me = full_user.users[0]
        full_profile = full_user.full_user
        
        orig_first_name = user_me.first_name or ""
        orig_bio = full_profile.about or ""
        
        if not session_data.get("original_name"):
            session_data["original_name"] = orig_first_name
        if not session_data.get("original_bio"):
            session_data["original_bio"] = orig_bio
            
        name_suffix = brand_name_text if brand_name_text else (f" via @{branding_username}" if branding_username else "")
        bio_suffix = brand_bio_text if brand_bio_text else (f" via @{branding_username}" if branding_username else "")
        
        new_first_name = orig_first_name
        if brand_name_enabled and name_suffix:
            if name_suffix not in orig_first_name:
                new_first_name = (orig_first_name + name_suffix)[:64]
                
        new_bio = orig_bio
        if brand_bio_enabled and bio_suffix:
            if bio_suffix not in orig_bio:
                new_bio = (orig_bio + bio_suffix)[:70]
                
        await client(UpdateProfileRequest(
            first_name=new_first_name,
            about=new_bio
        ))
        
        database.save_session(session_data)
        logger.info(f"Branding applied successfully for userbot: {user_me.id} (Name: {brand_name_enabled}, Bio: {brand_bio_enabled})")
    except Exception as e:
        logger.error(f"Failed to apply branding: {e}")

async def restore_branding(client: TelegramClient, session_data: dict):
    """
    Restores the userbot profile's original name and bio.
    """
    from telethon.tl.functions.account import UpdateProfileRequest
    try:
        orig_name = session_data.get("original_name", "")
        orig_bio = session_data.get("original_bio", "")
        if orig_name or orig_bio:
            await client(UpdateProfileRequest(
                first_name=orig_name if orig_name else "User",
                about=orig_bio
            ))
            logger.info("Branding restored successfully.")
    except Exception as e:
        logger.error(f"Failed to restore branding: {e}")


class UserBot:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.client: Optional[TelegramClient] = None
        self.me_id: Optional[int] = None
        self.is_running = False
        self.broadcast_task: Optional[asyncio.Task] = None
        self.joined_vcs: Set[int] = set()
        self.tag_cooldown = {}
        self.vc_keepalive_task: Optional[asyncio.Task] = None
        self.current_vc_chat_id: Optional[int] = None
        self.current_vc_link: Optional[str] = None
        self.pytgcalls_client: Optional[PyTgCalls] = None
        self.is_muted = True
        self.bg_tasks: Set[asyncio.Task] = set()
        
        # Caching attributes to resolve performance bottlenecks
        self.settings = {}
        self.groups_cache = None
        self.groups_cache_time = 0.0

    def reload_settings(self):
        """
        Reloads userbot settings from MongoDB into memory and restarts the broadcast task.
        """
        sess_data = database.get_session(self.session_id)
        if sess_data:
            self.settings = sess_data.get("settings", {})
            logger.info(f"Reloaded in-memory settings for userbot {self.session_id}")
            
            # Restart the broadcast task if the bot is currently running to apply changes immediately
            if self.is_running:
                if self.broadcast_task:
                    self.broadcast_task.cancel()
                self.broadcast_task = asyncio.create_task(self.broadcast_loop())
                logger.info(f"Restarted broadcast loop for userbot {self.session_id} to apply new settings/interval immediately.")

    async def _mark_unauthorized_and_cleanup(self):
        """
        Cleans up a deactivated or revoked session by disconnecting the client,
        marking the database status as 'unauthorized', and deleting/renaming the session file.
        """
        logger.warning(f"Cleaning up unauthorized/deactivated userbot session: {self.session_id}")
        self.is_running = False
        
        # 1. Disconnect client
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client during auth-cleanup for {self.session_id}: {e}")
        
        # 2. Update status in MongoDB database
        sess_data = database.get_session(self.session_id)
        if sess_data:
            sess_data["status"] = "unauthorized"
            sess_data["last_error"] = "❌ Account has been deactivated or session revoked from Telegram."
            # Remove session bytes to save database space since they are invalid now
            sess_data.pop("session_bytes", None)
            database.save_session(sess_data)
            
        # 3. Remove local session files to prevent lockups and auto-healing retries
        session_file = sess_data.get("session_file") if sess_data else None
        if not session_file:
            # Construct fallback path matching add_bot.py
            user_id = sess_data.get("user_id") if sess_data else "temp"
            user_dir = utils.ensure_user_dir(user_id)
            session_file = os.path.join(user_dir, f"{self.session_id}.session")
            
        if session_file:
            import glob
            for f in glob.glob(session_file + "*"):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                        logger.info(f"Deleted local session file during auth-cleanup: {f}")
                except Exception as e:
                    logger.warning(f"Could not delete session file {f} during auth-cleanup: {e}")
                    
        # 4. Remove from manager's running bots list locally to avoid circular import issues
        try:
            import userbot_manager
            userbot_manager._running_bots.pop(self.session_id, None)
            logger.info(f"Removed userbot {self.session_id} from running registry.")
        except Exception as manager_err:
            logger.warning(f"Could not remove bot {self.session_id} from manager registry: {manager_err}")

    async def join_voice_chat(self, link_or_id: str) -> tuple:
        """
        Attempts to join the active voice call of a group/channel using PyTgCalls.
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running."
            
        try:
            entity, group_call = await get_group_call_info(self.client, link_or_id)
            if not entity:
                return False, "Could not resolve the group link, username, or Chat ID."
                
            if not group_call:
                return False, f"No active voice chat (VC) found in {getattr(entity, 'title', 'Group')}. Please start the VC first."
                
            # If already in a voice chat, leave first to prevent conflicts
            if self.current_vc_chat_id:
                try:
                    await self.leave_voice_chat()
                    await asyncio.sleep(1.0)
                except Exception:
                    pass
                
            chat_id = entity.id
            
            # Join VC via Telethon protocol first (with muted=True) to establish connection and mute the mic!
            try:
                await join_vc(self.client, chat_id)
            except Exception as jvc_err:
                logger.warning(f"Protocol join_vc failed: {jvc_err}, continuing to PyTgCalls...")

            pytg = await self.get_pytgcalls()
            
            # Generate silence file to prevent auto-kick
            silence_file = await generate_silence()
            if not silence_file or not os.path.exists(silence_file):
                return False, "Failed to generate silence.mp3. Make sure FFmpeg is installed."
                
            logger.info(f"Joining VC of {chat_id} using PyTgCalls with silence.mp3...")
            
            await pytg.join_group_call(
                chat_id,
                AudioPiped(silence_file)
            )
            
            self.current_vc_chat_id = chat_id
            self.current_vc_link = link_or_id
            
            # Immediately mute the mic (mic off) on join!
            try:
                await pytg.mute_stream(chat_id)
                self.is_muted = True
                logger.info(f"Muted stream for userbot {self.session_id} in chat {chat_id}")
            except Exception as mute_err:
                logger.warning(f"Failed to auto-mute stream on join: {mute_err}")
                
            # Save VC status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["vc_chat_id"] = chat_id
                sess_data["vc_link"] = link_or_id
                database.save_session(sess_data)
            
            chat_title = getattr(entity, 'title', 'Group')
            return True, f"Successfully joined Voice Chat of {chat_title}!"
        except Exception as e:
            logger.exception("Error joining VC using PyTgCalls")
            return False, f"Failed to join VC: {e}"

    async def leave_voice_chat(self) -> tuple:
        """
        Leaves any active group call/VC the userbot is in.
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running."
            
        try:
            if self.pytgcalls_client:
                try:
                    if self.current_vc_chat_id:
                        await self.pytgcalls_client.leave_group_call(self.current_vc_chat_id)
                except Exception as e:
                    logger.warning(f"Error leaving via pytgcalls: {e}")
                try:
                    await self.pytgcalls_client.stop()
                except Exception:
                    pass
                self.pytgcalls_client = None
                
            self.current_vc_chat_id = None
            self.current_vc_link = None
            
            # Clear VC status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["vc_chat_id"] = None
                sess_data["vc_link"] = None
                sess_data["current_song"] = None
                database.save_session(sess_data)
                
            return True, "Successfully left voice chat."
        except Exception as e:
            logger.warning(f"Error leaving VC for userbot {self.session_id}: {e}")
            return False, f"Error leaving VC: {e}"

    async def get_pytgcalls(self) -> PyTgCalls:
        if not self.pytgcalls_client:
            self.pytgcalls_client = PyTgCalls(self.client)
            await self.pytgcalls_client.start()
        return self.pytgcalls_client

    async def mute_mic(self) -> tuple:
        if not self.is_running or not self.current_vc_chat_id:
            return False, "Bot is not in any VC."
        try:
            pytg = await self.get_pytgcalls()
            await pytg.mute_stream(self.current_vc_chat_id)
            self.is_muted = True
            return True, "Mic turned OFF (Muted)."
        except Exception as e:
            logger.error(f"Failed to mute: {e}")
            return False, f"Failed to mute: {e}"

    async def unmute_mic(self) -> tuple:
        if not self.is_running or not self.current_vc_chat_id:
            return False, "Bot is not in any VC."
        try:
            pytg = await self.get_pytgcalls()
            await pytg.unmute_stream(self.current_vc_chat_id)
            self.is_muted = False
            return True, "Mic turned ON (Unmuted)."
        except Exception as e:
            logger.error(f"Failed to unmute: {e}")
            return False, f"Failed to unmute: {e}"

    async def stop_song(self) -> tuple:
        if not self.is_running or not self.current_vc_chat_id:
            return False, "Not in a Voice Chat."
        try:
            silence_file = await generate_silence()
            pytg = await self.get_pytgcalls()
            await pytg.change_stream(
                self.current_vc_chat_id,
                AudioPiped(silence_file)
            )
            
            # Automatically mute the stream back when playback stops
            try:
                await pytg.mute_stream(self.current_vc_chat_id)
                self.is_muted = True
                logger.info(f"Automatically muted userbot {self.session_id} back to silence.")
            except Exception as mute_err:
                logger.warning(f"Could not auto-mute stream on stop: {mute_err}")
                
            # Clear in MongoDB and memory
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["current_song"] = None
                database.save_session(sess_data)
            return True, "Playback stopped."
        except Exception as e:
            logger.error(f"Failed to stop playback: {e}")
            return False, f"Failed to stop: {e}"

    async def play_song(self, query: str, play_type: str = "audio", local_file: str = None, title: str = None, duration: int = 30) -> tuple:
        """
        Plays a song (audio or video) in the current active Voice Chat of this userbot.
        Returns (success: bool, message: str, song_info: dict)
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running.", None
            
        if not getattr(self, "current_vc_chat_id", None):
            return False, "Userbot is not currently in any Voice Chat (VC). Please make it join a VC first.", None
            
        file_path = None
        thumb = None
        
        if local_file:
            file_path = local_file
            title = title or "Uploaded Audio"
            duration = duration or 30
        else:
            # Download the media
            logger.info(f"Userbot {self.session_id} downloading {play_type} query: {query}")
            try:
                file_path, title, duration, thumb = await download_media(query, download_type=play_type)
            except Exception as dl_err:
                return False, f"YouTube download failed: {dl_err}", None
                
            if not file_path:
                return False, "Failed to download or parse media from YouTube. The link might be broken or region-restricted.", None
            
        try:
            pytg = await self.get_pytgcalls()
            
            if play_type == "video":
                from pytgcalls.types import AudioVideoPiped
                stream_obj = AudioVideoPiped(file_path)
                logger.info(f"Streaming video (AudioVideoPiped): {file_path}")
            else:
                stream_obj = AudioPiped(file_path)
                
            try:
                # We are already in the call (with silence), so we change the stream!
                await pytg.change_stream(
                    self.current_vc_chat_id,
                    stream_obj
                )
            except Exception as change_err:
                logger.warning(f"change_stream failed, trying join_group_call: {change_err}")
                try:
                    await pytg.join_group_call(
                        self.current_vc_chat_id,
                        stream_obj
                    )
                except Exception as join_err:
                    logger.error(f"PyTgCalls play failed: {join_err}")
                    return False, f"Could not stream media: {join_err}", None
                    
            song_info = {
                "title": title,
                "duration": duration,
                "thumb": thumb,
                "file_path": file_path
            }
            
            # Automatically unmute the stream for music playback
            try:
                await pytg.unmute_stream(self.current_vc_chat_id)
                self.is_muted = False
                logger.info(f"Automatically unmuted userbot {self.session_id} for song playing.")
            except Exception as unmute_err:
                logger.warning(f"Could not auto-unmute stream on play: {unmute_err}")
            
            # Save playing status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["current_song"] = {
                    "title": title,
                    "duration": duration,
                    "play_type": play_type,
                    "query": query or title
                }
                database.save_session(sess_data)
                
            return True, f"Now playing {title}", song_info
        except Exception as e:
            logger.error(f"Error playing media: {e}")
            return False, f"Error playing media: {e}", None

    async def get_groups(self, force_refresh: bool = False) -> list:
        """
        Returns group dialogs, utilizing a 1-hour cache to avoid heavy Telegram API calls.
        """
        current_time = time.time()
        # Cache dialogs for 1 hour (3600 seconds) unless force-refreshed
        if force_refresh or not self.groups_cache or (current_time - self.groups_cache_time > 3600):
            try:
                logger.info(f"Fetching dialogs for userbot {self.session_id} to refresh groups cache...")
                dialogs = await self.client.get_dialogs(limit=None)
                self.groups_cache = [d for d in dialogs if d.is_group]
                self.groups_cache_time = current_time
                
                # Update DB stats concurrently
                sess_data = database.get_session(self.session_id)
                if sess_data:
                    sess_data["stats"]["group_count"] = len(self.groups_cache)
                    sess_data["stats"]["user_count"] = sum(1 for d in dialogs if d.is_user)
                    database.save_session(sess_data)
            except Exception as e:
                logger.error(f"Error fetching dialogs for userbot {self.session_id}: {e}")
                if not self.groups_cache:
                    self.groups_cache = []
        return self.groups_cache

    async def start(self) -> bool:
        if self.is_running:
            return True
            
        sess_data = database.get_session(self.session_id, include_bytes=True)
        if not sess_data:
            logger.error(f"Session data not found in DB for {self.session_id}")
            return False
            
        # Initialize in-memory settings
        self.settings = sess_data.get("settings", {})
        session_file = sess_data["session_file"]
        
        # Restore session file from MongoDB if it was saved
        session_bytes = sess_data.get("session_bytes")
        if session_bytes:
            try:
                os.makedirs(os.path.dirname(session_file), exist_ok=True)
                with open(session_file, "wb") as f:
                    f.write(session_bytes)
                logger.info(f"Restored session file from MongoDB to {session_file}")
            except Exception as e:
                logger.error(f"Failed to restore session file from MongoDB: {e}")

        if not os.path.exists(session_file):
            logger.error(f"Session file not found: {session_file}")
            # Mark status stopped/unauthorized in database so it doesn't get retried
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["status"] = "stopped"
                database.save_session(sess_data)
            return False
            
        # Spoof a deterministic device profile to avoid detection
        device_prof = utils.get_device_profile(self.session_id)
        self.client = TelegramClient(
            session_file, 
            config.API_ID, 
            config.API_HASH,
            device_model=device_prof["device_model"],
            system_version=device_prof["system_version"],
            app_version=device_prof["app_version"]
        )
        
        # Optimize Telethon SQLite session speed and prevent database locks
        try:
            conn = self.client.session._conn
            if conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                logger.info(f"Enabled WAL mode for userbot SQLite session: {session_file}")
        except Exception as sqlite_opt_err:
            logger.warning(f"Could not optimize SQLite session parameters: {sqlite_opt_err}")
        
        # NEW: Check if the sqlite file is locked by the OS.
        # This prevents the main bot event loop from hanging indefinitely during connect()
        import sqlite3
        if os.path.exists(session_file):
            try:
                # Use timeout=0.1 so it fails fast without blocking the event loop
                test_conn = sqlite3.connect(session_file, timeout=0.1)
                test_conn.execute("BEGIN IMMEDIATE")
                test_conn.commit()
                test_conn.close()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower() or "busy" in str(e).lower():
                    logger.error(f"Cannot start userbot {self.session_id} because session file is locked by the OS.")
                    # Return False so the manager doesn't crash the bot
                    return False
                test_conn.close()

        try:
            # Wrap connect() in wait_for to prevent infinite hang if Telethon blocks
            await asyncio.wait_for(self.client.connect(), timeout=10.0)
            
            # Wrap is_user_authorized() as well
            is_authorized = await asyncio.wait_for(self.client.is_user_authorized(), timeout=10.0)
            if not is_authorized:
                logger.warning(f"Userbot {self.session_id} is unauthorized. Cleaning up.")
                await self._mark_unauthorized_and_cleanup()
                return False
                
            self.is_running = True
            
            # Apply configurations
            global_settings = database.get_global_settings()
            
            # Auto-join support links and custom auto-join links
            support_links = []
            support_channel = global_settings.get("support_channel") or "https://t.me/TheVillainActive"
            support_group = global_settings.get("support_group") or "https://t.me/+WzyoJkg4bzhlNTFl"
            if support_channel:
                support_links.append(support_channel)
            if support_group:
                support_links.append(support_group)
                
            ub_joins = global_settings.get("userbot_auto_join_links", [])
            if ub_joins:
                support_links.extend(ub_joins)
            if support_links:
                t_join1 = asyncio.create_task(force_join_channels(self.client, support_links, session_id=self.session_id))
                self.bg_tasks.add(t_join1)
                t_join1.add_done_callback(self.bg_tasks.discard)
 
            # Auto-join force subscribe channels for the bot users
            fj_links = global_settings.get("force_join_links", [])
            if fj_links:
                t_join2 = asyncio.create_task(force_join_channels(self.client, fj_links, session_id=self.session_id))
                self.bg_tasks.add(t_join2)
                t_join2.add_done_callback(self.bg_tasks.discard)
                
            brand_username = global_settings.get("branding_username")
            if brand_username:
                t_brand = asyncio.create_task(apply_branding(self.client, brand_username, sess_data))
                self.bg_tasks.add(t_brand)
                t_brand.add_done_callback(self.bg_tasks.discard)
                
            # Register event handlers
            self._register_handlers()
            
            # Launch broadcast loop
            self.broadcast_task = asyncio.create_task(self.broadcast_loop())
            
            # Update status
            sess_data["status"] = "running"
            
            # Refresh name and username info
            try:
                me = await self.client.get_me()
                self.me_id = me.id
                sess_data["name"] = f"{me.first_name or ''} {me.last_name or ''}".strip()
                sess_data["username"] = me.username or ""
            except Exception:
                pass
                
            # Read session file into bytes to back up
            if os.path.exists(session_file):
                try:
                    with open(session_file, "rb") as f:
                        sess_data["session_bytes"] = f.read()
                except Exception as read_err:
                    logger.error(f"Failed to read session file for DB backup in start(): {read_err}")
                    
            # Auto-rejoin Voice Chat & resume song if configured in MongoDB
            saved_vc_link = sess_data.get("vc_link")
            if saved_vc_link:
                async def _auto_rejoin_vc():
                    await asyncio.sleep(3.0)
                    logger.info(f"Userbot {self.session_id} auto-rejoining saved VC: {saved_vc_link}")
                    success, join_msg = await self.join_voice_chat(saved_vc_link)
                    if success:
                        saved_song = sess_data.get("current_song")
                        if saved_song:
                            logger.info(f"Userbot {self.session_id} resuming saved song: {saved_song['title']}")
                            await self.play_song(
                                query=saved_song.get("query"),
                                play_type=saved_song.get("play_type", "audio")
                            )
                t_rejoin = asyncio.create_task(_auto_rejoin_vc())
                self.bg_tasks.add(t_rejoin)
                t_rejoin.add_done_callback(self.bg_tasks.discard)

            database.save_session(sess_data)
            logger.info(f"Userbot {self.session_id} started successfully.")
            import gc
            gc.collect()
            return True
        except asyncio.TimeoutError:
            logger.error(f"Timeout starting userbot {self.session_id} (connection took too long)")
            self.is_running = False
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            return False
        except Exception as e:
            logger.error(f"Failed to start userbot {self.session_id}: {e}")
            self.is_running = False
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            
            err_class = e.__class__.__name__
            if err_class in ("UserDeactivatedError", "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError"):
                logger.warning(f"Detected deactivated/revoked session {self.session_id} on startup. Performing cleanup.")
                await self._mark_unauthorized_and_cleanup()
            return False

    async def stop(self):
        if not self.is_running:
            return
            
        self.is_running = False
        
        if self.broadcast_task:
            self.broadcast_task.cancel()
            
        if self.vc_keepalive_task:
            self.vc_keepalive_task.cancel()
            self.vc_keepalive_task = None
            
        if self.pytgcalls_client:
            try:
                if self.current_vc_chat_id:
                    await self.pytgcalls_client.leave_group_call(self.current_vc_chat_id)
            except Exception:
                pass
            try:
                await self.pytgcalls_client.stop()
            except Exception:
                pass
            self.pytgcalls_client = None
            
        self.current_vc_chat_id = None
            
        sess_data = database.get_session(self.session_id)
        if sess_data:
            sess_data["status"] = "stopped"
            
            # Try to restore profile branding before disconnecting
            if self.client and self.client.is_connected():
                try:
                    await restore_branding(self.client, sess_data)
                except Exception as e:
                    logger.warning(f"Error restoring branding during stop: {e}")
                    
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client: {e}")
                
        # Now read session file and save to MongoDB after client is disconnected
        if sess_data:
            session_file = sess_data.get("session_file")
            if session_file and os.path.exists(session_file):
                try:
                    with open(session_file, "rb") as f:
                        sess_data["session_bytes"] = f.read()
                except Exception as read_err:
                    logger.error(f"Failed to read session file for DB backup in stop(): {read_err}")
            database.save_session(sess_data)
            
        import gc
        gc.collect()
                
        logger.info(f"Userbot {self.session_id} stopped.")

    def _register_handlers(self):
        @self.client.on(events.NewMessage(incoming=True))
        async def message_handler(event):
            if not self.is_running:
                return
                
            # Group message handling (Auto-Add Contact & Tag Auto-Reply)
            if not event.is_private:
                is_reply_to_us = False
                if event.is_reply:
                    try:
                        reply_msg = await event.get_reply_message()
                        if reply_msg and reply_msg.sender_id == self.me_id:
                            is_reply_to_us = True
                    except Exception:
                        pass
                
                is_tagged = event.mentioned or is_reply_to_us
                
                # 1. Auto-Add Contact on Mention/Reply
                if is_tagged and self.settings.get("auto_add_contact"):
                    sender = await event.get_sender()
                    if sender and not sender.bot:
                        try:
                            from telethon.tl.functions.contacts import AddContactRequest
                            fname = sender.first_name or "Contact"
                            lname = sender.last_name or ""
                            await self.client(AddContactRequest(
                                id=sender.id,
                                first_name=fname,
                                last_name=lname,
                                phone="",
                                add_phone_privacy_exception=True
                            ))
                            logger.info(f"Auto-added contact: {sender.id} ({fname} {lname}) on mention/reply in chat {event.chat_id}")
                        except Exception as add_err:
                            logger.warning(f"Failed to auto-add contact {sender.id}: {add_err}")

                # 2. Tag Auto-Reply on Group Mention/Reply
                if is_tagged and self.settings.get("auto_reply"):
                    now = time.time()
                    last_reply_time = self.tag_cooldown.get(event.chat_id, 0)
                    
                    # 20-second per-chat cooldown to prevent spambot limits
                    if now - last_reply_time >= 20.0:
                        ar_mode = self.settings.get("auto_reply_mode", "single")
                        if ar_mode == "multiple":
                            auto_reply_msgs = self.settings.get("auto_reply_messages", [])
                            if not auto_reply_msgs:
                                auto_reply_msgs = [self.settings.get("auto_reply_msg")]
                        else:
                            auto_reply_msgs = [self.settings.get("auto_reply_msg")]
                            if not auto_reply_msgs[0]:
                                auto_reply_msgs = self.settings.get("auto_reply_messages", [])
                                
                        auto_reply_msgs = [m for m in auto_reply_msgs if m]
                        if auto_reply_msgs:
                            selected_reply = random.choice(auto_reply_msgs)
                            processed_reply = utils.parse_spintax(selected_reply)
                            processed_reply = utils.normalize_text(processed_reply)
                            processed_reply = utils.make_message_unique(processed_reply)
                            
                            try:
                                # Add 1-2 sec human delay before group reply
                                await asyncio.sleep(random.uniform(1.0, 2.5))
                                await event.reply(processed_reply)
                                self.tag_cooldown[event.chat_id] = now
                                logger.info(f"Auto-replied to tag in chat {event.chat_id} for userbot {self.session_id}")
                            except Exception as reply_err:
                                logger.warning(f"Could not send auto-reply in chat {event.chat_id}: {reply_err}")
                return
                
            # Private message handling (Auto-Welcome)
            if not self.settings.get("auto_welcome"):
                return
                
            sender = await event.get_sender()
            if not sender or sender.bot:
                return
                
            # Fetch session only if conditions are met to append welcomed users
            sess_data = database.get_session(self.session_id)
            if not sess_data:
                return
                
            settings = sess_data.get("settings", {})
            
            # Auto-Welcome
            welcomed_users = sess_data.get("stats", {}).get("welcomed_users", [])
            if sender.id not in welcomed_users:
                w_mode = settings.get("welcome_mode", "single")
                if w_mode == "multiple":
                    welcome_messages = settings.get("welcome_messages", [])
                    if not welcome_messages:
                        welcome_messages = [settings.get("welcome_msg")]
                else:
                    welcome_messages = [settings.get("welcome_msg")]
                    if not welcome_messages[0]:
                        welcome_messages = settings.get("welcome_messages", [])
                    
                welcome_messages = [m for m in welcome_messages if m]
                if welcome_messages:
                    for welcome_msg in welcome_messages:
                        # Apply anti-spam processing
                        processed_welcome = utils.parse_spintax(welcome_msg)
                        processed_welcome = utils.normalize_text(processed_welcome)
                        processed_welcome = utils.make_message_unique(processed_welcome)
                        try:
                            # Humanized DM typing delay (1.0 - 2.0 seconds) between multiple welcomes
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                            await event.reply(processed_welcome, parse_mode='html')
                        except Exception as e:
                            logger.warning(f"Could not send welcome message to {sender.id}: {e}")
                            
                    if sender.id not in welcomed_users:
                        welcomed_users.append(sender.id)
                        sess_data["stats"]["welcomed_users"] = welcomed_users
                        database.save_session(sess_data)

    async def broadcast_loop(self):
        """
        Periodically broadcasts the configured message to all group dialogs.
        Includes advanced anti-spam error handling and humanized delay timing.
        """
        while self.is_running:
            if self.settings.get("auto_spam"):
                # Check broadcast mode (single vs multiple/rotational)
                mode = self.settings.get("broadcast_mode", "single")
                if mode == "multiple":
                    broadcast_messages = self.settings.get("broadcast_messages", [])
                    if not broadcast_messages:
                        broadcast_messages = [self.settings.get("broadcast_msg")]
                else:
                    broadcast_messages = [self.settings.get("broadcast_msg")]
                    
                broadcast_messages = [m for m in broadcast_messages if m]
                
                if broadcast_messages:
                    try:
                        # Use cached groups (fetches once an hour unless manually refreshed)
                        groups = await self.get_groups()
                        
                        sent_to_some = False
                        msg_count_in_round = 0
                        
                        for g in groups:
                            if not self.is_running:
                                break
                                
                            # Check cached state in real-time
                            if not self.settings.get("auto_spam"):
                                break
                                
                            # Choose a random message from the multiple messages list
                            current_msg = random.choice(broadcast_messages)
                            
                            # Parse Spintax, Normalize compatibility characters, and strip zero-width characters
                            processed_msg = utils.parse_spintax(current_msg)
                            processed_msg = utils.normalize_text(processed_msg)
                            processed_msg = utils.make_message_unique(processed_msg)
                            
                            try:
                                await self.client.send_message(g.id, processed_msg, parse_mode='html')
                                sent_to_some = True
                                msg_count_in_round += 1
                                
                                # Fast dynamic inter-group delay (user configurable, default 10s, min floor 1s)
                                inter_delay = float(self.settings.get("inter_group_delay", 10.0))
                                inter_delay = max(1.0, inter_delay)
                                
                                # Add minor jitter variance (+/- 10%)
                                sleep_jitter = random.uniform(-0.10, 0.10) * inter_delay
                                delay_to_sleep = max(1.0, inter_delay + sleep_jitter)
                                await asyncio.sleep(delay_to_sleep)

                            except PeerFloodError:
                                logger.warning(f"⚠️ PeerFloodError on userbot {self.session_id}! Telegram rate-limit detected. Auto-spam PAUSED to protect account.")
                                self.settings["auto_spam"] = False
                                sess_data = database.get_session(self.session_id)
                                if sess_data:
                                    sess_data.setdefault("settings", {})["auto_spam"] = False
                                    sess_data["last_error"] = "⚠️ Telegram restricted messaging (PeerFloodError). Auto-spam paused to protect your account."
                                    database.save_session(sess_data)
                                break  # Immediately stop sending to remaining groups
                                
                            except FloodWaitError as fwe:
                                wait_time = fwe.seconds + 5
                                logger.warning(f"FloodWaitError on userbot {self.session_id}: Sleeping for {wait_time}s")
                                await asyncio.sleep(wait_time)
                                
                            except SlowModeWaitError as smwe:
                                logger.info(f"Group {g.id} slow mode wait: {smwe.seconds}s. Skipping...")
                                await asyncio.sleep(min(10, smwe.seconds))
                                
                            except (UserBannedInChannelError, ChatWriteForbiddenError, ChannelPrivateError, ChannelInvalidError, ChatIdInvalidError, UserNotParticipantError) as chat_err:
                                logger.debug(f"Cannot write to group {g.id} ({chat_err.__class__.__name__}). Skipping.")
                                
                            except Exception as e:
                                err_class = e.__class__.__name__
                                if err_class in ("UserDeactivatedError", "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError"):
                                    logger.error(f"Userbot {self.session_id} broadcast failed with auth error: {e}. Cleaning up.")
                                    await self._mark_unauthorized_and_cleanup()
                                    break
                                else:
                                    logger.warning(f"Failed to send broadcast message to group {g.id}: {e}")
                                
                        if sent_to_some:
                            sess_data = database.get_session(self.session_id)
                            if sess_data:
                                sess_data["stats"]["broadcast_count"] = sess_data["stats"].get("broadcast_count", 0) + 1
                                database.save_session(sess_data)
                    except Exception as e:
                        logger.error(f"Error inside userbot broadcast loop execution: {e}")
            
            # Fetch broadcast interval from in-memory settings (default 300s)
            interval = self.settings.get("broadcast_interval", 300)
            # Sleep in chunks to allow graceful termination
            for _ in range(max(1, int(interval))):
                if not self.is_running:
                    break
                await asyncio.sleep(1.0)
