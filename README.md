# Mindful Consumption ☕

**brewed by [sxjeel](https://sxjeel.vercel.app/)**

A zero-distraction, ultra-lightweight Windows firewall for your brain. It forces you to pause, breathe, and state your exact purpose before opening distracting apps or watching non-educational YouTube videos.

No fluff. No RAM hogging. Just pure accountability.

## 🔥 Features
* **The Breathing Firewall:** Forces a 10-second deep breathing animation before you can unlock a blocked app. 
* **Native YouTube & Instagram Integration:** Physically hides YouTube Shorts and Instagram Reels from the DOM. If you click a non-educational video, it pauses instantly and throws a native Windows prompt over your browser.
* **Smart Multi-Monitor Centering:** The prompt will lock perfectly onto the center of whatever window you just tried to open.
* **Actual Time Tracking:** Tracks active window usage. The timer only ticks down when you are *actually looking* at the app.
* **Stealth Mode:** Runs silently in your system tray at 0% CPU. No "Quit" button to prevent you from easily bypassing your own rules.
* **Hydration & Quote Reminders:** Native Windows desktop notifications every 60 minutes to keep you focused and hydrated.

## ⚡ 1-Click Installation
1. Download **[`Setup.bat`](https://raw.githubusercontent.com/sajee05/mindful-consumption/main/Setup.bat)** (Right-click -> Save link as...)
2. Double-click `Setup.bat`.
3. It will automatically download the latest version, install it to your user directory, add it to your Startup folder, and run it silently in the tray.

*(If you just want the portable `.exe` without the startup routine, grab it from the [Releases tab](https://github.com/sajee05/mindful-consumption/releases/latest)).*

## 🌐 Setting up Focus Mode (Browser Extension)
To make the YouTube and Instagram tracking work, the desktop app pairs seamlessly with a zero-dependency local userscript.
1. Install [Tampermonkey](https://www.tampermonkey.net/) in your browser.
2. Open the Mindful Consumption Dashboard (click the blue tray icon).
3. Go to the **Focus & Web** tab and click **Auto-Install Browser Extension**.

## 🛠️ Built With
* Python (CustomTkinter for the Apple-inspired UI)
* SQLite (Lightweight, local data logging)
* Windows ctypes (For deep OS-level window interception)
* Localhost Micro-server (Zero-latency browser communication without proxy overhead)

---
*Stay focused on your goals.*