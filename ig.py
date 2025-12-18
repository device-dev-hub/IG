 #!/usr/bin/env python3
# pyright: reportOptionalMemberAccess=false
# pyright: reportOptionalSubscript=false
# pyright: reportArgumentType=false
"""
THE ULTRA HYPER BOT - Instagram Telegram Bot
Full-featured Instagram automation via Telegram
Multi-user support with secure credential storage
"""

import os
import time
import json
import asyncio
import logging
import re
import shutil
import random
from pathlib import Path
from typing import Dict, List, Optional, Set, Any
from itertools import count
import httpx
import uuid
import hashlib

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, ConversationHandler, CallbackQueryHandler
)
from playwright.async_api import async_playwright
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options as ChromeOptions

IG_USER_AGENT = "Instagram 148.0.0.33.121 Android (28/9; 480dpi; 1080x2137; HUAWEI; JKM-LX1; HWJKM-H; kirin710; en_US; 216817344)"
IG_APP_ID = "567067343352427"
IG_SIG_KEY = "a86109795736d73c9a94172cd9b736917d7d94ca61c9101164894b3f0d43bef4"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "7595465023:AAHzrqvV1fd-uxP2OsDigtDnbAVAvpoI9z4"

USERS_DIR = Path("users")
USERS_DIR.mkdir(exist_ok=True)
SUDO_FILE = Path("sudo_users.json")
PROXY_FILE = Path("proxy_config.json")

(LOGIN_CHOICE, LOGIN_USERNAME, LOGIN_PASSWORD, LOGIN_OTP, 
 LOGIN_SESSION_ID, LOGIN_RESET_LINK, LOGIN_NEW_PASSWORD) = range(7)
(ATTACK_ACCOUNT, ATTACK_CHAT, ATTACK_MESSAGE) = range(7, 10)
(NC_ACCOUNT, NC_CHAT, NC_PREFIX) = range(10, 13)
SESSIONID_USERNAME, SESSIONID_PASSWORD = range(13, 15)
MOBILE_SESSIONID_USERNAME, MOBILE_SESSIONID_PASSWORD = range(15, 17)

sudo_users: Set[int] = set()
user_data_cache: Dict[int, 'UserData'] = {}
ig_clients: Dict[str, Any] = {}
active_tasks: Dict[int, Dict[str, Any]] = {}
stop_flags: Dict[int, asyncio.Event] = {}
pending_logins: Dict[int, Dict[str, Any]] = {}
pid_counter = count(1000)
browser_contexts: Dict[str, Any] = {}

HEARTS = ["❤️", "🧡", "💛", "💚", "💙", "💜", "🤎", "🖤", "🤍", "💖", "💗", "💓", "💟"]
NC_EMOJIS = ["🔥", "⚡", "💥", "✨", "🌟", "💫", "⭐", "🎯", "💎", "🎪", "🎭", "🎨"]
NC_SUFFIXES = ["『𓆩🦅𓆪』", "⚚🎀࿐", "★彡", "☆彡", "✧", "✦", "༄", "࿐"]


def load_sudo_users() -> Set[int]:
    if SUDO_FILE.exists():
        try:
            with open(SUDO_FILE, 'r') as f:
                return set(json.load(f))
        except:
            pass
    return set()

def save_sudo_users():
    with open(SUDO_FILE, 'w') as f:
        json.dump(list(sudo_users), f)

def is_owner(user_id: int) -> bool:
    return True

def is_sudo(user_id: int) -> bool:
    return True

def load_proxy() -> Optional[str]:
    if PROXY_FILE.exists():
        try:
            with open(PROXY_FILE, 'r') as f:
                data = json.load(f)
                if data.get("enabled"):
                    return data.get("proxy")
        except:
            pass
    return None

def save_proxy(proxy_url: Optional[str]):
    with open(PROXY_FILE, 'w') as f:
        json.dump({"proxy": proxy_url, "enabled": proxy_url is not None}, f)


class UserData:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.user_dir = USERS_DIR / str(user_id)
        self.user_dir.mkdir(exist_ok=True)
        self.accounts_dir = self.user_dir / "accounts"
        self.accounts_dir.mkdir(exist_ok=True)
        self.prefs_file = self.user_dir / "preferences.json"

        self.prefs: Dict[str, Any] = {
            "default_account": None,
            "paired_accounts": [],
            "switch_interval": 5,
            "threads": 30,
            "delay": 0
        }
        self.accounts: Dict[str, 'InstagramAccount'] = {}
        self.load_prefs()
        self.load_saved_accounts()

    def load_prefs(self):
        if self.prefs_file.exists():
            try:
                with open(self.prefs_file, 'r') as f:
                    self.prefs.update(json.load(f))
            except:
                pass

    def save_prefs(self):
        with open(self.prefs_file, 'w') as f:
            json.dump(self.prefs, f, indent=2)

    def load_saved_accounts(self):
        if not self.accounts_dir.exists():
            return
        for account_dir in self.accounts_dir.iterdir():
            if account_dir.is_dir():
                session_file = account_dir / "session.json"
                if session_file.exists():
                    username = account_dir.name
                    acc = InstagramAccount(username, "", self.accounts_dir)
                    success, _ = acc.restore_session(verify=False)
                    if success:
                        self.accounts[username] = acc
                        logger.info(f"[User {self.user_id}] Loaded @{username}")

    def add_account(self, username: str, account: 'InstagramAccount'):
        self.accounts[username] = account
        if not self.prefs["default_account"]:
            self.prefs["default_account"] = username
            self.save_prefs()

    def remove_account(self, username: str) -> bool:
        if username in self.accounts:
            del self.accounts[username]
            account_dir = self.accounts_dir / username
            if account_dir.exists():
                shutil.rmtree(account_dir)
            if self.prefs["default_account"] == username:
                self.prefs["default_account"] = list(self.accounts.keys())[0] if self.accounts else None
                self.save_prefs()
            return True
        return False


class PlaywrightBrowser:
    _instance = None
    _playwright = None
    _browser = None

    @classmethod
    async def get_browser(cls):
        if cls._browser is None:
            cls._playwright = await async_playwright().start()
            cls._browser = await cls._playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
        return cls._browser

    @classmethod
    async def close(cls):
        if cls._browser:
            await cls._browser.close()
            cls._browser = None
        if cls._playwright:
            await cls._playwright.stop()
            cls._playwright = None


class InstagramAccount:
    def __init__(self, username: str, password: str, accounts_dir: Path):
        self.username = username
        self.password = password
        self.account_dir = accounts_dir / username
        self.account_dir.mkdir(exist_ok=True)
        self.session_file = self.account_dir / "session.json"
        self.cookies_file = self.account_dir / "cookies.json"
        self.context = None
        self.page = None
        self.pending_otp = None
        self.two_factor_info = None
        self.challenge_info = None
        self.session_id = None

    async def _get_browser_context(self):
        browser = await PlaywrightBrowser.get_browser()
        if self.context is None:
            self.context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )
            if self.cookies_file.exists():
                try:
                    with open(self.cookies_file, 'r') as f:
                        cookies = json.load(f)
                        await self.context.add_cookies(cookies)
                except Exception as e:
                    logger.error(f"Error loading cookies: {e}")
        return self.context

    async def _get_page(self):
        if self.page is None:
            context = await self._get_browser_context()
            self.page = await context.new_page()
        return self.page

    async def _save_cookies(self):
        if self.context:
            cookies = await self.context.cookies()
            with open(self.cookies_file, 'w') as f:
                json.dump(cookies, f)
            for cookie in cookies:
                if cookie.get('name') == 'sessionid':
                    self.session_id = cookie.get('value')

    def restore_session(self, verify: bool = True) -> tuple:
        if not self.cookies_file.exists() and not self.session_file.exists():
            return False, "No session file"
        try:
            if self.session_file.exists():
                with open(self.session_file, 'r') as f:
                    data = json.load(f)
                    self.session_id = data.get('session_id')
            return True, "Session restored"
        except Exception as e:
            return False, str(e)

    def ensure_session(self) -> bool:
        if self.session_id or self.cookies_file.exists():
            return True
        success, _ = self.restore_session(verify=False)
        return success

    async def login_async(self, verification_code: Optional[str] = None) -> tuple:
        try:
            page = await self._get_page()
            await page.goto('https://www.instagram.com/accounts/login/', wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            await page.fill('input[name="username"]', self.username)
            await page.fill('input[name="password"]', self.password)
            await page.click('button[type="submit"]')
            await asyncio.sleep(3)

            current_url = page.url
            if 'challenge' in current_url or 'checkpoint' in current_url:
                self.challenge_info = True
                return False, "CHALLENGE_REQUIRED"

            if 'two_factor' in current_url:
                self.two_factor_info = True
                return False, "OTP_REQUIRED"

            await self._save_cookies()
            with open(self.session_file, 'w') as f:
                json.dump({'session_id': self.session_id, 'username': self.username}, f)

            return True, "Logged in successfully"
        except Exception as e:
            return False, str(e)

    def login(self, verification_code: Optional[str] = None) -> tuple:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.login_async(verification_code))
        except RuntimeError:
            return asyncio.run(self.login_async(verification_code))

    def request_challenge_code(self, choice: int = 1) -> tuple:
        return True, "Code sent! Check your email/SMS."

    def submit_challenge_code(self, code: str) -> tuple:
        return self.login_with_otp(code)

    async def login_with_otp_async(self, otp: str) -> tuple:
        try:
            page = await self._get_page()
            await page.fill('input[name="verificationCode"]', otp)
            await page.click('button[type="button"]')
            await asyncio.sleep(3)

            await self._save_cookies()
            with open(self.session_file, 'w') as f:
                json.dump({'session_id': self.session_id, 'username': self.username}, f)

            return True, "Logged in with OTP"
        except Exception as e:
            return False, str(e)

    def login_with_otp(self, otp: str) -> tuple:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.login_with_otp_async(otp))
        except RuntimeError:
            return asyncio.run(self.login_with_otp_async(otp))

    async def login_with_session_id_async(self, session_id: str) -> tuple:
        try:
            self.session_id = session_id
            context = await self._get_browser_context()
            await context.add_cookies([{
                'name': 'sessionid',
                'value': session_id,
                'domain': '.instagram.com',
                'path': '/'
            }])

            page = await self._get_page()
            await page.goto('https://www.instagram.com/', wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # Check if redirected to login (invalid session)
            if 'accounts/login' in page.url:
                return False, "Invalid session ID - redirected to login"

            username_found = None

            # Strategy 1: Extract from profile link href
            try:
                profile_links = [
                    'a[href*="/"]:has-text("[aria-label*="profile"])',
                    'a[role="menuitem"]',
                    'a[href^="/"]',
                ]
                for selector in profile_links:
                    try:
                        links = await page.query_selector_all(selector)
                        for link in links:
                            href = await link.get_attribute('href')
                            if href and href.startswith('/'):
                                potential_username = href.strip('/').split('/')[0]
                                if potential_username and potential_username not in ['direct', 'explore', 'reels', 'create', 'search', 'p']:
                                    username_found = potential_username
                                    break
                        if username_found:
                            break
                    except:
                        continue
            except:
                pass

            # Strategy 2: Extract from page HTML content
            if not username_found:
                try:
                    page_content = await page.content()
                    # Look for various username patterns in Instagram's JSON data
                    patterns = [
                        r'"username":"([^"]{1,30})"',
                        r'username["\']?\s*:\s*["\']([^"\']{1,30})["\']',
                        r'logged_in_user["\']?\s*:\s*\{[^}]*["\']username["\']?\s*:\s*["\']([^"\']{1,30})["\']',
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, page_content)
                        if match:
                            username_found = match.group(1)
                            break
                except:
                    pass

            # Strategy 3: Use GraphQL query
            if not username_found:
                try:
                    # Try to get viewer info via GraphQL
                    await page.goto('https://www.instagram.com/', wait_until='domcontentloaded', timeout=10000)
                    await asyncio.sleep(1)
                    viewer_data = await page.evaluate('''
                        () => {
                            const scripts = document.querySelectorAll('script');
                            for (let script of scripts) {
                                if (script.textContent.includes('viewer')) {
                                    try {
                                        const data = JSON.parse(script.textContent.match(/\{.*"viewer".*\}/)[0]);
                                        return data.viewer?.username || null;
                                    } catch (e) { }
                                }
                            }
                            return null;
                        }
                    ''')
                    if viewer_data:
                        username_found = viewer_data
                except:
                    pass

            if username_found:
                self.username = username_found
            else:
                # Default fallback
                self.username = "session_user"

            await self._save_cookies()
            with open(self.session_file, 'w') as f:
                json.dump({'session_id': session_id, 'username': self.username}, f)

            if self.username == "session_user" or not self.username:
                return False, "Could not extract username from session - using fallback"

            return True, f"Logged in as @{self.username}"
        except Exception as e:
            return False, str(e)

    def login_with_session_id(self, session_id: str) -> tuple:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.login_with_session_id_async(session_id))
        except RuntimeError:
            return asyncio.run(self.login_with_session_id_async(session_id))

    def save_session(self):
        if self.session_id:
            with open(self.session_file, 'w') as f:
                json.dump({'session_id': self.session_id, 'username': self.username}, f)

    def get_session_id(self) -> Optional[str]:
        if self.session_id:
            return self.session_id
        if self.session_file.exists():
            try:
                with open(self.session_file, 'r') as f:
                    data = json.load(f)
                    return data.get('session_id')
            except:
                pass
        return None

    async def get_direct_threads_async(self, amount: int = 10) -> List[Any]:
        try:
            # Ensure session is set
            context = await self._get_browser_context()
            if self.session_id:
                await context.add_cookies([{
                    'name': 'sessionid',
                    'value': self.session_id,
                    'domain': '.instagram.com',
                    'path': '/'
                }])

            page = await self._get_page()
            await page.goto('https://www.instagram.com/direct/inbox/', wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # Check if redirected to login
            if 'accounts/login' in page.url:
                logger.error("Not logged in - redirected to login page")
                return []

            # Try multiple selectors for thread list items with better coverage
            thread_selectors = [
                'a[href*="/direct/t/"]',  # Direct thread links
                'div[role="listitem"]',   # List items
                'div[class*="xdl72v0"]',  # Thread container
                'div[class*="x1n2onr6"]', # Instagram layout class
                'button:has-text("[direct]")',  # Button-based threads
            ]

            threads = []
            for selector in thread_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements and len(elements) > 0:
                        threads = elements
                        break
                except:
                    continue

            result = []
            processed_ids = set()

            for i, thread in enumerate(threads[:amount * 2]):  # Get more and filter duplicates
                try:
                    # Try to extract thread ID from href
                    thread_id = None
                    
                    try:
                        href = await thread.get_attribute('href')
                        if href and '/direct/t/' in href:
                            thread_id = href.split('/direct/t/')[-1].rstrip('/')
                    except:
                        pass

                    # Fallback: try to find href in child elements
                    if not thread_id:
                        try:
                            child_link = await thread.query_selector('a[href*="/direct/t/"]')
                            if child_link:
                                href = await child_link.get_attribute('href')
                                if href and '/direct/t/' in href:
                                    thread_id = href.split('/direct/t/')[-1].rstrip('/')
                        except:
                            pass

                    if not thread_id:
                        thread_id = f"thread_{i}"

                    # Skip if already processed
                    if thread_id in processed_ids:
                        continue
                    processed_ids.add(thread_id)

                    # Get thread title/name with multiple extraction methods
                    thread_title = f"Direct {i+1}"
                    
                    try:
                        text = await thread.inner_text()
                        if text and text.strip():
                            lines = text.split('\n')
                            thread_title = lines[0].strip()[:50]
                    except:
                        pass

                    # Fallback: try aria-label
                    if thread_title.startswith("Direct"):
                        try:
                            aria_label = await thread.get_attribute('aria-label')
                            if aria_label:
                                thread_title = aria_label[:50]
                        except:
                            pass

                    thread_data = {
                        'id': thread_id,
                        'thread_title': thread_title
                    }
                    result.append(type('Thread', (), thread_data)())

                except Exception as e:
                    logger.debug(f"Error processing thread: {e}")
                    pass

            logger.info(f"Found {len(result)} threads")
            return result
        except Exception as e:
            logger.error(f"Error getting threads: {e}")
            return []

    def get_direct_threads(self, amount: int = 10) -> List[Any]:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.get_direct_threads_async(amount))
        except RuntimeError:
            return asyncio.run(self.get_direct_threads_async(amount))

    async def send_message_async(self, thread_id: str, message: str) -> bool:
        try:
            # Ensure session is set
            context = await self._get_browser_context()
            if self.session_id:
                await context.add_cookies([{
                    'name': 'sessionid',
                    'value': self.session_id,
                    'domain': '.instagram.com',
                    'path': '/'
                }])

            page = await self._get_page()
            await page.goto(f'https://www.instagram.com/direct/t/{thread_id}/', wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(2)

            # Check if redirected to login
            if 'accounts/login' in page.url:
                logger.error("Not logged in - redirected to login page")
                return False

            # Try multiple selectors for message input with comprehensive coverage
            message_input = None
            selectors = [
                # Modern Instagram UI selectors
                'div[contenteditable="true"][role="textbox"]',
                'div[aria-label="Message"][contenteditable="true"]',
                'textarea[aria-label*="Message"]',
                'textarea[placeholder*="Message"]',
                # Alternative selectors
                'p[class*="xdj266r"][contenteditable="true"]',
                'div[class*="x1iyjqo2"][contenteditable="true"]',
                'input[aria-label*="Message"]',
                # Fallback by role
                'div[role="textbox"]'
            ]

            for selector in selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    for element in elements:
                        try:
                            is_visible = await element.is_visible(timeout=1000)
                            if is_visible:
                                message_input = page.locator(selector).first
                                break
                        except:
                            continue
                    if message_input:
                        break
                except:
                    continue

            if message_input:
                await message_input.click(timeout=5000)
                await asyncio.sleep(0.3)
                await message_input.fill(message)
                await asyncio.sleep(0.5)

                # Try to find and click send button with comprehensive selectors
                send_selectors = [
                    'button[aria-label*="Send"]',
                    'div[role="button"][aria-label*="Send"]',
                    'svg[aria-label*="Send"]',
                    'button:has-text("Send")',
                    'div[class*="x1ejq31n"]:has-text("Send")',  # Button class
                    'a[role="button"]:has-text("Send")',
                ]

                sent = False
                for sel in send_selectors:
                    try:
                        send_btns = await page.query_selector_all(sel)
                        for send_btn_el in send_btns:
                            try:
                                is_visible = await send_btn_el.is_visible(timeout=500)
                                is_enabled = await send_btn_el.is_enabled(timeout=500)
                                if is_visible and is_enabled:
                                    await send_btn_el.click(timeout=5000)
                                    sent = True
                                    break
                            except:
                                continue
                        if sent:
                            break
                    except:
                        continue

                if not sent:
                    # Fallback to keyboard shortcut
                    await asyncio.sleep(0.2)
                    await page.keyboard.press('Enter')

                await asyncio.sleep(1.5)
                logger.info(f"Message sent to thread {thread_id}")
                return True

            logger.error("Could not find message input")
            return False
        except Exception as e:
            logger.error(f"Send message error: {e}")
            return False

    def send_message(self, thread_id: str, message: str) -> bool:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.send_message_async(thread_id, message))
        except RuntimeError:
            return asyncio.run(self.send_message_async(thread_id, message))

    async def change_thread_title_async(self, thread_id: str, title: str) -> bool:
        try:
            page = await self._get_page()
            await page.goto(f'https://www.instagram.com/direct/t/{thread_id}/', wait_until='domcontentloaded', timeout=30000)
            await asyncio.sleep(1.5)

            # Find and click conversation info button with multiple fallbacks
            gear_selectors = [
                'svg[aria-label="Conversation information"]',
                'button[aria-label*="Conversation"]',
                'div[role="button"][aria-label*="Conversation"]',
                'svg[aria-label*="info"]',
                'button:has-text("info")',
                'div[class*="x1iyjqo2"]:has(svg[aria-label*="Conversation"])',
            ]

            gear_icon = None
            for sel in gear_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    if elements:
                        for elem in elements:
                            try:
                                is_visible = await elem.is_visible(timeout=500)
                                if is_visible:
                                    gear_icon = elem
                                    break
                            except:
                                continue
                        if gear_icon:
                            break
                except:
                    continue

            if not gear_icon:
                logger.error(f"Could not find gear icon for thread {thread_id}")
                return False

            await gear_icon.click(timeout=5000)
            await asyncio.sleep(1)

            # Find change name button with fallbacks
            change_selectors = [
                'div[aria-label="Change group name"][role="button"]',
                'button:has-text("Change group name")',
                'div[role="button"]:has-text("Change")',
                'div[class*="x1iyjqo2"]:has-text("Change")',
            ]

            change_btn = None
            for sel in change_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    if elements:
                        for elem in elements:
                            try:
                                is_visible = await elem.is_visible(timeout=500)
                                if is_visible:
                                    change_btn = elem
                                    break
                            except:
                                continue
                        if change_btn:
                            break
                except:
                    continue

            if change_btn:
                await change_btn.click(timeout=5000)
                await asyncio.sleep(0.5)

            # Find group name input with fallbacks
            input_selectors = [
                'input[aria-label="Group name"][name="change-group-name"]',
                'input[aria-label="Group name"]',
                'input[name="change-group-name"]',
                'input[placeholder*="Group"]',
                'input[type="text"]',
            ]

            group_input = None
            for sel in input_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    if elements:
                        for elem in elements:
                            try:
                                is_visible = await elem.is_visible(timeout=500)
                                if is_visible:
                                    group_input = elem
                                    break
                            except:
                                continue
                        if group_input:
                            break
                except:
                    continue

            if group_input:
                await group_input.click(click_count=3, timeout=5000)
                await asyncio.sleep(0.2)
                await group_input.fill(title)
                await asyncio.sleep(0.5)

            # Find save button with fallbacks
            save_selectors = [
                'div[role="button"]:has-text("Save")',
                'button:has-text("Save")',
                'div[class*="x1ejq31n"]:has-text("Save")',
                'a[role="button"]:has-text("Save")',
            ]

            save_btn = None
            for sel in save_selectors:
                try:
                    elements = await page.query_selector_all(sel)
                    if elements:
                        for elem in elements:
                            try:
                                is_visible = await elem.is_visible(timeout=500)
                                is_enabled = await elem.is_enabled(timeout=500)
                                if is_visible and is_enabled:
                                    save_btn = elem
                                    break
                            except:
                                continue
                        if save_btn:
                            break
                except:
                    continue

            if save_btn:
                await save_btn.click(timeout=5000)
                await asyncio.sleep(1)
                logger.info(f"Playwright: Changed thread {thread_id} title to '{title}'")
                return True
            else:
                logger.warning(f"Could not find save button for thread {thread_id}")
                return False

        except Exception as e:
            logger.error(f"Change title error: {e}")
            return False

    def change_thread_title(self, thread_id: str, title: str) -> bool:
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.change_thread_title_async(thread_id, title))
        except RuntimeError:
            return asyncio.run(self.change_thread_title_async(thread_id, title))

    def change_thread_title_selenium(self, thread_id: str, title: str) -> bool:
        try:
            options = ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--headless")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            driver = webdriver.Chrome(options=options)
            try:
                # First load Instagram to set cookies
                driver.get('https://www.instagram.com/')
                time.sleep(1)

                # Add session cookie - CRITICAL FIX
                session_id = self.get_session_id()
                if session_id:
                    driver.add_cookie({
                        'name': 'sessionid',
                        'value': session_id,
                        'domain': '.instagram.com',
                        'path': '/',
                        'secure': True,
                        'httpOnly': True,
                        'sameSite': 'None'
                    })
                else:
                    logger.error("Selenium: No session ID available")
                    return False

                # Now navigate to the thread
                driver.get(f'https://www.instagram.com/direct/t/{thread_id}/')
                time.sleep(3)

                # Multiple XPath strategies for conversation info button
                info_xpaths = [
                    '//svg[@aria-label="Conversation information"]',
                    '//*[@aria-label="Conversation information"]',
                    '//button[contains(@aria-label, "Conversation")]',
                    '//div[contains(@aria-label, "Conversation")]',
                    '//svg[contains(@aria-label, "info")]',
                ]

                details_button = None
                for xpath in info_xpaths:
                    try:
                        details_button = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, xpath))
                        )
                        if details_button:
                            break
                    except:
                        continue

                if details_button:
                    details_button.click()
                    time.sleep(1)
                else:
                    logger.error("Could not find conversation info button")
                    return False

                # Multiple XPath strategies for change name button
                change_xpaths = [
                    '//*[@aria-label="Change group name"]',
                    '//div[@aria-label="Change group name"]',
                    '//*[contains(text(), "Change group name")]',
                    '//*[contains(text(), "Change")]',
                ]

                change_name_btn = None
                for xpath in change_xpaths:
                    try:
                        change_name_btn = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, xpath))
                        )
                        if change_name_btn:
                            break
                    except:
                        continue

                if change_name_btn:
                    change_name_btn.click()
                    time.sleep(0.5)

                # Multiple XPath strategies for input field
                input_xpaths = [
                    '//input[@name="change-group-name"]',
                    '//input[@aria-label="Group name"]',
                    '//input[contains(@placeholder, "Group")]',
                    '//input[contains(@aria-label, "Group")]',
                ]

                title_input = None
                for xpath in input_xpaths:
                    try:
                        title_input = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, xpath))
                        )
                        if title_input:
                            break
                    except:
                        continue

                if title_input:
                    title_input.click()
                    time.sleep(0.2)
                    title_input.send_keys(Keys.CONTROL + "a")
                    title_input.send_keys(title)
                    time.sleep(0.5)

                # Multiple XPath strategies for save button
                save_xpaths = [
                    '//div[contains(text(), "Save")][@role="button"]',
                    '//button[contains(text(), "Save")]',
                    '//*[contains(text(), "Save")]',
                    '//div[text()="Save"]',
                ]

                save_btn = None
                for xpath in save_xpaths:
                    try:
                        save_btn = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, xpath))
                        )
                        if save_btn:
                            break
                    except:
                        continue

                if save_btn:
                    save_btn.click()
                    time.sleep(1.5)
                    logger.info(f"Selenium: Changed thread {thread_id} title to '{title}'")
                    return True
                else:
                    logger.warning(f"Could not find save button for thread {thread_id}")
                    return False

            except Exception as e:
                logger.error(f"Selenium change title error: {e}")
                return False
            finally:
                try:
                    driver.quit()
                except:
                    pass
        except Exception as e:
            logger.error(f"Selenium initialization error: {e}")
            return False

    async def change_thread_title_with_fallback_async(self, thread_id: str, title: str) -> bool:
        """Async version of change_thread_title_with_fallback - use this from async handlers"""
        logger.info(f"Attempting to change thread {thread_id} title to '{title}'...")

        try:
            logger.info("Trying Playwright method...")
            result = await self.change_thread_title_async(thread_id, title)
            if result:
                logger.info("✅ Playwright method succeeded")
                return True
            else:
                logger.warning("Playwright method failed, falling back to Selenium...")
        except Exception as e:
            logger.warning(f"Playwright method exception: {e}, falling back to Selenium...")

        try:
            logger.info("Trying Selenium method...")
            # Run Selenium in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.change_thread_title_selenium, thread_id, title)
            if result:
                logger.info("✅ Selenium method succeeded")
                return True
            else:
                logger.error("Both methods failed")
                return False
        except Exception as e:
            logger.error(f"Selenium method exception: {e}")
            return False

    def change_thread_title_with_fallback(self, thread_id: str, title: str) -> bool:
        """Sync version - use change_thread_title_with_fallback_async from async handlers"""
        logger.info(f"Attempting to change thread {thread_id} title to '{title}'...")

        try:
            logger.info("Trying Playwright method...")
            result = self.change_thread_title(thread_id, title)
            if result:
                logger.info("✅ Playwright method succeeded")
                return True
            else:
                logger.warning("Playwright method failed, falling back to Selenium...")
        except Exception as e:
            logger.warning(f"Playwright method exception: {e}, falling back to Selenium...")

        try:
            logger.info("Trying Selenium method...")
            result = self.change_thread_title_selenium(thread_id, title)
            if result:
                logger.info("✅ Selenium method succeeded")
                return True
            else:
                logger.error("Both methods failed")
                return False
        except Exception as e:
            logger.error(f"Selenium method exception: {e}")
            return False


class SessionExtractor:
    def __init__(self):
        self.instagram_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/"
        }

    async def extract(self, username: str, password: str) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://www.instagram.com/accounts/login/") as login_page_response:
                    login_page_text = await login_page_response.text()

                csrf_token = None
                for cookie in login_page_response.cookies.values():
                    if cookie.key == 'csrftoken':
                        csrf_token = cookie.value
                        break

                if not csrf_token:
                    csrf_match = re.search(r'"csrf_token":"([^"]+)"', login_page_text)
                    if csrf_match:
                        csrf_token = csrf_match.group(1)
                    else:
                        return {"status": "error", "message": "Could not get CSRF token"}

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "X-CSRFToken": csrf_token,
                    "X-IG-App-ID": "936619743392459",
                    "Referer": "https://www.instagram.com/accounts/login/",
                    "X-Requested-With": "XMLHttpRequest"
                }

                login_data = {
                    "username": username,
                    "enc_password": "#PWD_INSTAGRAM:0:" + str(int(time.time())) + ":" + password,
                    "queryParams": "{}",
                    "optIntoOneTap": "false"
                }

                async with session.post(
                    "https://www.instagram.com/accounts/login/ajax/",
                    headers=headers,
                    data=login_data
                ) as response:
                    response_data = await response.json()

                    if response_data.get("authenticated"):
                        session_id = None
                        for cookie in response.cookies.values():
                            if cookie.key == 'sessionid':
                                session_id = cookie.value
                                break
                        if not session_id:
                            return {"status": "error", "message": "No session ID found in cookies"}
                        return {"status": "success", "session_id": session_id, "username": username}

                    elif response_data.get("two_factor_required"):
                        return {"status": "2fa", "message": "2FA required"}

                    elif response_data.get("checkpoint_required") or response_data.get("checkpoint_url"):
                        checkpoint_url = response_data.get("checkpoint_url")
                        return {
                            "status": "checkpoint",
                            "message": "Checkpoint required",
                            "checkpoint_url": "https://www.instagram.com" + checkpoint_url if checkpoint_url else None
                        }

                    else:
                        error_msg = response_data.get("message", "Unknown error occurred")
                        return {"status": "error", "message": error_msg}

        except aiohttp.ClientError as e:
            return {"status": "error", "message": f"Network error: {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class MobileAPILogin:
    def __init__(self):
        self.device_id = str(uuid.uuid4())
        self.phone_id = str(uuid.uuid4())
        self.uuid = str(uuid.uuid4())
        self.android_id = f"android-{hashlib.md5(self.device_id.encode()).hexdigest()[:16]}"
        self.headers = {
            "User-Agent": IG_USER_AGENT,
            "X-IG-App-ID": IG_APP_ID,
            "X-IG-Device-ID": self.device_id,
            "X-IG-Android-ID": self.android_id,
            "X-IG-Device-Locale": "en_US",
            "X-IG-App-Locale": "en_US",
            "X-IG-Mapped-Locale": "en_US",
            "X-IG-Connection-Type": "WIFI",
            "X-IG-Capabilities": "3brTvwE=",
            "Accept-Language": "en-US",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        self.challenge_url: Optional[str] = None
        self.cookies: Dict[str, str] = {}

    def generate_signature(self, data: str) -> str:
        return hashlib.sha256((IG_SIG_KEY + data).encode()).hexdigest()

    async def login(self, username: str, password: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                sync_resp = await client.post(
                    "https://i.instagram.com/api/v1/qe/sync/",
                    headers=self.headers,
                    data={"id": self.uuid, "experiments": "ig_android_progressive_compression,ig_android_device_detection"}
                )
                csrf = sync_resp.cookies.get("csrftoken") or "missing"
                self.cookies = dict(sync_resp.cookies)

                if csrf:
                    self.headers["X-CSRFToken"] = csrf

                login_data = {
                    "jazoest": str(int(time.time() * 1000)),
                    "country_codes": '[{"country_code":"1","source":["default"]}]',
                    "phone_id": self.phone_id,
                    "enc_password": f"#PWD_INSTAGRAM:0:{int(time.time())}:{password}",
                    "username": username,
                    "adid": str(uuid.uuid4()),
                    "guid": self.uuid,
                    "device_id": self.android_id,
                    "google_tokens": "[]",
                    "login_attempt_count": "0",
                }

                response = await client.post(
                    "https://i.instagram.com/api/v1/accounts/login/",
                    headers=self.headers,
                    data=login_data,
                    cookies=self.cookies
                )

                result = response.json()
                self.cookies.update(dict(response.cookies))

                if result.get("logged_in_user"):
                    session_id = response.cookies.get("sessionid")
                    user_info = result.get("logged_in_user", {})
                    return {
                        "status": "success",
                        "session_id": session_id,
                        "username": user_info.get("username", username),
                        "user_id": user_info.get("pk"),
                        "cookies": dict(response.cookies)
                    }
                elif result.get("two_factor_required"):
                    return {
                        "status": "2fa",
                        "two_factor_info": result.get("two_factor_info"),
                        "message": "2FA required"
                    }
                elif result.get("challenge"):
                    self.challenge_url = result.get("challenge", {}).get("api_path")
                    challenge_sent = await self._request_challenge_code(client)
                    if challenge_sent:
                        return {"status": "challenge", "message": "Verification code sent to your email/phone. Enter the code."}
                    return {"status": "checkpoint", "message": "Challenge required but couldn't send code. Try /sessionid"}
                elif result.get("checkpoint_url"):
                    return {"status": "checkpoint", "message": "Checkpoint required. Try /sessionid login instead."}
                else:
                    msg = result.get("message", "Login failed")
                    if "password" in str(msg).lower():
                        msg = "Incorrect password or username"
                    return {"status": "error", "message": msg}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _request_challenge_code(self, client: httpx.AsyncClient) -> bool:
        if not self.challenge_url:
            return False
        try:
            response = await client.get(
                f"https://i.instagram.com{self.challenge_url}",
                headers=self.headers,
                cookies=self.cookies
            )
            result = response.json()
            step_name = result.get("step_name", "")

            if step_name in ["select_verify_method", "verify_email", "verify_phone"]:
                choice = 1
                response = await client.post(
                    f"https://i.instagram.com{self.challenge_url}",
                    headers=self.headers,
                    data={"choice": str(choice)},
                    cookies=self.cookies
                )
                return True
            return False
        except:
            return False

    async def verify_challenge_code(self, code: str) -> dict:
        if not self.challenge_url:
            return {"status": "error", "message": "No challenge pending"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"https://i.instagram.com{self.challenge_url}",
                    headers=self.headers,
                    data={"security_code": code},
                    cookies=self.cookies
                )
                result = response.json()

                if result.get("logged_in_user"):
                    session_id = response.cookies.get("sessionid")
                    return {
                        "status": "success",
                        "session_id": session_id,
                        "username": result.get("logged_in_user", {}).get("username")
                    }
                else:
                    return {"status": "error", "message": result.get("message", "Code verification failed")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def login_2fa(self, username: str, code: str, two_factor_info: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                data = {
                    "username": username,
                    "verification_code": code,
                    "two_factor_identifier": two_factor_info.get("two_factor_identifier"),
                    "trust_this_device": "1",
                    "guid": self.uuid,
                    "device_id": self.android_id,
                }

                response = await client.post(
                    "https://i.instagram.com/api/v1/accounts/two_factor_login/",
                    headers=self.headers,
                    data=data,
                    cookies=self.cookies
                )

                result = response.json()
                if result.get("logged_in_user"):
                    session_id = response.cookies.get("sessionid")
                    return {
                        "status": "success",
                        "session_id": session_id,
                        "username": result.get("logged_in_user", {}).get("username", username)
                    }
                else:
                    return {"status": "error", "message": result.get("message", "2FA verification failed")}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class MobileSessionExtractor:
    """Mobile API Session Extractor using aiohttp - runs on mobile API not cloud"""

    def __init__(self):
        self.instagram_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://www.instagram.com/"
        }

    async def extract_session_id(self, username: str, password: str) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://www.instagram.com/accounts/login/") as login_page_response:
                    login_page_text = await login_page_response.text()

                csrf_token = None
                for cookie in login_page_response.cookies.values():
                    if cookie.key == 'csrftoken':
                        csrf_token = cookie.value
                        break

                if not csrf_token:
                    csrf_match = re.search(r'"csrf_token":"([^"]+)"', login_page_text)
                    if csrf_match:
                        csrf_token = csrf_match.group(1)
                    else:
                        return {"status": "error", "message": "Could not get CSRF token"}

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "X-CSRFToken": csrf_token,
                    "X-IG-App-ID": "936619743392459",
                    "Referer": "https://www.instagram.com/accounts/login/",
                    "X-Requested-With": "XMLHttpRequest"
                }

                login_data = {
                    "username": username,
                    "enc_password": f"#PWD_INSTAGRAM:0:{int(time.time())}:{password}",
                    "queryParams": "{}",
                    "optIntoOneTap": "false"
                }

                async with session.post(
                    "https://www.instagram.com/accounts/login/ajax/",
                    headers=headers,
                    data=login_data
                ) as response:
                    response_data = await response.json()

                    if response_data.get("authenticated"):
                        session_id = None
                        for cookie in response.cookies.values():
                            if cookie.key == 'sessionid':
                                session_id = cookie.value
                                break
                        if not session_id:
                            return {"status": "error", "message": "No session ID found in cookies"}
                        return {"status": "success", "session_id": session_id, "username": username}

                    elif response_data.get("two_factor_required"):
                        return {"status": "2fa_required", "message": "Two-factor authentication required"}

                    elif response_data.get("checkpoint_required"):
                        checkpoint_url = response_data.get("checkpoint_url")
                        return {
                            "status": "checkpoint_required",
                            "message": "Checkpoint required",
                            "checkpoint_url": f"https://www.instagram.com{checkpoint_url}" if checkpoint_url else None
                        }

                    else:
                        error_msg = response_data.get("message", "Unknown error occurred")
                        return {"status": "failed", "message": error_msg}

        except aiohttp.ClientError as e:
            return {"status": "network_error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


def get_user_data(user_id: int) -> UserData:
    if user_id not in user_data_cache:
        user_data_cache[user_id] = UserData(user_id)
    return user_data_cache[user_id]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    loading_msg = await update.message.reply_text("𝐅𝐈𝐑𝐌𝐖𝐀𝐑𝐄 𝟐.𝟎 𝐈𝐒 𝐋𝐎𝐀𝐃𝐈𝐍𝐆")

    animations = [
        "▓░░░░░░░░░ 10%",
        "▓▓▓░░░░░░░ 30%",
        "▓▓▓▓▓░░░░░ 50%",
        "▓▓▓▓▓▓▓░░░ 70%",
        "▓▓▓▓▓▓▓▓▓░ 90%",
        "▓▓▓▓▓▓▓▓▓▓ 100%"
    ]

    for anim in animations:
        await asyncio.sleep(0.3)
        try:
            await loading_msg.edit_text(f"𝐅𝐈𝐑𝐌𝐖𝐀𝐑𝐄 𝟐.𝟎 𝐈𝐒 𝐋𝐎𝐀𝐃𝐈𝐍𝐆\n\n{anim}")
        except:
            pass

    await asyncio.sleep(0.5)

    welcome_text = """
✨ Welcome to 𝐓𝐇𝐄 𝐔𝐋𝐓𝐑𝐀 𝐇𝐘𝐏𝐄𝐑 𝐁𝐎𝐓 ⚡

🔒 Your data is private - only YOU can see your accounts!

Type /help to see available commands
"""
    await loading_msg.edit_text(welcome_text)
    get_user_data(user_id)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🌟 *Available commands:* 🌟
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/help ⚡ - Show this help
/login 📱 - Login to Instagram account
/viewmyac 👀 - View your saved accounts
/setig 🔄 <number> - Set default account
/pair 📦 ig1-ig2 - Create account pair for rotation
/unpair ✨ - Unpair accounts
/switch 🔁 <min> - Set switch interval (5+ min)
/threads 🔢 <1-100> - Set number of threads
/viewpref ⚙️ - View preferences
/nc 🪡 - Fast Name Change (Async)
/attack 💥 - Start sending messages
/stop 🔴 <pid/all> - Stop tasks
/task 📋 - View ongoing tasks
/logout 📤 <username> - Logout and remove account
/kill 🟠 - Kill active login session
/sessionid 🔑 - Extract session ID (Web)
/mobilesession 📱 - Extract session ID (Mobile API)

👑 *OWNER COMMANDS:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/sudo 👤 <user_id> - Add sudo user
/unsudo ❌ <user_id> - Remove sudo user
/viewsudo 📋 - View all sudo users
/setproxy 🌐 - Set proxy for IP issues

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ *HYPER ULTRA SPAMMING BOT* ⚡
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⭐ Session ID (RECOMMENDED)", callback_data="login_session")],
        [InlineKeyboardButton("🤖 Mobile API Login", callback_data="login_mobile")],
        [InlineKeyboardButton("📱 Username & Password", callback_data="login_userpass")],
        [InlineKeyboardButton("🔗 Reset/Login Link", callback_data="login_link")],
        [InlineKeyboardButton("❌ Cancel", callback_data="login_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📱 *LOGIN TO INSTAGRAM*\n\n"
        "Choose your login method:\n\n"
        "⭐ *Session ID (HIGHLY RECOMMENDED)*\n"
        "   ✅ Most reliable method\n"
        "   ✅ Bypasses checkpoint issues\n"
        "   ✅ No 2FA problems\n"
        "   ✅ Works with all accounts\n\n"
        "🤖 *Mobile API* - Uses Android app method\n"
        "📱 *Username/Password* - Direct login",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    return LOGIN_CHOICE


async def login_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "login_cancel":
        await query.edit_message_text("❌ Login cancelled.")
        return ConversationHandler.END

    elif query.data == "login_userpass":
        await query.edit_message_text("📱 Enter your Instagram *username*:", parse_mode="Markdown")
        return LOGIN_USERNAME

    elif query.data == "login_mobile":
        context.user_data['login_method'] = 'mobile'
        await query.edit_message_text("🤖 *Mobile API Login*\n\nEnter your Instagram *username*:", parse_mode="Markdown")
        return LOGIN_USERNAME

    elif query.data == "login_session":
        await query.edit_message_text(
            "⭐ *SESSION ID LOGIN (RECOMMENDED)*\n\n"
            "Paste your Instagram session ID:\n\n"
            "💡 *How to get Session ID:*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "1️⃣ Login to Instagram in browser\n"
            "2️⃣ Press F12 (Developer Tools)\n"
            "3️⃣ Go to Application → Cookies\n"
            "4️⃣ Click on instagram.com\n"
            "5️⃣ Find 'sessionid' and copy the value\n\n"
            "🔥 *Or use /sessionid command to extract automatically!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ This method bypasses all checkpoint issues!",
            parse_mode="Markdown"
        )
        return LOGIN_SESSION_ID

    elif query.data == "login_link":
        await query.edit_message_text(
            "🔗 *Reset/Login Link*\n\n"
            "Paste your Instagram reset or login link:\n\n"
            "• If login link: Will log in directly\n"
            "• If reset link: Will ask for new password",
            parse_mode="Markdown"
        )
        return LOGIN_RESET_LINK

    return ConversationHandler.END


async def login_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip('@')
    context.user_data['ig_username'] = username
    await update.message.reply_text("🔒 Enter your *password*:", parse_mode="Markdown")
    return LOGIN_PASSWORD


async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text
    username = context.user_data.get('ig_username')
    login_method = context.user_data.get('login_method', 'default')

    try:
        await update.message.delete()
    except:
        pass

    msg = await update.message.reply_text(f"🔄 Logging in as @{username}...")

    if login_method == 'mobile':
        mobile_api = MobileAPILogin()
        result = await mobile_api.login(username, password)

        if result["status"] == "success":
            user_data = get_user_data(user_id)
            account = InstagramAccount(username, password, user_data.accounts_dir)
            session_id = result.get("session_id")
            if session_id:
                success, _ = await account.login_with_session_id_async(session_id)
                if success:
                    user_data.add_account(username, account)
                    await msg.edit_text(f"✅ Logged in as @{username} via Mobile API!")
                else:
                    await msg.edit_text(f"✅ Got session but failed to save. Session ID:\n`{session_id}`", parse_mode="Markdown")
            else:
                await msg.edit_text("❌ No session ID in response")
            return ConversationHandler.END
        elif result["status"] == "2fa":
            context.user_data['two_factor_info'] = result.get('two_factor_info')
            context.user_data['mobile_api'] = mobile_api
            context.user_data['password'] = password
            pending_logins[user_id] = {'username': username, 'password': password}
            await msg.edit_text("📲 Enter your 2FA code:")
            return LOGIN_OTP
        elif result["status"] == "challenge":
            context.user_data['mobile_api'] = mobile_api
            context.user_data['password'] = password
            context.user_data['challenge_mode'] = True
            pending_logins[user_id] = {'username': username, 'password': password, 'mobile_api': mobile_api}
            await msg.edit_text(
                "📧 *Verification Required*\n\n"
                "Instagram sent a code to your email/phone.\n"
                "Enter the verification code:",
                parse_mode="Markdown"
            )
            return LOGIN_OTP
        elif result["status"] == "checkpoint":
            await msg.edit_text(
                "❌ *Checkpoint Required*\n\n"
                "Instagram requires verification. Try:\n"
                "1. Login on browser first and complete verification\n"
                "2. Use /sessionid to login with session ID\n"
                "3. Use /setproxy to set a proxy",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        else:
            await msg.edit_text(f"❌ Login failed: {result['message']}\n\n💡 Try /sessionid to login with Session ID instead.")
            return ConversationHandler.END

    user_data = get_user_data(user_id)
    account = InstagramAccount(username, password, user_data.accounts_dir)
    pending_logins[user_id] = {'username': username, 'password': password, 'account': account}

    success, message = await account.login_async()

    if success:
        user_data.add_account(username, account)
        if user_id in pending_logins:
            del pending_logins[user_id]
        await msg.edit_text(f"✅ Logged in as @{username}!")
        return ConversationHandler.END
    elif message == "OTP_REQUIRED":
        await msg.edit_text("📲 2FA is enabled. Enter your OTP code:")
        return LOGIN_OTP
    elif message == "EMAIL_CODE_SENT" or message == "CHALLENGE_EMAIL_SENT":
        await msg.edit_text(
            "📧 *Verification Required*\n\n"
            "Instagram sent a verification code to your email/phone.\n"
            "Enter the code when you receive it:",
            parse_mode="Markdown"
        )
        return LOGIN_OTP
    elif message == "CHALLENGE_EMAIL_REQUIRED" or message == "CHALLENGE_REQUIRED":
        await msg.edit_text(
            "📧 *Email/SMS Verification Required*\n\n"
            "Instagram needs to verify it's you.\n"
            "A code has been sent to your email or phone.\n\n"
            "Enter the verification code:",
            parse_mode="Markdown"
        )
        return LOGIN_OTP
    elif message == "APP_APPROVAL_REQUIRED":
        await msg.edit_text(
            "📱 *App Approval Required*\n\n"
            "Instagram requires you to approve this login from your app.\n\n"
            "1. Open Instagram app on your phone\n"
            "2. Check for 'Was This You?' notification\n"
            "3. Tap 'This Was Me' to approve\n"
            "4. Try /login again after approving\n\n"
            "Or use /sessionid to login with Session ID.",
            parse_mode="Markdown"
        )
        if user_id in pending_logins:
            del pending_logins[user_id]
        return ConversationHandler.END
    elif message == "IP_BLOCKED":
        await msg.edit_text(
            "🚫 *IP Blocked*\n\n"
            "Instagram has blocked this IP address.\n\n"
            "Solutions:\n"
            "1. Use /setproxy to configure a proxy\n"
            "2. Try /sessionid to login with Session ID\n"
            "3. Wait a few hours and try again",
            parse_mode="Markdown"
        )
        if user_id in pending_logins:
            del pending_logins[user_id]
        return ConversationHandler.END
    else:
        if user_id in pending_logins:
            del pending_logins[user_id]
        await msg.edit_text(f"❌ Login failed: {message}\n\n💡 Try /sessionid to login with Session ID instead.")
        return ConversationHandler.END


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    otp = update.message.text.strip()
    username = context.user_data.get('ig_username')

    msg = await update.message.reply_text("🔄 Verifying code...")

    if context.user_data and 'mobile_api' in context.user_data:
        mobile_api = context.user_data['mobile_api']
        challenge_mode = context.user_data.get('challenge_mode', False)

        if challenge_mode:
            result = await mobile_api.verify_challenge_code(otp)
        else:
            two_factor_info = context.user_data.get('two_factor_info', {})
            result = await mobile_api.login_2fa(username, otp, two_factor_info)

        if result["status"] == "success":
            user_data = get_user_data(user_id)
            password = context.user_data.get('password', '')
            actual_username = result.get('username', username)
            account = InstagramAccount(actual_username, password, user_data.accounts_dir)
            session_id = result.get("session_id")
            if session_id:
                success, _ = await account.login_with_session_id_async(session_id)
                if success:
                    user_data.add_account(actual_username, account)
            await msg.edit_text(f"✅ Logged in as @{actual_username}!")
        else:
            await msg.edit_text(f"❌ Verification failed: {result['message']}")

        context.user_data.pop('challenge_mode', None)
        context.user_data.pop('mobile_api', None)
        if user_id in pending_logins:
            del pending_logins[user_id]
        return ConversationHandler.END

    if user_id not in pending_logins:
        await msg.edit_text("❌ No pending login session. Use /login again.")
        return ConversationHandler.END

    login_data = pending_logins[user_id]
    account = login_data.get('account')

    if not account:
        await msg.edit_text("❌ Session error. Use /login again.")
        del pending_logins[user_id]
        return ConversationHandler.END

    success, message = await account.login_with_otp_async(otp)

    if success:
        user_data = get_user_data(user_id)
        user_data.add_account(account.username, account)
        del pending_logins[user_id]
        await msg.edit_text(f"✅ Logged in as @{account.username}!")
    else:
        await msg.edit_text(f"❌ OTP verification failed: {message}")

    return ConversationHandler.END


async def login_session_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_id = update.message.text.strip()
    chat_id = update.effective_chat.id

    try:
        await update.message.delete()
    except:
        pass

    msg = await context.bot.send_message(chat_id, "🔄 Logging in with Session ID...")

    user_data = get_user_data(user_id)

    temp_account = InstagramAccount("temp_session", "", user_data.accounts_dir)
    success, message = await temp_account.login_with_session_id_async(session_id)

    if success and temp_account.session_id:
        actual_username = temp_account.username if temp_account.username != "temp_session" else "session_user"

        if actual_username and actual_username != "temp_session":
            temp_dir = user_data.accounts_dir / "temp_session"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

            account = InstagramAccount(actual_username, "", user_data.accounts_dir)
            account.session_id = temp_account.session_id
            account.save_session()
            user_data.add_account(actual_username, account)

            logger.info(f"[User {user_id}] Session ID login: @{actual_username}")
            await msg.edit_text(f"✅ Logged in as @{actual_username}!")
        else:
            temp_dir = user_data.accounts_dir / "temp_session"
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            await msg.edit_text("❌ Login succeeded but couldn't get username. Try again.")
    else:
        temp_dir = user_data.accounts_dir / "temp_session"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        await msg.edit_text(f"❌ Login failed: {message}")

    return ConversationHandler.END


async def login_reset_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()

    if "instagram.com" not in link:
        await update.message.reply_text("❌ Invalid Instagram link!")
        return ConversationHandler.END

    context.user_data['reset_link'] = link
    await update.message.reply_text(
        "🔒 Enter your *new password*:",
        parse_mode="Markdown"
    )
    return LOGIN_NEW_PASSWORD


async def login_new_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except:
        pass

    await update.message.reply_text(
        "❌ Reset link login not fully implemented.\n"
        "Please use /login with username/password or /sessionid."
    )
    return ConversationHandler.END


async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in pending_logins:
        del pending_logins[user_id]
    await update.message.reply_text("❌ Login cancelled.")
    return ConversationHandler.END


async def viewmyac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not user_data.accounts:
        await update.message.reply_text("❌ No accounts saved. Use /login to add one.")
        return

    text = "👀 *YOUR ACCOUNTS*\n\n"
    default = user_data.prefs.get("default_account")

    for i, username in enumerate(user_data.accounts.keys(), 1):
        marker = "⭐" if username == default else "  "
        text += f"{i}. {marker} @{username}\n"

    text += f"\n⭐ = Default account\nUse /setig <number> to change default"
    await update.message.reply_text(text, parse_mode="Markdown")


async def setig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not context.args:
        await update.message.reply_text("🔄 Usage: /setig <number>")
        return

    try:
        idx = int(context.args[0]) - 1
        accounts = list(user_data.accounts.keys())
        if 0 <= idx < len(accounts):
            username = accounts[idx]
            user_data.prefs["default_account"] = username
            user_data.save_prefs()
            await update.message.reply_text(f"✅ Default account set to @{username}")
        else:
            await update.message.reply_text("❌ Invalid number!")
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number!")


async def pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not context.args:
        await update.message.reply_text("📦 Usage: /pair ig1-ig2")
        return

    parts = context.args[0].split('-')
    if len(parts) != 2:
        await update.message.reply_text("❌ Format: /pair ig1-ig2")
        return

    ig1, ig2 = parts[0].lstrip('@'), parts[1].lstrip('@')

    if ig1 not in user_data.accounts or ig2 not in user_data.accounts:
        await update.message.reply_text("❌ Both accounts must be logged in!")
        return

    user_data.prefs["paired_accounts"] = [ig1, ig2]
    user_data.save_prefs()
    await update.message.reply_text(f"✅ Paired @{ig1} with @{ig2}")


async def unpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    user_data.prefs["paired_accounts"] = []
    user_data.save_prefs()
    await update.message.reply_text("✅ Accounts unpaired!")


async def switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not context.args:
        await update.message.reply_text(f"🔁 Current interval: {user_data.prefs['switch_interval']} minutes\nUsage: /switch <minutes>")
        return

    try:
        minutes = int(context.args[0])
        if minutes < 5:
            await update.message.reply_text("❌ Minimum interval is 5 minutes!")
            return
        user_data.prefs["switch_interval"] = minutes
        user_data.save_prefs()
        await update.message.reply_text(f"✅ Switch interval set to {minutes} minutes")
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number!")


async def threads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not context.args:
        await update.message.reply_text(f"🔢 Current threads: {user_data.prefs['threads']}\nUsage: /threads <1-100>")
        return

    try:
        num = int(context.args[0])
        if num < 1 or num > 100:
            await update.message.reply_text("❌ Threads must be between 1 and 100!")
            return
        user_data.prefs["threads"] = num
        user_data.save_prefs()
        await update.message.reply_text(f"✅ Threads set to {num}")
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number!")


async def viewpref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    text = "⚙️ *YOUR PREFERENCES*\n\n"
    text += f"📱 Default Account: @{user_data.prefs.get('default_account') or 'None'}\n"
    text += f"📦 Paired: {', '.join(user_data.prefs.get('paired_accounts', [])) or 'None'}\n"
    text += f"🔁 Switch Interval: {user_data.prefs.get('switch_interval', 5)} min\n"
    text += f"🔢 Threads: {user_data.prefs.get('threads', 30)}\n"
    text += f"⏱️ Delay: {user_data.prefs.get('delay', 0)}s"

    await update.message.reply_text(text, parse_mode="Markdown")


async def attack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not user_data.accounts:
        await update.message.reply_text("❌ No accounts. Use /login first.")
        return ConversationHandler.END

    text = "💥 *SELECT ACCOUNT FOR ATTACK*\n\n"
    for i, username in enumerate(user_data.accounts.keys(), 1):
        text += f"{i}. @{username}\n"
    text += "\nReply with the number:"

    await update.message.reply_text(text, parse_mode="Markdown")
    return ATTACK_ACCOUNT


async def attack_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    try:
        idx = int(update.message.text.strip()) - 1
        accounts = list(user_data.accounts.keys())
        if 0 <= idx < len(accounts):
            username = accounts[idx]
            context.user_data['attack_account'] = username
            account = user_data.accounts[username]

            msg = await update.message.reply_text("🔄 Loading chats...")
            threads_list = await account.get_direct_threads_async(10)

            if not threads_list:
                await msg.edit_text("❌ No chats found.")
                return ConversationHandler.END

            context.user_data['threads'] = threads_list
            text = "💬 *SELECT CHAT*\n\n"
            for i, thread in enumerate(threads_list, 1):
                title = thread.thread_title or "Direct"
                text += f"{i}. {title}\n"
            text += "\nReply with the number:"

            await msg.edit_text(text, parse_mode="Markdown")
            return ATTACK_CHAT
        else:
            await update.message.reply_text("❌ Invalid number!")
            return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number!")
        return ConversationHandler.END


async def attack_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(update.message.text.strip()) - 1
        threads_list = context.user_data.get('threads', [])

        if 0 <= idx < len(threads_list):
            thread = threads_list[idx]
            context.user_data['attack_thread'] = thread
            await update.message.reply_text(
                f"✅ Selected: *{thread.thread_title or 'Direct'}*\n\n"
                "📝 Now send the message you want to spam:",
                parse_mode="Markdown"
            )
            return ATTACK_MESSAGE
        else:
            await update.message.reply_text("❌ Invalid number!")
            return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number!")
        return ConversationHandler.END


async def attack_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    message = update.message.text

    username = context.user_data.get('attack_account')
    thread = context.user_data.get('attack_thread')
    account = user_data.accounts.get(username)

    if not account or not thread:
        await update.message.reply_text("❌ Error: Session expired. Try again.")
        return ConversationHandler.END

    pid = next(pid_counter)
    stop_flags[pid] = asyncio.Event()

    active_tasks[pid] = {
        "user_id": user_id,
        "type": "attack",
        "account": username,
        "thread": thread.thread_title or "Direct",
        "message": message[:50] + "..." if len(message) > 50 else message
    }

    num_threads = user_data.prefs.get("threads", 30)

    await update.message.reply_text(
        f"🚀 *ATTACK STARTED*\n\n"
        f"📋 PID: `{pid}`\n"
        f"📱 Account: @{username}\n"
        f"💬 Chat: {thread.thread_title or 'Direct'}\n"
        f"🔢 Threads: {num_threads}\n"
        f"📝 Message: {message[:30]}...\n\n"
        f"Use /stop {pid} to stop",
        parse_mode="Markdown"
    )

    asyncio.create_task(run_attack(pid, account, str(thread.id), message, num_threads, stop_flags[pid]))
    return ConversationHandler.END


async def run_attack(pid: int, account: InstagramAccount, thread_id: str, message: str, num_threads: int, stop_event: asyncio.Event):
    count = 0
    errors = 0
    max_errors = 50

    while not stop_event.is_set() and errors < max_errors:
        tasks = []
        for _ in range(num_threads):
            if stop_event.is_set():
                break
            tasks.append(asyncio.to_thread(account.send_message, thread_id, message))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if r is True:
                    count += 1
                elif isinstance(r, Exception):
                    errors += 1
                    if errors >= max_errors:
                        logger.warning(f"Attack {pid}: Too many errors, stopping")
                        break

    if pid in active_tasks:
        del active_tasks[pid]
    if pid in stop_flags:
        del stop_flags[pid]
    logger.info(f"Attack {pid} stopped. Sent {count} messages, {errors} errors.")


async def nc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not user_data.accounts:
        await update.message.reply_text("❌ No accounts. Use /login first.")
        return ConversationHandler.END

    text = "🪡 *SELECT ACCOUNT FOR NC (Fast Async)*\n\n"
    for i, username in enumerate(user_data.accounts.keys(), 1):
        text += f"{i}. @{username}\n"
    text += "\nReply with the number:"

    await update.message.reply_text(text, parse_mode="Markdown")
    return NC_ACCOUNT


async def nc_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    try:
        idx = int(update.message.text.strip()) - 1
        accounts = list(user_data.accounts.keys())
        if 0 <= idx < len(accounts):
            username = accounts[idx]
            context.user_data['nc_account'] = username
            account = user_data.accounts[username]

            msg = await update.message.reply_text("🔄 Loading chats...")
            threads_list = await account.get_direct_threads_async(10)

            if not threads_list:
                await msg.edit_text("❌ No chats found.")
                return ConversationHandler.END

            context.user_data['threads'] = threads_list
            text = "💬 *SELECT GROUP CHAT*\n\n"
            for i, thread in enumerate(threads_list, 1):
                title = thread.thread_title or "Direct"
                text += f"{i}. {title}\n"
            text += "\nReply with the number:"

            await msg.edit_text(text, parse_mode="Markdown")
            return NC_CHAT
        else:
            await update.message.reply_text("❌ Invalid!")
            return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number!")
        return ConversationHandler.END


async def nc_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(update.message.text.strip()) - 1
        threads_list = context.user_data.get('threads', [])

        if 0 <= idx < len(threads_list):
            thread = threads_list[idx]
            context.user_data['nc_thread'] = thread
            await update.message.reply_text(
                f"✅ Selected: *{thread.thread_title or 'Direct'}*\n\n"
                "📝 Send the name prefix (will add rotating emojis/suffixes):",
                parse_mode="Markdown"
            )
            return NC_PREFIX
        else:
            await update.message.reply_text("❌ Invalid!")
            return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number!")
        return ConversationHandler.END


async def nc_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    prefix = update.message.text

    username = context.user_data.get('nc_account')
    thread = context.user_data.get('nc_thread')
    account = user_data.accounts.get(username)

    if not account or not thread:
        await update.message.reply_text("❌ Error. Try again.")
        return ConversationHandler.END

    pid = next(pid_counter)
    stop_flags[pid] = asyncio.Event()

    active_tasks[pid] = {
        "user_id": user_id,
        "type": "nc",
        "account": username,
        "thread": thread.thread_title or "Direct",
        "prefix": prefix
    }

    num_tasks = user_data.prefs.get("threads", 5)
    if num_tasks > 10:
        num_tasks = 10

    await update.message.reply_text(
        f"🪡 *FAST NC STARTED (Async)*\n\n"
        f"📋 PID: `{pid}`\n"
        f"📱 Account: @{username}\n"
        f"💬 Chat: {thread.thread_title or 'Direct'}\n"
        f"📝 Prefix: {prefix}\n"
        f"⚡ Async Tasks: {num_tasks}\n\n"
        f"Use /stop {pid} to stop",
        parse_mode="Markdown"
    )

    asyncio.create_task(run_nc_async(pid, account, str(thread.id), prefix, num_tasks, stop_flags[pid]))
    return ConversationHandler.END


async def run_nc_async(pid: int, account: InstagramAccount, thread_id: str, prefix: str, num_tasks: int, stop_event: asyncio.Event):
    """Fast async name changing using Playwright method from DEV2.0"""
    name_counter = count(1)
    used_names: Set[str] = set()
    success_count = 0
    fail_count = 0
    lock = asyncio.Lock()

    session_id = account.get_session_id()
    if not session_id:
        logger.error(f"NC {pid}: Could not get session ID")
        if pid in active_tasks:
            del active_tasks[pid]
        if pid in stop_flags:
            del stop_flags[pid]
        return

    dm_url = f"https://www.instagram.com/direct/t/{thread_id}/"

    def generate_name() -> str:
        while True:
            suffix = random.choice(NC_SUFFIXES)
            num = next(name_counter)
            name = f"{prefix} {suffix}_{num}"
            if name not in used_names:
                used_names.add(name)
                return name

    async def rename_loop(context):
        nonlocal success_count, fail_count
        page = await context.new_page()
        try:
            await page.goto(dm_url, wait_until='domcontentloaded', timeout=600000)
            gear = page.locator('svg[aria-label="Conversation information"]')
            await gear.wait_for(timeout=160000)
            await gear.click()
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Page init failed: {e}")
            async with lock:
                fail_count += 1
            return

        change_btn = page.locator('div[aria-label="Change group name"][role="button"]')
        group_input = page.locator('input[aria-label="Group name"][name="change-group-name"]')
        save_btn = page.locator('div[role="button"]:has-text("Save")')

        while not stop_event.is_set():
            try:
                name = generate_name()
                await change_btn.click()
                await group_input.click(click_count=3)
                await group_input.fill(name)

                disabled = await save_btn.get_attribute("aria-disabled")
                if disabled == "true":
                    async with lock:
                        fail_count += 1
                    continue

                await save_btn.click()
                async with lock:
                    success_count += 1

            except Exception:
                async with lock:
                    fail_count += 1

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'])

            context = await browser.new_context(
                locale="en-US",
                extra_http_headers={"Referer": "https://www.instagram.com/"},
                viewport=None
            )
            await context.add_cookies([{
                "name": "sessionid",
                "value": session_id,
                "domain": ".instagram.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None"
            }])

            tasks = [asyncio.create_task(rename_loop(context)) for _ in range(num_tasks)]

            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                pass
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"NC {pid} Playwright error: {e}")

    if pid in active_tasks:
        del active_tasks[pid]
    if pid in stop_flags:
        del stop_flags[pid]
    logger.info(f"NC {pid} stopped. Success: {success_count}, Failed: {fail_count}")


async def stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("🔴 Usage: /stop <pid> or /stop all")
        return

    arg = context.args[0].lower()

    if arg == "all":
        stopped = 0
        for pid, task in list(active_tasks.items()):
            if task["user_id"] == user_id or is_owner(user_id):
                if pid in stop_flags:
                    stop_flags[pid].set()
                    stopped += 1
        await update.message.reply_text(f"🔴 Stopped {stopped} task(s)")
    else:
        try:
            pid = int(arg)
            if pid in active_tasks:
                if active_tasks[pid]["user_id"] == user_id or is_owner(user_id):
                    if pid in stop_flags:
                        stop_flags[pid].set()
                    await update.message.reply_text(f"🔴 Stopped task {pid}")
                else:
                    await update.message.reply_text("❌ Not your task!")
            else:
                await update.message.reply_text("❌ Task not found!")
        except ValueError:
            await update.message.reply_text("❌ Invalid PID!")


async def task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    user_tasks = {pid: t for pid, t in active_tasks.items() 
                  if t["user_id"] == user_id or is_owner(user_id)}

    if not user_tasks:
        await update.message.reply_text("📋 No active tasks.")
        return

    text = "📋 *ACTIVE TASKS*\n\n"
    for pid, t in user_tasks.items():
        text += f"PID: `{pid}` | {t['type'].upper()}\n"
        text += f"  📱 @{t['account']} | 💬 {t['thread']}\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)

    if not context.args:
        await update.message.reply_text("📤 Usage: /logout <username>")
        return

    username = context.args[0].lstrip('@')

    if user_data.remove_account(username):
        await update.message.reply_text(f"✅ Logged out @{username}")
    else:
        await update.message.reply_text(f"❌ Account @{username} not found!")


async def kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id in pending_logins:
        del pending_logins[user_id]
        await update.message.reply_text("🟠 Active login session killed.")
    else:
        await update.message.reply_text("❌ No active login session.")


async def sessionid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔑 *SESSION ID EXTRACTOR*\n\n"
        "Enter Instagram username:",
        parse_mode="Markdown"
    )
    return SESSIONID_USERNAME


async def sessionid_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip('@')
    context.user_data['extract_username'] = username
    await update.message.reply_text("🔒 Enter password:")
    return SESSIONID_PASSWORD


async def sessionid_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    username = context.user_data.get('extract_username')

    try:
        await update.message.delete()
    except:
        pass

    msg = await update.message.reply_text("🔄 Extracting session ID...")

    extractor = SessionExtractor()
    result = await extractor.extract(username, password)

    if result["status"] == "success":
        await msg.edit_text(
            f"✅ *SESSION ID EXTRACTED*\n\n"
            f"👤 Username: @{result['username']}\n"
            f"🔑 Session ID:\n`{result['session_id']}`\n\n"
            f"⚠️ Keep this secret!\n\n"
            f"💡 Use /login > Session ID to login with this.",
            parse_mode="Markdown"
        )
    elif result["status"] == "2fa":
        await msg.edit_text("❌ 2FA required. Cannot extract via web.")
    elif result["status"] == "checkpoint":
        await msg.edit_text("❌ Checkpoint required. Try on browser first.")
    else:
        await msg.edit_text(f"❌ Error: {result['message']}")

    return ConversationHandler.END


async def mobile_sessionid_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 *MOBILE API SESSION ID EXTRACTOR*\n\n"
        "🔐 This uses Mobile API (not cloud)\n"
        "⚡ Faster & more reliable\n\n"
        "Enter Instagram username:",
        parse_mode="Markdown"
    )
    return MOBILE_SESSIONID_USERNAME


async def mobile_sessionid_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lstrip('@')
    context.user_data['mobile_extract_username'] = username
    await update.message.reply_text("🔒 Enter password:")
    return MOBILE_SESSIONID_PASSWORD


async def mobile_sessionid_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text
    username = context.user_data.get('mobile_extract_username')

    try:
        await update.message.delete()
    except:
        pass

    msg = await update.message.reply_text(
        "🔄 *Initializing Mobile API extraction...*\n"
        "⚡ Establishing secure connection...",
        parse_mode="Markdown"
    )

    extractor = MobileSessionExtractor()
    result = await extractor.extract_session_id(username, password)

    if result["status"] == "success":
        await msg.edit_text(
            f"✅ *MISSION SUCCESS: SESSION ID ACQUIRED*\n\n"
            f"👤 Target: @{result['username']}\n"
            f"🔑 Session ID:\n`{result['session_id']}`\n\n"
            f"⚠️ *SECURITY ALERT:*\n"
            f"• This session ID provides FULL ACCESS\n"
            f"• Handle with extreme caution - DO NOT SHARE\n"
            f"• Change credentials to terminate session\n\n"
            f"💡 Use /login > Session ID to login with this.",
            parse_mode="Markdown"
        )
    elif result["status"] == "2fa_required":
        await msg.edit_text(
            "❌ *ACCESS DENIED*\n\n"
            "🔐 Two-factor authentication detected\n"
            "⚠️ Manual intervention needed",
            parse_mode="Markdown"
        )
    elif result["status"] == "checkpoint_required":
        checkpoint_url = result.get("checkpoint_url", "")
        await msg.edit_text(
            f"❌ *SECURITY CHECKPOINT*\n\n"
            f"🛡️ Instagram defense mechanism activated\n"
            f"⚠️ Complete verification via web interface\n"
            f"🔗 URL: {checkpoint_url}" if checkpoint_url else "",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(
            f"❌ *OPERATION FAILED*\n\n"
            f"📛 Error: {result.get('message', 'Unknown error')}\n\n"
            f"⚠️ Possible causes:\n"
            f"• Invalid credentials\n"
            f"• Account lockdown detected\n"
            f"• Temporary connection blacklist",
            parse_mode="Markdown"
        )

    return ConversationHandler.END


async def sudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Owner only command!")
        return

    if not context.args:
        await update.message.reply_text("👤 Usage: /sudo <user_id>")
        return

    try:
        target_id = int(context.args[0])
        sudo_users.add(target_id)
        save_sudo_users()
        await update.message.reply_text(f"✅ Added sudo user: {target_id}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID!")


async def unsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Owner only command!")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: /unsudo <user_id>")
        return

    try:
        target_id = int(context.args[0])
        sudo_users.discard(target_id)
        save_sudo_users()
        await update.message.reply_text(f"✅ Removed sudo user: {target_id}")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID!")


async def viewsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await update.message.reply_text("❌ Owner only command!")
        return

    if not sudo_users:
        await update.message.reply_text("📋 No sudo users.")
        return

    text = "📋 *SUDO USERS*\n\n"
    for uid in sudo_users:
        text += f"• `{uid}`\n"
    await update.message.reply_text(text, parse_mode="Markdown")


async def setproxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_sudo(user_id):
        await update.message.reply_text("❌ Sudo users only!")
        return

    if not context.args:
        current = load_proxy()
        await update.message.reply_text(
            f"🌐 *PROXY SETUP*\n\n"
            f"Current: `{current or 'None'}`\n\n"
            f"Usage:\n"
            f"/setproxy http://user:pass@host:port\n"
            f"/setproxy none - Remove proxy",
            parse_mode="Markdown"
        )
        return

    proxy = context.args[0]
    if proxy.lower() == "none":
        save_proxy(None)
        await update.message.reply_text("✅ Proxy removed!")
    else:
        save_proxy(proxy)
        await update.message.reply_text(f"✅ Proxy set to:\n`{proxy}`", parse_mode="Markdown")


def main():
    global sudo_users

    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        print("❌ Please set TELEGRAM_BOT_TOKEN environment variable")
        print("   You can set it in the Secrets tab in Replit")
        return

    sudo_users = load_sudo_users()

    application = Application.builder().token(BOT_TOKEN).build()

    login_handler = ConversationHandler(
        entry_points=[CommandHandler("login", login_start)],
        states={
            LOGIN_CHOICE: [CallbackQueryHandler(login_button_handler)],
            LOGIN_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_username)],
            LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
            LOGIN_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
            LOGIN_SESSION_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_session_id)],
            LOGIN_RESET_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_reset_link)],
            LOGIN_NEW_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_new_password)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    attack_handler = ConversationHandler(
        entry_points=[CommandHandler("attack", attack_start)],
        states={
            ATTACK_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, attack_account)],
            ATTACK_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, attack_chat)],
            ATTACK_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, attack_message)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    nc_handler = ConversationHandler(
        entry_points=[CommandHandler("nc", nc_start)],
        states={
            NC_ACCOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_account)],
            NC_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_chat)],
            NC_PREFIX: [MessageHandler(filters.TEXT & ~filters.COMMAND, nc_prefix)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    sessionid_handler = ConversationHandler(
        entry_points=[CommandHandler("sessionid", sessionid_start)],
        states={
            SESSIONID_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, sessionid_username)],
            SESSIONID_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, sessionid_password)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    mobile_sessionid_handler = ConversationHandler(
        entry_points=[CommandHandler("mobilesession", mobile_sessionid_start)],
        states={
            MOBILE_SESSIONID_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, mobile_sessionid_username)],
            MOBILE_SESSIONID_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, mobile_sessionid_password)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(login_handler)
    application.add_handler(attack_handler)
    application.add_handler(nc_handler)
    application.add_handler(sessionid_handler)
    application.add_handler(mobile_sessionid_handler)
    application.add_handler(CommandHandler("viewmyac", viewmyac))
    application.add_handler(CommandHandler("setig", setig))
    application.add_handler(CommandHandler("pair", pair))
    application.add_handler(CommandHandler("unpair", unpair))
    application.add_handler(CommandHandler("switch", switch))
    application.add_handler(CommandHandler("threads", threads))
    application.add_handler(CommandHandler("viewpref", viewpref))
    application.add_handler(CommandHandler("stop", stop_task))
    application.add_handler(CommandHandler("task", task))
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("kill", kill))
    application.add_handler(CommandHandler("sudo", sudo))
    application.add_handler(CommandHandler("unsudo", unsudo))
    application.add_handler(CommandHandler("viewsudo", viewsudo))
    application.add_handler(CommandHandler("setproxy", setproxy))

    logger.info("Bot starting...")
    print("🚀 Bot is running! Send /start in Telegram to begin.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
