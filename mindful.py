import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog
import sqlite3, psutil, threading, time, ctypes, json, webbrowser
from datetime import datetime
import pystray
from PIL import Image, ImageDraw
from plyer import notification
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# --- APPLE-INSPIRED UI SETTINGS ---
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# --- DATABASE SETUP ---
DB_LOCK = threading.Lock()
def init_db():
    with DB_LOCK:
        conn = sqlite3.connect('mindfulness.db', check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS tracked_items (id INTEGER PRIMARY KEY, name TEXT UNIQUE, type TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS usage_logs (id INTEGER PRIMARY KEY, item_name TEXT, timestamp DATETIME, reason TEXT, requested_mins REAL, actual_seconds INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
        
        defaults = {
            'water': '1', 'quote_toggle': '0', 'quote_text': 'Breathe. Stay focused on your goals.',
            'block_non_edu': '0', 'remind_non_edu': '1', 'block_shorts': '0', 'block_reels': '1',
            'focus_quote': 'The video you are watching is NOT related to your goal in life. Please exercise caution and stay focused.'
        }
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        conn.commit()
        return conn

conn = init_db()

# --- GLOBALS ---
active_sessions = {}
is_prompting = False
grace_period_apps = {} 
app_reference = None 

# --- BROWSER SCRIPT (SERVED VIA LOCALHOST) ---
USERSCRIPT_JS = """// ==UserScript==
// @name         Mindful Consumption Enforcer
// @namespace    http://tampermonkey.net/
// @version      3.0
// @description  Hides shorts/reels and natively enforces educational focus.
// @match        *://*.youtube.com/*
// @match        *://*.instagram.com/*
// @grant        none
// ==/UserScript==

(function() {
    'use strict';
    const API = "http://127.0.0.1:49321";
    let config = {};
    let wasPrompting = false;

    function applyCSS() {
        if (document.getElementById('mindful-css')) return;
        let style = document.createElement('style');
        style.id = 'mindful-css';
        let css = "";
        if (config.block_shorts === '1') css += `ytd-rich-shelf-renderer[is-shorts], ytd-reel-shelf-renderer, a[title="Shorts"] { display: none !important; }`;
        if (config.block_reels === '1') css += `a[href*="/reels/"] { display: none !important; }`;
        style.innerHTML = css;
        document.head.appendChild(style);
    }

    function getCategory() {
        try {
            let player = document.getElementById('movie_player');
            if (player && player.getPlayerResponse) {
                let data = player.getPlayerResponse();
                return data.microformat.playerMicroformatRenderer.category;
            }
        } catch(e) {}
        let meta = document.querySelector('meta[itemprop="genre"]');
        if (meta && meta.content) return meta.content;
        return "Unknown";
    }

    function checkVideo() {
        if (!window.location.href.includes("watch")) return;
        
        let cat = getCategory();
        if (cat === "Unknown" || cat === "Education") return;

        const player = document.querySelector('video');
        if (!player) return;

        fetch(`${API}/check_yt?cat=${encodeURIComponent(cat)}`)
            .then(r => r.json())
            .then(data => {
                if (data.status === "prompting") {
                    wasPrompting = true;
                    if (!player.paused) player.pause();
                } else if (data.status === "allowed") {
                    if (wasPrompting) {
                        wasPrompting = false;
                        player.play(); 
                    }
                }
            }).catch(e => {});
    }

    function loadConfig() {
        fetch(`${API}/config`)
            .then(r => r.json())
            .then(data => {
                config = data;
                applyCSS();
            }).catch(e => {});
    }

    loadConfig();
    setInterval(loadConfig, 5000); 
    if (window.location.host.includes("youtube.com")) {
        setInterval(checkVideo, 1000); 
    }
})();
"""

# --- WINDOWS API ---
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def get_foreground_info():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.lower()
    
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try: exe_name = psutil.Process(pid.value).name().lower()
    except: exe_name = ""
    return title, exe_name, pid.value, hwnd

def get_open_windows():
    windows = []
    def enum_cb(hwnd, _):
        if ctypes.windll.user32.IsWindowVisible(hwnd):
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if title and title != "Program Manager":
                    pid = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    try:
                        exe = psutil.Process(pid.value).name()
                        windows.append((title, exe))
                    except: pass
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(enum_cb)
    ctypes.windll.user32.EnumWindows(cb, 0)
    return windows

# --- LIGHTWEIGHT LOCALHOST SERVER ---
class MindfulServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass 
    
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()
        
    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        
        if self.path == '/config':
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            with DB_LOCK:
                c = conn.cursor()
                data = {row[0]: row[1] for row in c.execute("SELECT * FROM settings").fetchall()}
            self.wfile.write(json.dumps(data).encode())
            
        elif self.path == '/mindful.user.js':
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Disposition', 'inline; filename="mindful.user.js"')
            self.end_headers()
            self.wfile.write(USERSCRIPT_JS.encode('utf-8'))
            
        elif self.path.startswith('/check_yt'):
            query = parse_qs(urlparse(self.path).query)
            cat = query.get('cat', ['Unknown'])[0]
            
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            with DB_LOCK:
                c = conn.cursor()
                conf = {row[0]: row[1] for row in c.execute("SELECT key, value FROM settings").fetchall()}

            b_edu = conf.get('block_non_edu', '0') == '1'
            r_edu = conf.get('remind_non_edu', '1') == '1'

            if not b_edu and not r_edu:
                self.wfile.write(json.dumps({"status": "allowed"}).encode())
                return

            current_time = time.time()
            session = active_sessions.get("youtube_focus")
            
            if session and current_time < session["end_time"]:
                self.wfile.write(json.dumps({"status": "allowed"}).encode())
                return

            global is_prompting
            if is_prompting:
                self.wfile.write(json.dumps({"status": "prompting"}).encode())
                return

            is_prompting = True
            _, _, _, hwnd = get_foreground_info() 

            rect = RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)

            is_time_up = session and current_time >= session["end_time"]

            if is_time_up:
                app_reference.after(0, lambda: show_time_up_prompt("youtube_focus", False, None, hwnd, rect_tuple, is_extension=True))
            else:
                if b_edu:
                    app_reference.after(0, lambda: show_prompt("youtube_focus", False, None, hwnd, rect_tuple, is_extension=True))
                else:
                    app_reference.after(0, lambda: show_reminder_prompt(cat, rect_tuple))

            self.wfile.write(json.dumps({"status": "prompting"}).encode())
        else:
            self.end_headers()

def start_local_server():
    server = HTTPServer(('127.0.0.1', 49321), MindfulServer)
    server.serve_forever()

def monitor_loop():
    global is_prompting
    while True:
        time.sleep(0.5) 
        if is_prompting: continue
        title, exe_name, pid, hwnd = get_foreground_info()
        if not title and not exe_name: continue

        with DB_LOCK:
            c = conn.cursor()
            tracked_items = c.execute("SELECT name, type FROM tracked_items").fetchall()

        matched_item = None
        is_app = False

        for name, item_type in tracked_items:
            if item_type == 'app' and name == exe_name:
                matched_item, is_app = name, True
                break
            elif item_type == 'website' and name in title:
                matched_item, is_app = name, False
                break

        if matched_item:
            current_time = time.time()
            if matched_item in grace_period_apps and current_time < grace_period_apps[matched_item]: continue
            session = active_sessions.get(matched_item)
            
            if session and current_time < session["end_time"]:
                with DB_LOCK:
                    c.execute("UPDATE usage_logs SET actual_seconds = actual_seconds + 1 WHERE id = ?", (session["log_id"],))
                    conn.commit()
            else:
                is_prompting = True
                is_time_up = session and current_time >= session["end_time"]
                
                # Fetch Window Coordinates BEFORE freezing to prevent deadlocks!
                rect = RECT()
                ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)
                
                # Now it is safe to suspend the app
                if is_app:
                    try: psutil.Process(pid).suspend() 
                    except: pass

                if not is_time_up:
                    ctypes.windll.user32.ShowWindow(hwnd, 0) 
                    app_reference.after(0, lambda: show_prompt(matched_item, is_app, pid, hwnd, rect_tuple))
                else:
                    app_reference.after(0, lambda: show_time_up_prompt(matched_item, is_app, pid, hwnd, rect_tuple))

def notification_loop():
    mins_passed = 0
    while True:
        time.sleep(60)
        mins_passed += 1
        with DB_LOCK:
            c = conn.cursor()
            water = c.execute("SELECT value FROM settings WHERE key='water'").fetchone()[0]
            qt = c.execute("SELECT value FROM settings WHERE key='quote_toggle'").fetchone()[0]
            qtext = c.execute("SELECT value FROM settings WHERE key='quote_text'").fetchone()[0]
        if mins_passed % 60 == 0:
            if water == '1': notification.notify(title="💧 Time to Hydrate", message="Drink water!", timeout=10)
            if qt == '1': notification.notify(title="Mindful Reminder", message=qtext, timeout=10)


# --- POPUP UI WINDOWS ---
def center_with_rect(win, width, height, rect_tuple):
    left, top, right, bottom = rect_tuple
    tw, th = right - left, bottom - top
    if tw <= 0 or th <= 0:
        x, y = int((win.winfo_screenwidth()/2) - (width/2)), int((win.winfo_screenheight()/2) - (height/2))
    else:
        x, y = left + (tw // 2) - (width // 2), top + (th // 2) - (height // 2)
    win.geometry(f"{width}x{height}+{x}+{y}")

def show_reminder_prompt(cat, rect_tuple):
    global is_prompting
    win = ctk.CTkToplevel(app_reference)
    win.title("Mindful Reminder")
    win.attributes('-topmost', True)
    center_with_rect(win, 520, 320, rect_tuple)
    win.configure(fg_color=("gray95", "gray10"))

    with DB_LOCK:
        quote = conn.cursor().execute("SELECT value FROM settings WHERE key='focus_quote'").fetchone()[0]

    ctk.CTkLabel(win, text="⚠️ Stay Focused", font=("Segoe UI", 24, "bold"), text_color="#ffcc00").pack(pady=(25,5))
    ctk.CTkLabel(win, text=f"Video Category: {cat}", font=("Segoe UI", 15, "bold")).pack()
    lbl = ctk.CTkLabel(win, text=f'"{quote}"', font=("Segoe UI", 14, "italic"), wraplength=450)
    lbl.pack(pady=15)
    slider_val_label = ctk.CTkLabel(win, text="Remind me again in 5 Minutes", font=("Segoe UI", 16, "bold"), text_color="#007aff")
    slider_val_label.pack(pady=(5, 5))

    def update_slider(value):
        if value >= 120: slider_val_label.configure(text="Don't remind me again (Infinity)")
        elif value < 1.0: slider_val_label.configure(text="Remind me in 30 Seconds")
        else: slider_val_label.configure(text=f"Remind me again in {int(value)} Minutes")

    slider = ctk.CTkSlider(win, from_=0.5, to=120, number_of_steps=239, width=400, command=update_slider)
    slider.set(5)
    slider.pack(pady=5)

    def submit(event=None):
        global is_prompting
        val = slider.get()
        allowed_secs = 999999 * 60 if val >= 120 else 30 if val < 1.0 else int(val) * 60
        active_sessions["youtube_focus"] = {"end_time": time.time() + allowed_secs, "log_id": -1}
        win.destroy()
        is_prompting = False

    def cancel(event=None):
        global is_prompting
        win.destroy()
        is_prompting = False

    win.protocol("WM_DELETE_WINDOW", cancel)
    ctk.CTkButton(win, text="Got it, Resume Video", command=submit, fg_color="#28a745", hover_color="#218838", font=("Segoe UI", 16, "bold"), width=200, height=40).pack(pady=20)
    win.bind('<Return>', submit)


def show_time_up_prompt(item_name, is_app, pid, hwnd, rect_tuple, is_extension=False):
    global is_prompting
    win = ctk.CTkToplevel(app_reference)
    win.title("Time Elapsed")
    win.attributes('-topmost', True)
    center_with_rect(win, 480, 230, rect_tuple)
    win.configure(fg_color=("gray95", "gray10"))
    
    ctk.CTkLabel(win, text="⚠️ Time is Up!", font=("Segoe UI", 24, "bold"), text_color="#dc3545").pack(pady=(20,10))
    
    display_name = "YouTube Video" if item_name == "youtube_focus" else item_name.title()
    ctk.CTkLabel(win, text=f"Your allowed time for {display_name} has elapsed.\nPlease close it and refocus.", font=("Segoe UI", 15)).pack(pady=5)
    
    def acknowledge():
        global is_prompting
        if is_app:
            try: psutil.Process(pid).resume()
            except: pass
        if not is_extension:
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            
        grace_period_apps[item_name] = time.time() + 15 
        if item_name in active_sessions:
            del active_sessions[item_name]
            
        win.destroy()
        is_prompting = False
        
    def more_time():
        win.destroy()
        if item_name in active_sessions:
            del active_sessions[item_name] # Clear old session
        show_prompt(item_name, is_app, pid, hwnd, rect_tuple, is_extension)

    btn_frame = ctk.CTkFrame(win, fg_color="transparent")
    btn_frame.pack(pady=20)
    ctk.CTkButton(btn_frame, text="I Will Close It", command=acknowledge, fg_color="#dc3545", hover_color="#c82333", width=150, font=("Segoe UI", 14, "bold")).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="I Need More Time", command=more_time, fg_color="#007aff", hover_color="#0056b3", width=150, font=("Segoe UI", 14, "bold")).pack(side="right", padx=10)
    win.protocol("WM_DELETE_WINDOW", acknowledge)


def show_prompt(item_name, is_app, pid, hwnd, rect_tuple, is_extension=False):
    global is_prompting
    prompt_win = ctk.CTkToplevel(app_reference)
    prompt_win.title("Mindfulness Check")
    prompt_win.attributes('-topmost', True)
    center_with_rect(prompt_win, 520, 380, rect_tuple)
    prompt_win.configure(fg_color=("gray95", "gray10"))
    breath_label = ctk.CTkLabel(prompt_win, text="", text_color="#007aff")
    breath_label.place(relx=0.5, rely=0.5, anchor="center")

    def cancel(event=None):
        global is_prompting
        if not is_extension:
            if is_app:
                try: psutil.Process(pid).kill()
                except: pass
            else: ctypes.windll.user32.ShowWindow(hwnd, 9)
        prompt_win.destroy()
        is_prompting = False
    prompt_win.protocol("WM_DELETE_WINDOW", cancel)

    def animate_text(text, start_size, end_size, duration, next_func):
        steps = 60 
        step_time, step_size = int(duration / steps), (end_size - start_size) / steps
        breath_label.configure(text=text)
        def step(current_step):
            if not prompt_win.winfo_exists(): return
            if current_step <= steps:
                breath_label.configure(font=("Segoe UI", int(start_size + (step_size * current_step)), "bold"))
                prompt_win.after(step_time, step, current_step + 1)
            else:
                if next_func: next_func()
        step(0)

    def phase_inhale(): animate_text("Breathe In...", 16, 50, 3300, phase_hold)
    def phase_hold(): animate_text("Hold...", 50, 50, 3300, phase_release)
    def phase_release(): animate_text("Breathe Out...", 50, 16, 3300, build_ui)

    def build_ui():
        if not prompt_win.winfo_exists(): return
        breath_label.destroy()
        
        display_name = "YouTube Video" if item_name == "youtube_focus" else item_name.title()
        ctk.CTkLabel(prompt_win, text=f"Why are you opening {display_name}?", font=("Segoe UI", 20, "bold")).pack(pady=(30, 10))
        reason_entry = ctk.CTkEntry(prompt_win, width=420, height=45, font=("Segoe UI", 15), corner_radius=10, placeholder_text="Type your exact purpose...")
        reason_entry.pack(pady=10)
        reason_entry.focus_force()
        slider_val_label = ctk.CTkLabel(prompt_win, text="5 Minutes", font=("Segoe UI", 16, "bold"), text_color="#007aff")
        slider_val_label.pack(pady=(15, 5))

        def update_slider(value):
            if value >= 120: slider_val_label.configure(text="Infinity (No Time Limit)")
            elif value < 1.0: slider_val_label.configure(text="30 Seconds")
            else: slider_val_label.configure(text=f"{int(value)} Minutes")

        slider = ctk.CTkSlider(prompt_win, from_=0.5, to=120, number_of_steps=239, width=380, command=update_slider, button_color="#007aff", progress_color="#007aff")
        slider.set(5)
        slider.pack(pady=10)

        def submit(event=None):
            global is_prompting
            reason = reason_entry.get().strip()
            if len(reason) < 3: return
            val = slider.get()
            db_mins, allowed_secs = (999999, 999999 * 60) if val >= 120 else (0.5, 30) if val < 1.0 else (int(val), int(val) * 60)
            
            with DB_LOCK:
                c = conn.cursor()
                c.execute("INSERT INTO usage_logs (item_name, timestamp, reason, requested_mins, actual_seconds) VALUES (?, ?, ?, ?, ?)", (item_name, datetime.now().isoformat(), reason, db_mins, 0))
                active_key = "youtube_focus" if is_extension else item_name
                active_sessions[active_key] = {"end_time": time.time() + allowed_secs, "log_id": c.lastrowid}
                conn.commit()
            
            if not is_extension:
                if is_app:
                    try: psutil.Process(pid).resume()
                    except: pass
                ctypes.windll.user32.ShowWindow(hwnd, 9) 
                
            prompt_win.destroy()
            is_prompting = False

        btn_frame = ctk.CTkFrame(prompt_win, fg_color="transparent")
        btn_frame.pack(pady=20)
        ctk.CTkButton(btn_frame, text="Unlock App", command=submit, corner_radius=8, fg_color="#28a745", hover_color="#218838", font=("Segoe UI", 15, "bold"), width=150, height=40).pack(side='left', padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=cancel, corner_radius=8, fg_color="#dc3545", hover_color="#c82333", font=("Segoe UI", 15, "bold"), width=150, height=40).pack(side='right', padx=10)
        prompt_win.bind('<Return>', submit)

    phase_inhale()

# --- DASHBOARD UI ---
class MindfulnessApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        global app_reference
        app_reference = self
        self.title("Mindful Consumption")
        self.geometry(f"800x680+{int(self.winfo_screenwidth()/2 - 400)}+{int(self.winfo_screenheight()/2 - 340)}")
        self.protocol('WM_DELETE_WINDOW', self.hide_window)
        self.withdraw()
        self.setup_tray()
        
        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.pack(padx=20, pady=(20, 0), fill="both", expand=True)
        
        self.tab_config = self.tabview.add("Blocklist")
        self.tab_focus = self.tabview.add("Focus & Web")
        self.tab_stats = self.tabview.add("Analytics")
        self.tab_settings = self.tabview.add("Settings")
        
        self.setup_config()
        self.setup_focus()
        self.setup_stats()
        self.setup_settings()
        self.tabview.configure(command=self.on_tab_change)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(side="bottom", pady=15)
        ctk.CTkLabel(footer, text="brewed by ", font=("Segoe UI", 13)).pack(side="left")
        link = ctk.CTkLabel(footer, text="sxjeel", font=("Segoe UI", 13, "bold"), text_color="#007aff", cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda e: webbrowser.open("https://sxjeel.vercel.app/"))
        ctk.CTkLabel(footer, text=" ☕", font=("Segoe UI", 13)).pack(side="left")

        threading.Thread(target=monitor_loop, daemon=True).start()
        threading.Thread(target=notification_loop, daemon=True).start()
        threading.Thread(target=start_local_server, daemon=True).start() 

    def setup_config(self):
        btn_frame = ctk.CTkFrame(self.tab_config, fg_color="transparent")
        btn_frame.pack(pady=(10, 15))
        ctk.CTkButton(btn_frame, text="+ Running App", command=self.pick_running_app, corner_radius=8).pack(side='left', padx=5)
        ctk.CTkButton(btn_frame, text="+ Select .exe", command=self.add_app, corner_radius=8).pack(side='left', padx=5)
        ctk.CTkButton(btn_frame, text="+ Add Website / Keyword", command=self.add_website, corner_radius=8).pack(side='left', padx=5)
        self.list_frame = ctk.CTkScrollableFrame(self.tab_config, corner_radius=10)
        self.list_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.refresh_listbox()
        
    def setup_focus(self):
        with DB_LOCK:
            c = conn.cursor()
            conf = {row[0]: row[1] for row in c.execute("SELECT key, value FROM settings").fetchall()}

        f = ctk.CTkScrollableFrame(self.tab_focus, corner_radius=10)
        f.pack(fill="both", expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(f, text="Browser Integration required for Focus Mode.", font=("Segoe UI", 16, "bold")).pack(pady=(10,0))
        
        btn_box = ctk.CTkFrame(f, fg_color="transparent")
        btn_box.pack(pady=10)
        ctk.CTkButton(btn_box, text="Auto-Install Browser Extension", fg_color="#28a745", hover_color="#218838", command=lambda: webbrowser.open("http://127.0.0.1:49321/mindful.user.js")).pack(side="left", padx=5)
        
        def copy_script():
            self.clipboard_clear()
            self.clipboard_append(USERSCRIPT_JS)
            messagebox.showinfo("Copied", "Script copied to clipboard!\n\nUpdate your existing script in Tampermonkey with this new version.")
            
        ctk.CTkButton(btn_box, text="Copy Script Manually", fg_color="#007aff", command=copy_script).pack(side="left", padx=5)
        ctk.CTkLabel(f, text="* Requires Tampermonkey installed in your browser.", text_color="gray", font=("Segoe UI", 12)).pack(pady=(0, 20))

        self.b_edu = ctk.StringVar(value=conf.get('block_non_edu', '0'))
        self.r_edu = ctk.StringVar(value=conf.get('remind_non_edu', '1'))
        self.b_sho = ctk.StringVar(value=conf.get('block_shorts', '0'))
        self.b_ree = ctk.StringVar(value=conf.get('block_reels', '1'))

        def toggle_block():
            if self.b_edu.get() == "1": self.r_edu.set("0")
            self.save_settings()

        def toggle_remind():
            if self.r_edu.get() == "1": self.b_edu.set("0")
            self.save_settings()

        ctk.CTkSwitch(f, text="Block Non-Education YouTube Videos", variable=self.b_edu, onvalue="1", offvalue="0", command=toggle_block, font=("Segoe UI", 14)).pack(anchor="w", padx=20, pady=10)
        ctk.CTkSwitch(f, text="Remind for Non-Education YouTube Videos", variable=self.r_edu, onvalue="1", offvalue="0", command=toggle_remind, font=("Segoe UI", 14)).pack(anchor="w", padx=20, pady=10)
        ctk.CTkSwitch(f, text="Hide YouTube Shorts Element", variable=self.b_sho, onvalue="1", offvalue="0", command=self.save_settings, font=("Segoe UI", 14)).pack(anchor="w", padx=20, pady=10)
        ctk.CTkSwitch(f, text="Hide Instagram Reels Element", variable=self.b_ree, onvalue="1", offvalue="0", command=self.save_settings, font=("Segoe UI", 14)).pack(anchor="w", padx=20, pady=10)
        
        ctk.CTkLabel(f, text="Non-Education Reminder Quote:", font=("Segoe UI", 14)).pack(anchor="w", padx=20, pady=(20, 5))
        self.f_quote = ctk.CTkEntry(f, width=500, font=("Segoe UI", 13))
        self.f_quote.insert(0, conf.get('focus_quote', ''))
        self.f_quote.pack(anchor="w", padx=20)
        self.f_quote.bind("<FocusOut>", lambda e: self.save_settings())

    def setup_stats(self):
        self.stats_text = ctk.CTkTextbox(self.tab_stats, font=("Consolas", 14), corner_radius=10)
        self.stats_text.pack(fill="both", expand=True, padx=20, pady=20)
        self.stats_text.configure(state='disabled')

    def setup_settings(self):
        with DB_LOCK:
            c = conn.cursor()
            conf = {row[0]: row[1] for row in c.execute("SELECT key, value FROM settings").fetchall()}

        frame = ctk.CTkFrame(self.tab_settings, corner_radius=10)
        frame.pack(fill="both", expand=True, padx=30, pady=30)
        
        ctk.CTkLabel(frame, text="Desktop Notifications", font=("Segoe UI", 20, "bold")).pack(pady=(20, 10))
        self.water_var = ctk.StringVar(value="on" if conf.get('water') == '1' else "off")
        ctk.CTkSwitch(frame, text="Hydration Reminder (Every 60 mins)", variable=self.water_var, onvalue="on", offvalue="off", command=self.save_settings, font=("Segoe UI", 15)).pack(pady=15)
        
        self.quote_var = ctk.StringVar(value="on" if conf.get('quote_toggle') == '1' else "off")
        ctk.CTkSwitch(frame, text="Mindful Quote Reminder (Every 60 mins)", variable=self.quote_var, onvalue="on", offvalue="off", command=self.save_settings, font=("Segoe UI", 15)).pack(pady=15)
        
        ctk.CTkLabel(frame, text="Your Custom Quote:", font=("Segoe UI", 14)).pack(pady=(15, 5))
        self.quote_entry = ctk.CTkEntry(frame, width=400, font=("Segoe UI", 14))
        self.quote_entry.insert(0, conf.get('quote_text', ''))
        self.quote_entry.pack()
        self.quote_entry.bind("<FocusOut>", lambda e: self.save_settings())

    def save_settings(self):
        with DB_LOCK:
            c = conn.cursor()
            c.execute("UPDATE settings SET value=? WHERE key='water'", ('1' if self.water_var.get() == "on" else '0',))
            c.execute("UPDATE settings SET value=? WHERE key='quote_toggle'", ('1' if self.quote_var.get() == "on" else '0',))
            c.execute("UPDATE settings SET value=? WHERE key='quote_text'", (self.quote_entry.get(),))
            if hasattr(self, 'b_edu'):
                c.execute("UPDATE settings SET value=? WHERE key='block_non_edu'", (self.b_edu.get(),))
                c.execute("UPDATE settings SET value=? WHERE key='remind_non_edu'", (self.r_edu.get(),))
                c.execute("UPDATE settings SET value=? WHERE key='block_shorts'", (self.b_sho.get(),))
                c.execute("UPDATE settings SET value=? WHERE key='block_reels'", (self.b_ree.get(),))
                c.execute("UPDATE settings SET value=? WHERE key='focus_quote'", (self.f_quote.get(),))
            conn.commit()

    def on_tab_change(self):
        if self.tabview.get() == "Analytics": self.refresh_stats()

    def pick_running_app(self):
        win = ctk.CTkToplevel(self)
        win.title("Select Running App")
        win.attributes('-topmost', True)
        win.geometry(f"500x400+{int(self.winfo_screenwidth()/2 - 250)}+{int(self.winfo_screenheight()/2 - 200)}")
        ctk.CTkLabel(win, text="Select a window to block:", font=("Segoe UI", 16, "bold")).pack(pady=10)
        listbox = ctk.CTkScrollableFrame(win, width=450, height=250)
        listbox.pack(pady=10)
        def on_select(title, exe):
            if "applicationframehost" in exe.lower(): self.insert_db_item(title.lower(), 'website')
            else: self.insert_db_item(exe.lower(), 'app')
            win.destroy()
        for title, exe in get_open_windows():
            btn = ctk.CTkButton(listbox, text=f"{title[:40]} ({exe})", anchor="w", fg_color="transparent", text_color=("black", "white"), command=lambda t=title, e=exe: on_select(t, e))
            btn.pack(fill="x", pady=2)

    def add_app(self):
        fp = filedialog.askopenfilename(filetypes=[("Executables", "*.exe")])
        if fp: self.insert_db_item(fp.split('/')[-1].lower(), 'app')

    def add_website(self):
        kw = simpledialog.askstring("Add Website", "Enter keyword:")
        if kw: self.insert_db_item(kw.lower().strip(), 'website')

    def insert_db_item(self, name, item_type):
        try:
            with DB_LOCK:
                conn.cursor().execute("INSERT INTO tracked_items (name, type) VALUES (?, ?)", (name, item_type))
                conn.commit()
            self.refresh_listbox()
        except: pass

    def remove_item(self, name):
        with DB_LOCK:
            conn.cursor().execute("DELETE FROM tracked_items WHERE name = ?", (name,))
            conn.commit()
        self.refresh_listbox()

    def refresh_listbox(self):
        for widget in self.list_frame.winfo_children(): widget.destroy()
        with DB_LOCK:
            items = conn.cursor().execute("SELECT name, type FROM tracked_items").fetchall()
        for name, item_type in items:
            row = ctk.CTkFrame(self.list_frame, fg_color=("gray90", "gray15"), corner_radius=8)
            row.pack(fill="x", pady=6, padx=5)
            icon = "📱 Store / App" if item_type == 'app' else "🌐 Web / Keyword"
            ctk.CTkLabel(row, text=f"{icon}  |  {name}", font=("Segoe UI", 15)).pack(side="left", padx=15, pady=12)
            ctk.CTkButton(row, text="Remove", width=80, fg_color="#dc3545", hover_color="#c82333", command=lambda n=name: self.remove_item(n)).pack(side="right", padx=15)

    def refresh_stats(self):
        self.stats_text.configure(state='normal')
        self.stats_text.delete(1.0, ctk.END)
        with DB_LOCK:
            recent = conn.cursor().execute("SELECT item_name, timestamp, requested_mins, actual_seconds, reason FROM usage_logs ORDER BY timestamp DESC LIMIT 20").fetchall()
            weekly = conn.cursor().execute("SELECT item_name, SUM(actual_seconds) FROM usage_logs WHERE timestamp >= date('now', '-7 days') GROUP BY item_name").fetchall()
        self.stats_text.insert(ctk.END, "=== AVERAGE DAILY USAGE (LAST 7 DAYS) ===\n")
        for name, total_secs in weekly:
            self.stats_text.insert(ctk.END, f" ⏱️ {name.title()}: {((total_secs / 7) / 60):.1f} mins/day\n")
        self.stats_text.insert(ctk.END, "\n=== RECENT SESSIONS ===\n")
        for log in recent:
            dt = datetime.fromisoformat(log[1]).strftime("%b %d, %I:%M %p")
            act_mins = log[3] / 60
            req = "∞" if log[2] >= 999999 else "30s" if log[2] == 0.5 else f"{int(log[2])}m"
            self.stats_text.insert(ctk.END, f"[{dt}] {log[0].title()}\n    Reason: {log[4]}\n    Time: Asked {req} | Used {act_mins:.1f}m\n\n")
        self.stats_text.configure(state='disabled')

    def setup_tray(self):
        image = Image.new('RGB', (64, 64), color=(0, 122, 255))
        d = ImageDraw.Draw(image)
        d.ellipse((16, 16, 48, 48), fill=(255, 255, 255))
        menu = pystray.Menu(pystray.MenuItem('Open Dashboard', self.show_window, default=True))
        self.tray = pystray.Icon("Mindful", image, "Mindful Consumption", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def hide_window(self): self.withdraw()
    def show_window(self, icon, item): self.after(0, self.deiconify)

if __name__ == "__main__":
    app = MindfulnessApp()
    app.mainloop()