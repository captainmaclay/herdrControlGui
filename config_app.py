#!/usr/bin/env python3
"""Herdr Config — Центр управления и маршрутизации AI-агентов (WSL2 / Windows).

Включает:
1. 📡 Маршруты & Herdr:
   - Состояние сервера Herdr и активных панелей агентов (agy, claude) в WSL2
   - Статус туннеля SOCKS5 для Google Gemini (порт 1081)
   - Статус прямого подключения для Anthropic Claude (белый IP)
2. 🛡️ SOCKS5 Прокси:
   - Список SOCKS5-прокси начиная от 1081 (+1 с каждым добавлением)
   - Кнопка ➕ для быстрого создания нового SOCKS5
   - Пагинация строго по 10 строк на страницу с переключением страниц
   - В каждой строке: статус (Online/Offline), внешний IP, определение страны (или undefined)
   - Индивидуальная и групповая проверка прокси
3. 🔮 Gemini OAuth:
   - Управление Google OAuth2 аккаунтами без API-ключей
   - Список сохраненных профилей (~/.gemini/profiles)
   - Переключение активного аккаунта в один клик
   - Онлайн-валидация токенов в Google API
   - Добавление новых Google-аккаунтов через браузерный OAuth
"""

from __future__ import annotations

import json
import os
import queue
from pathlib import Path
import socket
import sys
import extended_logger
extended_logger.init_extended_logging()

import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import traceback

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Защита от сбоев в pythonw и логирование ошибок запуска
if sys.stdout is None:
    try:
        sys.stdout = open(BASE_DIR / "stdout.log", "a", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(BASE_DIR / "stderr.log", "a", encoding="utf-8")
    except Exception:
        pass

import sync_manager
import proxy_manager
import gemini_manager
import settings_manager
import strategy_manager
import backup_manager
import claude_manager
import node_isolate_manager
import claude_oauth_manager
import integrations_manager
import token_vault_manager
import watchdog_manager
import i18n
from i18n import t

try:
    from PIL import Image, ImageDraw
    import pystray
    HAS_TRAY = True
except Exception as e:
    HAS_TRAY = False
    try:
        if sys.stderr:
            sys.stderr.write(f"pystray import failed: {e}\n")
            sys.stderr.flush()
    except Exception:
        pass

# ── Цветовая палитра Catppuccin Mocha ──────────────────────────────────────
C = {
    "bg":          "#181825",
    "card":        "#1e1e2e",
    "card_inner":  "#252538",
    "card_hover":  "#2b2b40",
    "fg":          "#cdd6f4",
    "subtext":     "#a6adc8",
    "accent_blue": "#89b4fa",
    "accent_peach":"#fab387",
    "accent_mauve":"#cba6f7",
    "accent_hover":"#f5c2e7",
    "green":       "#a6e3a1",
    "red":         "#f38ba8",
    "yellow":      "#f9e2af",
    "border":      "#313244",
    "input_bg":    "#181825",
    "tag_bg":      "#2a2a3e",
    "tag_fg":      "#b4befe",
    "table_header":"#212133",
    "table_row_alt":"#1c1c2b",
}

FONT_APP_TITLE = ("Segoe UI", 12, "bold")
FONT_TITLE = ("Segoe UI", 11, "bold")
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_MAIN = ("Segoe UI", 9)
FONT_SUB = ("Segoe UI", 8)
FONT_MONO = ("Consolas", 9)
FONT_MONO_BOLD = ("Consolas", 9, "bold")

SINGLE_INSTANCE_PORT = 38124


def make_tray_icon(color: str = "blue"):
    """Генерация яркой, контрастной иконки 'H' для системного трея."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    colors = {
        "blue":   ("#89b4fa", "#1e1e2e"),
        "green":  ("#a6e3a1", "#1e1e2e"),
        "red":    ("#f38ba8", "#1e1e2e"),
        "yellow": ("#f9e2af", "#1e1e2e"),
    }
    bg_col, fg_col = colors.get(color, colors["blue"])

    margin = 3
    d.rounded_rectangle([margin, margin, size - margin, size - margin], radius=16, fill=bg_col, outline="#ffffff", width=2)

    x1, x2 = 18, 26
    x3, x4 = 38, 46
    y_t, y_b = 16, 48
    ym_t, ym_b = 28, 36
    d.rounded_rectangle([x1, y_t, x2, y_b], radius=3, fill=fg_col)
    d.rounded_rectangle([x3, y_t, x4, y_b], radius=3, fill=fg_col)
    d.rectangle([x2, ym_t, x3, ym_b], fill=fg_col)
    return img



def show_toast(parent, title, message, duration=10000):
    import extended_logger
    extended_logger.write_ext_log("TOAST", f"Creating toast: {title}")
    extended_logger.write_ext_log("TOAST_MSG", message)
    """Показывает всплывашку в нижнем правом углу с кнопкой копирования."""
    import tkinter as tk
    toast = tk.Toplevel(parent)
    toast.overrideredirect(True)
    toast.attributes("-topmost", True)
    toast.configure(bg=C["card"], bd=2, relief="solid")
    
    # Calculate position (bottom right)
    window_x = parent.winfo_rootx() + parent.winfo_width() - 320
    window_y = parent.winfo_rooty() + parent.winfo_height() - 250
    scr_w = parent.winfo_screenwidth()
    scr_h = parent.winfo_screenheight()
    window_x = min(max(window_x, 0), scr_w - 340)
    window_y = min(max(window_y, 0), scr_h - 240)
    x_str = f"+{window_x}"
    y_str = f"+{window_y}"
    toast.geometry(f"340x240{x_str}{y_str}")
    extended_logger.write_ext_log("TOAST_GEOMETRY", f"340x240{x_str}{y_str}")

    lbl_title = tk.Label(toast, text=title, font=FONT_BOLD, bg=C["card"], fg=C["accent_peach"])
    lbl_title.pack(anchor="w", padx=10, pady=(10, 5))
    
    txt = tk.Text(toast, bg=C["card_inner"], fg=C["fg"], font=FONT_SUB, bd=0, height=7, wrap="word")
    txt.insert("1.0", message)
    txt.config(state="disabled")
    txt.pack(fill="both", expand=True, padx=10, pady=5)
    
    btn_frame = tk.Frame(toast, bg=C["card"])
    btn_frame.pack(fill="x", padx=10, pady=(0, 10))
    
    def on_copy():
        parent.clipboard_clear()
        parent.clipboard_append(message)
        parent.update()
        toast.destroy()
        
    tk.Button(
        btn_frame, text="📋 Копировать", font=FONT_SUB, bg=C["accent_blue"], fg="#000",
        command=on_copy, bd=0, cursor="hand2", padx=10
    ).pack(side="left")
    
    tk.Button(
        btn_frame, text="❌ Закрыть", font=FONT_SUB, bg=C["card_inner"], fg=C["fg"],
        command=toast.destroy, bd=0, cursor="hand2", padx=10
    ).pack(side="right")
    
    parent.after(duration, lambda: toast.destroy() if toast.winfo_exists() else None)


class HerdrConfigApp(tk.Tk):
    def __init__(self):
        super().__init__()
        i18n.init_language()
        self.title(t("app_title"))
        self.geometry("920x720")
        self.minsize(860, 640)
        self.configure(bg=C["bg"])

        icon_path = BASE_DIR / "app_icon.ico"
        if not icon_path.exists() and getattr(sys, "_MEIPASS", None):
            icon_path = Path(sys._MEIPASS) / "app_icon.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception:
                pass

        self.tray_icon = None
        self.protocol("WM_DELETE_WINDOW", self.on_close_button)

        # Состояние роутера и сохраняемые настройки
        self.is_checking_routes = False
        self.auto_refresh_enabled = tk.BooleanVar(value=settings_manager.get_setting("auto_refresh_routes", True))
        self.auto_refresh_enabled.trace_add(
            "write", lambda *_: settings_manager.set_setting("auto_refresh_routes", self.auto_refresh_enabled.get())
        )

        self.auto_proxy_failover_enabled = tk.BooleanVar(value=settings_manager.get_setting("auto_proxy_failover", True))
        self.auto_proxy_failover_enabled.trace_add(
            "write", lambda *_: settings_manager.set_setting("auto_proxy_failover", self.auto_proxy_failover_enabled.get())
        )

        # Язык интерфейса
        self.current_lang_var = tk.StringVar(value=i18n.get_lang())

        # Состояние Backup
        b_cfg = backup_manager.get_backup_config()
        self._suppress_backup_trace = False
        self.backup_password_var = tk.StringVar(value=backup_manager.load_backup_password())
        self.backup_dir_var = tk.StringVar(value=b_cfg.get("backup_dir", str(backup_manager.DEFAULT_BACKUP_DIR)))
        self.backup_interval_var = tk.IntVar(value=b_cfg.get("backup_interval_hours", 12))
        self.auto_backup_enabled_var = tk.BooleanVar(value=b_cfg.get("auto_backup_enabled", False))
        self.backup_pw_show = False

        self.backup_password_var.trace_add("write", self._on_backup_password_changed)
        self.backup_dir_var.trace_add("write", lambda *_: getattr(self, "_suppress_backup_trace", False) or backup_manager.update_backup_config("backup_dir", self.backup_dir_var.get()))
        self.backup_interval_var.trace_add("write", lambda *_: getattr(self, "_suppress_backup_trace", False) or backup_manager.update_backup_config("backup_interval_hours", self.backup_interval_var.get()))
        self.auto_backup_enabled_var.trace_add("write", lambda *_: getattr(self, "_suppress_backup_trace", False) or backup_manager.update_backup_config("auto_backup_enabled", self.auto_backup_enabled_var.get()))

        # Состояние прокси
        self.proxies: list[dict] = []
        self.current_proxy_page = 1
        self.is_checking_proxies = False

        # Состояние Gemini OAuth
        self.gemini_profiles: list[dict] = []
        self.is_checking_gemini = False

        # Состояние и логгер интеграций
        self._integ_task_running = False
        self._on_integration_log_cb = self._on_integration_log
        integrations_manager.logger.add_listener(self._on_integration_log_cb)

        self._build_ui()
        if HAS_TRAY:
            self._init_tray()

        # Enforcement: 99% времени в зашифрованом состоянии при старте.
        if backup_manager.load_backup_password():
            try:
                with token_vault_manager.auto_unlock_context():
                    pass # metadata is automatically updated inside context
            except Exception:
                pass
        token_vault_manager.ensure_locked()

        # Первоначальная загрузка данных
        self.load_proxies_data()
        self.load_gemini_profiles_data()

        # Быстрый мониторинг портов и Killswitch
        self._closing = False
        self._is_checking_fast_ports = False
        self._last_claude_accessible = None
        self.after(1000, self._schedule_fast_port_check)

        # Запуск проверки маршрутов
        self.after(300, self.refresh_routes_async)

        # Периодическое полное обновление маршрутов (каждые 15 секунд)
        self.node_isolate_thread = node_isolate_manager.NodeIsolateThread(
            lambda: (
                settings_manager.get_claude_proxy_host(),
                settings_manager.get_claude_proxy_port(),
                settings_manager.get_claude_node_isolate()
            )
        )
        self.node_isolate_thread.start()

        token_vault_manager.start_vault_watchdog()
        self._schedule_auto_refresh()

        # На передний план
        self.after(50, self.bring_to_front)

    def _build_ui(self):
        # ── 1. Верхняя панель (Header) ──────────────────────────
        header = tk.Frame(self, bg=C["card"], height=62, bd=0)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        h_in = tk.Frame(header, bg=C["card"])
        h_in.pack(fill="both", expand=True, padx=20, pady=10)

        title_box = tk.Frame(h_in, bg=C["card"])
        title_box.pack(side="left")

        tk.Label(
            title_box, text=t("app_name"), font=FONT_APP_TITLE,
            fg=C["accent_blue"], bg=C["card"]
        ).pack(side="left")

        badge = tk.Label(
            title_box, text=t("workspace_badge"), font=FONT_SUB,
            fg="#11111b", bg=C["green"], padx=6, pady=1
        )
        badge.pack(side="left", padx=(10, 0))

        self.quick_status = tk.Label(
            h_in, text=t("status_init"), font=FONT_MAIN,
            fg=C["yellow"], bg=C["card"]
        )
        self.quick_status.pack(side="right")

        # ── 2. Панель навигации (Tabs) ──────────────────────────
        nav_bar = tk.Frame(self, bg="#11111b", height=42, bd=0)
        nav_bar.pack(fill="x", side="top")
        nav_bar.pack_propagate(False)

        nav_in = tk.Frame(nav_bar, bg="#11111b")
        nav_in.pack(fill="both", expand=True, padx=16)

        self.tab_buttons = {}
        tabs = [
            ("routes", t("tab_routes")),
            ("proxy", t("tab_proxy")),
            ("gemini", t("tab_gemini")),
            ("claude_oauth", t("tab_claude_oauth")),
            ("strategy", t("tab_strategy")),
            ("backup", t("tab_backup")),
            ("integrations", t("tab_integrations")),
            ("localization", t("tab_localization")),
        ]

        for tab_id, tab_label in tabs:
            btn = tk.Button(
                nav_in,
                text=tab_label,
                font=FONT_BOLD,
                bg="#11111b",
                fg=C["subtext"],
                activebackground=C["card"],
                activeforeground=C["fg"],
                bd=0,
                padx=14,
                pady=8,
                cursor="hand2",
                command=lambda t=tab_id: self.switch_page(t),
            )
            btn.pack(side="left", padx=(0, 4), pady=4)
            self.tab_buttons[tab_id] = btn

        # ── 3. Контейнер страниц ────────────────────────────────
        self.pages_container = tk.Frame(self, bg=C["bg"])
        self.pages_container.pack(fill="both", expand=True, padx=18, pady=12)

        # Страницы
        self.page_routes = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_proxy = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_gemini = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_claude_oauth = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_strategy = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_backup = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_integrations = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_localization = tk.Frame(self.pages_container, bg=C["bg"])

        self._build_page_routes(self.page_routes)
        self._build_page_proxy(self.page_proxy)
        self._build_page_gemini(self.page_gemini)
        self._build_page_claude_oauth(self.page_claude_oauth)
        self._build_page_strategy(self.page_strategy)
        self._build_page_backup(self.page_backup)
        self._build_page_integrations(self.page_integrations)
        self._build_page_localization(self.page_localization)

        # Отображаем первую страницу по умолчанию
        self.active_tab = "routes"
        self.switch_page("routes")

        # ── 4. Нижняя панель (Footer) ───────────────────────────
        footer = tk.Frame(self, bg=C["card"], height=54, bd=0)
        footer.pack(fill="x", side="bottom")
        footer.pack_propagate(False)

        foot_in = tk.Frame(footer, bg=C["card"])
        foot_in.pack(fill="both", expand=True, padx=20, pady=8)

        self.refresh_btn = tk.Button(
            foot_in, text=t("btn_check_all"), font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", activebackground="#b4befe",
            bd=0, padx=16, pady=6, cursor="hand2",
            command=self.on_global_refresh
        )
        self.refresh_btn.pack(side="left")

        auto_cb = tk.Checkbutton(
            foot_in, text=t("lbl_auto_refresh"), variable=self.auto_refresh_enabled,
            font=FONT_MAIN, fg=C["fg"], bg=C["card"], activebackground=C["card"],
            activeforeground=C["fg"], selectcolor=C["card_inner"], bd=0
        )
        auto_cb.pack(side="left", padx=16)

        self.last_check_lbl = tk.Label(
            foot_in, text=t("lbl_last_check", time="--:--:--"), font=FONT_SUB,
            fg=C["subtext"], bg=C["card"]
        )
        self.last_check_lbl.pack(side="right")

    def switch_page(self, page_id: str):
        """Переключение между вкладками."""
        self.active_tab = page_id

        # Скрываем все страницы
        for p in (self.page_routes, self.page_proxy, self.page_gemini, self.page_claude_oauth, self.page_strategy, self.page_backup, self.page_integrations, self.page_localization):
            p.pack_forget()

        # Стили кнопок вкладок
        for t_id, btn in self.tab_buttons.items():
            if t_id == page_id:
                btn.config(bg=C["card"], fg=C["accent_blue"], relief="flat")
            else:
                btn.config(bg="#11111b", fg=C["subtext"], relief="flat")

        # Показываем выбранную страницу
        if page_id == "routes":
            self.page_routes.pack(fill="both", expand=True)
            self._update_claude_proxy_combo()
        elif page_id == "proxy":
            self.load_proxies_data()
            self.page_proxy.pack(fill="both", expand=True)
            self.render_proxy_table()
        elif page_id == "gemini":
            self.page_gemini.pack(fill="both", expand=True)
            self.render_gemini_page()
        elif page_id == "claude_oauth":
            self.page_claude_oauth.pack(fill="both", expand=True)
            self.render_claude_oauth_page()
            self.refresh_claude_profiles_async()
        elif page_id == "strategy":
            self.load_gemini_profiles_data()
            self.page_strategy.pack(fill="both", expand=True)
            self.render_strategy_page()
        elif page_id == "backup":
            self.page_backup.pack(fill="both", expand=True)
            self.render_backup_page()
        elif page_id == "integrations":
            self.page_integrations.pack(fill="both", expand=True)
            self.render_integrations_page()
        elif page_id == "localization":
            self.page_localization.pack(fill="both", expand=True)
            self.render_localization_page()

    # =========================================================================
    # СТРАНИЦА 1: МАРШРУТЫ & HERDR
    # =========================================================================

    def rebuild_ui(self):
        """Мгновенная перерисовка всего интерфейса на выбранном языке."""
        curr_tab = getattr(self, "active_tab", "localization")
        for child in self.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self.title(t("app_title"))
        self._build_ui()
        self.switch_page(curr_tab)

    def on_select_language(self, lang_code: str):
        """Обработчик выбора языка."""
        if lang_code == i18n.get_lang():
            return
        i18n.set_lang(lang_code)
        settings_manager.set_setting("language", lang_code)
        self.current_lang_var.set(lang_code)
        self.rebuild_ui()

    def render_localization_page(self):
        """Отрисовка страницы локализации."""
        pass

    def _build_page_localization(self, parent: tk.Frame):
        """Построение вкладки выбора языка (Localization)."""
        p_top = tk.Frame(parent, bg=C["bg"])
        p_top.pack(fill="x", pady=(0, 14))

        tk.Label(
            p_top, text=t("loc_title"), font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(anchor="w")

        tk.Label(
            p_top, text=t("loc_subtitle"), font=FONT_SUB,
            fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        card.configure(highlightbackground=C["border"], highlightthickness=1)
        card.pack(fill="x", pady=(0, 14))

        pad = tk.Frame(card, bg=C["card"])
        pad.pack(fill="both", expand=True, padx=20, pady=16)

        tk.Label(
            pad, text=t("loc_select_lang"), font=FONT_BOLD,
            fg=C["fg"], bg=C["card"]
        ).pack(anchor="w", pady=(0, 12))

        # Опция 1: English
        curr = i18n.get_lang()
        en_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid", cursor="hand2")
        en_frame.configure(highlightbackground=C["accent_blue"] if curr == "en" else C["border"], highlightthickness=1)
        en_frame.pack(fill="x", pady=(0, 10))

        en_in = tk.Frame(en_frame, bg=C["card_inner"])
        en_in.pack(fill="both", expand=True, padx=14, pady=12)

        en_head = tk.Frame(en_in, bg=C["card_inner"])
        en_head.pack(fill="x")

        rb_en = tk.Radiobutton(
            en_head, text=t("loc_lang_en"), variable=self.current_lang_var, value="en",
            font=FONT_BOLD, fg=C["fg"], bg=C["card_inner"],
            activebackground=C["card_inner"], activeforeground=C["accent_blue"],
            selectcolor=C["card"], bd=0, cursor="hand2",
            command=lambda: self.on_select_language("en")
        )
        rb_en.pack(side="left")

        if curr == "en":
            tk.Label(
                en_head, text=" ACTIVE / DEFAULT ", font=FONT_SUB,
                bg=C["green"], fg="#11111b", padx=6, pady=1
            ).pack(side="right")

        tk.Label(
            en_in, text=t("loc_lang_en_desc"), font=FONT_MAIN,
            fg=C["subtext"], bg=C["card_inner"], anchor="w", justify="left"
        ).pack(fill="x", padx=(28, 0), pady=(4, 0))

        # Опция 2: Russian
        ru_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid", cursor="hand2")
        ru_frame.configure(highlightbackground=C["accent_blue"] if curr == "ru" else C["border"], highlightthickness=1)
        ru_frame.pack(fill="x", pady=(0, 10))

        ru_in = tk.Frame(ru_frame, bg=C["card_inner"])
        ru_in.pack(fill="both", expand=True, padx=14, pady=12)

        ru_head = tk.Frame(ru_in, bg=C["card_inner"])
        ru_head.pack(fill="x")

        rb_ru = tk.Radiobutton(
            ru_head, text=t("loc_lang_ru"), variable=self.current_lang_var, value="ru",
            font=FONT_BOLD, fg=C["fg"], bg=C["card_inner"],
            activebackground=C["card_inner"], activeforeground=C["accent_blue"],
            selectcolor=C["card"], bd=0, cursor="hand2",
            command=lambda: self.on_select_language("ru")
        )
        rb_ru.pack(side="left")

        if curr == "ru":
            tk.Label(
                ru_head, text=f" {t('btn_active')} ", font=FONT_SUB,
                bg=C["green"], fg="#11111b", padx=6, pady=1
            ).pack(side="right")

        tk.Label(
            ru_in, text=t("loc_lang_ru_desc"), font=FONT_MAIN,
            fg=C["subtext"], bg=C["card_inner"], anchor="w", justify="left"
        ).pack(fill="x", padx=(28, 0), pady=(4, 0))

        # Информационный блок
        info_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        info_card.configure(highlightbackground=C["border"], highlightthickness=1)
        info_card.pack(fill="x")

        info_pad = tk.Frame(info_card, bg=C["card"])
        info_pad.pack(fill="both", expand=True, padx=20, pady=14)

        tk.Label(
            info_pad, text=t("loc_info_title"), font=FONT_BOLD,
            fg=C["accent_peach"], bg=C["card"]
        ).pack(anchor="w", pady=(0, 4))

        tk.Label(
            info_pad, text=t("loc_info_desc"), font=FONT_MAIN,
            fg=C["subtext"], bg=C["card"], anchor="w", justify="left"
        ).pack(anchor="w")

    def _build_page_routes(self, parent: tk.Frame):
        # ── Карточка 1: aiWatcher — сторож сервисов WSL2 ─────────
        self.aiwatcher_card = self._build_aiwatcher_card(parent)
        self.aiwatcher_card.pack(fill="x", pady=(0, 10))

        # ── Карточка 2: Google Gemini (SOCKS5) ──────────────────
        self.gemini_card = self._build_gemini_card(parent)
        self.gemini_card.pack(fill="x", pady=(0, 10))

        # ── Карточка 3: Anthropic Claude (Оригинальный IP) ──────
        self.claude_card = self._build_claude_card(parent)
        self.claude_card.pack(fill="x")

    # ───────────────────────── aiWatcher ─────────────────────────
    AIW_STATUS_STYLE = {
        watchdog_manager.ST_UNKNOWN:      ("aiw_st_unknown", "subtext"),
        watchdog_manager.ST_RUNNING:      ("aiw_st_running", "green"),
        watchdog_manager.ST_STARTING:     ("aiw_st_starting", "yellow"),
        watchdog_manager.ST_RESTARTED:    ("aiw_st_restarted", "accent_peach"),
        watchdog_manager.ST_START_FAILED: ("aiw_st_start_failed", "red"),
        watchdog_manager.ST_DISABLED:     ("aiw_st_disabled", "subtext"),
        watchdog_manager.ST_PAUSED:       ("aiw_st_paused", "subtext"),
        watchdog_manager.ST_EXTERNAL:     ("aiw_st_external", "yellow"),
        watchdog_manager.ST_UNHEALTHY:    ("aiw_st_unhealthy", "accent_peach"),
        watchdog_manager.ST_HEALING:      ("aiw_st_healing", "yellow"),
        watchdog_manager.ST_HEAL_FAILED:  ("aiw_st_heal_failed", "red"),
    }

    def _build_aiwatcher_card(self, parent: tk.Frame) -> tk.Frame:
        """Карточка встроенного aiWatcher: сторож AionUi и OmniRoute в WSL2 (бывший D:\\My files\\aiWatcher)."""
        card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        card.configure(highlightbackground=C["border"], highlightthickness=1)

        pad = tk.Frame(card, bg=C["card"])
        pad.pack(fill="both", expand=True, padx=16, pady=10)

        head = tk.Frame(pad, bg=C["card"])
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head, text=t("aiw_title"), font=FONT_TITLE, fg=C["fg"], bg=C["card"]).pack(side="left")
        self.aiw_badge = tk.Label(head, text=t("aiw_badge_off"), font=FONT_BOLD,
                                  bg=C["card_inner"], fg=C["subtext"], padx=8, pady=2)
        self.aiw_badge.pack(side="right")

        self.aiw_desc_lbl = tk.Label(pad, text=t("aiw_desc"), font=FONT_SUB, fg=C["subtext"],
                                     bg=C["card"], anchor="w", justify="left")
        self.aiw_desc_lbl.pack(fill="x", pady=(0, 6))

        # Панель управления
        ctl = tk.Frame(pad, bg=C["card"])
        ctl.pack(fill="x", pady=(0, 6))
        self.aiw_toggle_btn = tk.Button(
            ctl, text=t("aiw_btn_turn_on"), font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", activebackground=C["accent_hover"],
            activeforeground="#11111b", bd=0, padx=8, pady=2, cursor="hand2",
            command=self._aiw_toggle_watcher
        )
        self.aiw_toggle_btn.pack(side="left", padx=(0, 8))
        self.aiw_check_btn = tk.Button(
            ctl, text=t("aiw_btn_check_now"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["card_hover"],
            activeforeground=C["fg"], bd=0, padx=8, pady=2, cursor="hand2",
            command=self._aiw_check_now
        )
        self.aiw_check_btn.pack(side="left", padx=(0, 8))
        self.aiw_interval_lbl = tk.Label(ctl, text="", font=FONT_SUB, fg=C["subtext"], bg=C["card"])
        self.aiw_interval_lbl.pack(side="left")

        # Ручное обслуживание AionUi (скрипты aionUi_helper, под флагом обслуживания)
        ctl2 = tk.Frame(pad, bg=C["card"])
        ctl2.pack(fill="x", pady=(0, 6))
        self.aiw_restart_btn = tk.Button(
            ctl2, text=t("aiw_btn_restart_aionui"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["card_hover"],
            activeforeground=C["fg"], bd=0, padx=8, pady=2, cursor="hand2",
            command=self._aiw_restart_aionui
        )
        self.aiw_restart_btn.pack(side="left", padx=(0, 8))
        self.aiw_repair_btn = tk.Button(
            ctl2, text=t("aiw_btn_repair_db"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["card_hover"],
            activeforeground=C["fg"], bd=0, padx=8, pady=2, cursor="hand2",
            command=self._aiw_repair_db
        )
        self.aiw_repair_btn.pack(side="left", padx=(0, 8))

        # Предупреждение о внешнем (отдельном) aiWatcher — показывается только при обнаружении
        self.aiw_external_frame = tk.Frame(pad, bg=C["card"])
        self.aiw_external_lbl = tk.Label(self.aiw_external_frame, text=t("aiw_external_warning"),
                                         font=FONT_BOLD, fg=C["yellow"], bg=C["card"],
                                         anchor="w", justify="left", wraplength=560)
        self.aiw_external_lbl.pack(side="left", fill="x", expand=True)
        self.aiw_stop_external_btn = tk.Button(
            self.aiw_external_frame, text=t("aiw_btn_stop_external"), font=FONT_BOLD,
            bg=C["accent_peach"], fg="#11111b", activebackground=C["accent_hover"],
            activeforeground="#11111b", bd=0, padx=8, pady=2, cursor="hand2",
            command=self._aiw_stop_external
        )
        self.aiw_stop_external_btn.pack(side="right")

        # Сервисы
        self.aiw_apps_box = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        self.aiw_apps_box.configure(highlightbackground=C["border"], highlightthickness=1)
        self.aiw_apps_box.pack(fill="x", pady=(0, 6))
        apps_in = tk.Frame(self.aiw_apps_box, bg=C["card_inner"])
        apps_in.pack(fill="x", padx=12, pady=6)

        # События из фонового потока сторожа идут через очередь: Tk нельзя трогать не из главного потока
        self._aiw_queue: "queue.Queue[tuple[str, str, str]]" = queue.Queue()
        self.aiwatcher = watchdog_manager.WatchdogService(on_event=self._aiw_on_event_threadsafe)
        self.aiw_app_vars: dict[str, tk.BooleanVar] = {}
        self.aiw_app_checks: dict[str, tk.Checkbutton] = {}
        self.aiw_app_labels: dict[str, tk.Label] = {}
        for name, app in self.aiwatcher.config["apps"].items():
            row = tk.Frame(apps_in, bg=C["card_inner"])
            row.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=app.get("enabled", True))
            self.aiw_app_vars[name] = var
            label_key = f"aiw_app_{name}"
            label = t(label_key)
            cb = tk.Checkbutton(
                row, text=name if label == label_key else label,
                variable=var, font=FONT_BOLD, fg=C["fg"], bg=C["card_inner"],
                activebackground=C["card_inner"], activeforeground=C["fg"], selectcolor=C["input_bg"],
                cursor="hand2", command=lambda n=name: self._aiw_toggle_app(n)
            )
            cb.pack(side="left")
            self.aiw_app_checks[name] = cb
            lbl = tk.Label(row, text=t("aiw_st_unknown"), font=FONT_MONO_BOLD, fg=C["subtext"], bg=C["card_inner"])
            lbl.pack(side="right")
            self.aiw_app_labels[name] = lbl

        # Журнал
        tk.Label(pad, text=t("aiw_log_title"), font=FONT_BOLD, fg=C["subtext"], bg=C["card"],
                 anchor="w").pack(fill="x")
        log_box = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        log_box.configure(highlightbackground=C["border"], highlightthickness=1)
        log_box.pack(fill="x")
        self.aiw_log_txt = tk.Text(log_box, height=5, font=FONT_MONO, bg=C["card_inner"], fg=C["fg"],
                                   bd=0, wrap="word", state="disabled", highlightthickness=0)
        aiw_sb = tk.Scrollbar(log_box, command=self.aiw_log_txt.yview)
        self.aiw_log_txt.configure(yscrollcommand=aiw_sb.set)
        aiw_sb.pack(side="right", fill="y")
        self.aiw_log_txt.pack(side="left", fill="x", expand=True, padx=6, pady=4)

        self._aiw_refresh_controls()
        self._aiw_poll_id = self.after(200, self._aiw_poll_queue)
        return card

    # ── aiWatcher: события из фонового потока ──
    def _aiw_on_event_threadsafe(self, kind: str, app: str, text: str):
        """Вызывается из любого потока: только кладёт событие в очередь."""
        if getattr(self, "_closing", False):
            return
        self._aiw_queue.put((kind, app, text))

    def _aiw_drain_queue(self):
        """Главный поток: применяет накопившиеся события к интерфейсу."""
        while True:
            try:
                kind, app, text = self._aiw_queue.get_nowait()
            except queue.Empty:
                break
            self._aiw_on_event(kind, app, text)

    def _aiw_poll_queue(self):
        if getattr(self, "_closing", False):
            return
        try:
            self._aiw_drain_queue()
        finally:
            if not getattr(self, "_closing", False):
                self._aiw_poll_id = self.after(200, self._aiw_poll_queue)

    def _aiw_on_event(self, kind: str, app: str, text: str):
        if getattr(self, "_closing", False):
            return
        if kind == "status":
            self._aiw_set_app_status(app, text)
            self._aiw_refresh_controls()
        else:
            self._aiw_append_log(text)
            if kind in ("external", "action"):
                self._aiw_refresh_controls()

    def _aiw_append_log(self, line: str):
        try:
            self.aiw_log_txt.config(state="normal")
            self.aiw_log_txt.insert("end", line + "\n")
            if int(self.aiw_log_txt.index("end-1c").split(".")[0]) > 300:
                self.aiw_log_txt.delete("1.0", "51.0")
            self.aiw_log_txt.see("end")
            self.aiw_log_txt.config(state="disabled")
        except tk.TclError:
            pass

    def _aiw_set_app_status(self, app: str, status: str):
        lbl = self.aiw_app_labels.get(app)
        if not lbl:
            return
        key, color = self.AIW_STATUS_STYLE.get(status, ("aiw_st_unknown", "subtext"))
        lbl.config(text=t(key), fg=C[color])

    def _aiw_refresh_controls(self):
        svc = self.aiwatcher
        if svc.external:
            self.aiw_badge.config(text=t("aiw_badge_external"), bg=C["yellow"], fg="#11111b")
            if not self.aiw_external_frame.winfo_manager():
                self.aiw_external_frame.pack(fill="x", pady=(0, 6), before=self.aiw_apps_box)
        else:
            if self.aiw_external_frame.winfo_manager():
                self.aiw_external_frame.pack_forget()
            if svc.enabled:
                self.aiw_badge.config(text=t("aiw_badge_on"), bg=C["green"], fg="#11111b")
            else:
                self.aiw_badge.config(text=t("aiw_badge_off"), bg=C["red"], fg="#11111b")
        self.aiw_toggle_btn.config(text=t("aiw_btn_turn_off") if svc.enabled else t("aiw_btn_turn_on"))
        self.aiw_interval_lbl.config(text=t("aiw_interval", sec=svc.config.get("interval", 5)))
        for name, st in list(svc.statuses.items()):
            self._aiw_set_app_status(name, st)
        busy = "disabled" if svc.action_running else "normal"
        self.aiw_restart_btn.config(state=busy)
        self.aiw_repair_btn.config(state=busy)

    # ── aiWatcher: действия пользователя ──
    def _aiw_toggle_watcher(self):
        self.aiwatcher.set_enabled(not self.aiwatcher.enabled)
        self._aiw_refresh_controls()

    def _aiw_toggle_app(self, name: str):
        self.aiwatcher.set_app_enabled(name, self.aiw_app_vars[name].get())

    def _aiw_check_now(self):
        self.aiwatcher.wake(recheck_external=True)

    def _aiw_restart_aionui(self):
        """Чистый перезапуск AionUi (fix_aionui_login.py --fix): лечит экран входа «Connection failed»."""
        if self.aiwatcher.run_action(t("aiw_action_restart"), watchdog_manager.AIONUI_RESTART_CMD,
                                     watchdog_manager.HEAL_TIMEOUT_S):
            self._aiw_refresh_controls()

    def _aiw_repair_db(self):
        """Ремонт базы AionUi (repair_aionui_db.py): при «database disk image is malformed»."""
        if not messagebox.askyesno(t("aiw_title"), t("aiw_confirm_repair_db")):
            return
        if self.aiwatcher.run_action(t("aiw_action_repair"), watchdog_manager.AIONUI_REPAIR_DB_CMD,
                                     watchdog_manager.REPAIR_TIMEOUT_S):
            self._aiw_refresh_controls()

    def _aiw_stop_external(self):
        procs = list(self.aiwatcher.external)
        if not procs:
            return
        if not messagebox.askyesno(t("aiw_title"), t("aiw_confirm_stop_external", count=len(procs))):
            return

        def worker():
            killed = watchdog_manager.stop_external_watchers(procs)
            autorun_off = watchdog_manager.disable_legacy_autorun()
            self.aiwatcher.refresh_external(force=True)
            msg = t("aiw_external_stopped", count=killed)
            if autorun_off:
                msg += " " + t("aiw_autorun_disabled")
            self._aiw_on_event_threadsafe("external", "", msg)
            self.aiwatcher.wake()

        threading.Thread(target=worker, daemon=True).start()

    def _build_gemini_card(self, parent: tk.Frame) -> tk.Frame:
        card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        card.configure(highlightbackground=C["border"], highlightthickness=1)

        pad = tk.Frame(card, bg=C["card"])
        pad.pack(fill="both", expand=True, padx=16, pady=10)

        head = tk.Frame(pad, bg=C["card"])
        head.pack(fill="x", pady=(0, 6))

        tk.Label(head, text=t("gemini_route_title"), font=FONT_TITLE, fg=C["accent_blue"], bg=C["card"]).pack(side="left")

        pill = tk.Label(
            head, text=t("pill_socks5"), font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", padx=8, pady=2
        )
        pill.pack(side="right")

        self.gemini_status_lbl = tk.Label(
            pad, text=t("gemini_checking"), font=FONT_MAIN,
            fg=C["yellow"], bg=C["card"], anchor="w"
        )
        self.gemini_status_lbl.pack(fill="x", pady=(0, 4))

        details_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        details_frame.configure(highlightbackground=C["border"], highlightthickness=1)
        details_frame.pack(fill="x", pady=(2, 0))

        d_in = tk.Frame(details_frame, bg=C["card_inner"])
        d_in.pack(fill="x", padx=12, pady=6)

        r1 = tk.Frame(d_in, bg=C["card_inner"])
        r1.pack(fill="x", pady=1)
        tk.Label(r1, text="Маршрут:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        self.gemini_route_lbl = tk.Label(r1, text=f"socks5h://127.0.0.1:1081 ({t('pill_socks5')})", font=FONT_MONO, fg=C["fg"], bg=C["card_inner"])
        self.gemini_route_lbl.pack(side="left")

        r2 = tk.Frame(d_in, bg=C["card_inner"])
        r2.pack(fill="x", pady=1)
        tk.Label(r2, text="Google API:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        self.gemini_ping_lbl = tk.Label(r2, text=t("waiting_check"), font=FONT_MONO, fg=C["subtext"], bg=C["card_inner"])
        self.gemini_ping_lbl.pack(side="left")

        r3 = tk.Frame(d_in, bg=C["card_inner"])
        r3.pack(fill="x", pady=1)
        tk.Label(r3, text="Модели:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        tk.Label(
            r3, text="gemini-3.8-flash • gemini-3.7-flash • gemini-3.1-pro • agy CLI",
            font=FONT_MAIN, fg=C["tag_fg"], bg=C["card_inner"]
        ).pack(side="left")

        return card

    def _build_claude_card(self, parent: tk.Frame) -> tk.Frame:
        card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        card.configure(highlightbackground=C["border"], highlightthickness=1)

        pad = tk.Frame(card, bg=C["card"])
        pad.pack(fill="both", expand=True, padx=16, pady=10)

        head = tk.Frame(pad, bg=C["card"])
        head.pack(fill="x", pady=(0, 6))

        tk.Label(head, text=t("claude_route_title"), font=FONT_TITLE, fg=C["accent_peach"], bg=C["card"]).pack(side="left")

        self.claude_pill = tk.Label(
            head, text=t("pill_claude_proxy"), font=FONT_BOLD,
            bg=C["accent_peach"], fg="#11111b", padx=8, pady=2
        )
        self.claude_pill.pack(side="right")

        self.claude_status_lbl = tk.Label(
            pad, text=t("claude_checking"), font=FONT_MAIN,
            fg=C["yellow"], bg=C["card"], anchor="w"
        )
        self.claude_status_lbl.pack(fill="x", pady=(0, 6))

        # Настройки прокси Claude и Killswitch
        cfg_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        cfg_frame.configure(highlightbackground=C["border"], highlightthickness=1)
        cfg_frame.pack(fill="x", pady=(0, 6))

        cfg_in = tk.Frame(cfg_frame, bg=C["card_inner"])
        cfg_in.pack(fill="x", padx=12, pady=6)

        # Строка 1: Host, Port, Выбор из списка, Кнопка Сохранить
        row_cfg = tk.Frame(cfg_in, bg=C["card_inner"])
        row_cfg.pack(fill="x", pady=2)

        tk.Label(row_cfg, text=t("lbl_claude_proxy_host"), font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"]).pack(side="left")
        self.claude_host_var = tk.StringVar(value=settings_manager.get_claude_proxy_host())
        self.claude_host_entry = tk.Entry(
            row_cfg, textvariable=self.claude_host_var, font=FONT_MONO,
            bg=C["input_bg"], fg=C["fg"], insertbackground=C["fg"],
            bd=1, relief="solid", highlightthickness=1, highlightbackground=C["border"], width=13
        )
        self.claude_host_entry.pack(side="left", padx=(4, 12))

        tk.Label(row_cfg, text=t("lbl_claude_proxy_port"), font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"]).pack(side="left")
        self.claude_port_var = tk.StringVar(value=str(settings_manager.get_claude_proxy_port()))
        self.claude_port_entry = tk.Entry(
            row_cfg, textvariable=self.claude_port_var, font=FONT_MONO,
            bg=C["input_bg"], fg=C["fg"], insertbackground=C["fg"],
            bd=1, relief="solid", highlightthickness=1, highlightbackground=C["border"], width=6
        )
        self.claude_port_entry.pack(side="left", padx=(4, 12))

        # Выбор из списка существующих прокси
        self.claude_proxy_combo = ttk.Combobox(row_cfg, width=30, state="readonly")
        self._update_claude_proxy_combo()
        self.claude_proxy_combo.pack(side="left", padx=(0, 10))
        self.claude_proxy_combo.bind("<<ComboboxSelected>>", self._on_claude_combo_selected)

        # Кнопка применить
        self.claude_save_btn = tk.Button(
            row_cfg, text=t("btn_claude_save"), font=FONT_BOLD,
            bg=C["accent_peach"], fg="#11111b", activebackground=C["accent_hover"],
            activeforeground="#11111b", bd=0, padx=8, pady=2, cursor="hand2",
            command=self._save_claude_settings_action
        )
        self.claude_save_btn.pack(side="left", padx=(0, 12))

        # Чекбокс Killswitch
        self.claude_killswitch_var = tk.BooleanVar(value=settings_manager.get_claude_killswitch())
        self.claude_ks_cb = tk.Checkbutton(
            cfg_in, text=t("lbl_claude_killswitch"),
            variable=self.claude_killswitch_var,
            font=FONT_BOLD, fg=C["red"] if self.claude_killswitch_var.get() else C["subtext"],
            bg=C["card_inner"], activebackground=C["card_inner"],
            activeforeground=C["fg"], selectcolor=C["input_bg"],
            cursor="hand2", command=self._on_claude_killswitch_toggle
        )
        self.claude_ks_cb.pack(anchor="w", pady=(4, 2))

        # Чекбокс Изоляции Node.js
        self.claude_node_isolate_var = tk.BooleanVar(value=settings_manager.get_claude_node_isolate())
        self.claude_ni_cb = tk.Checkbutton(
            cfg_in, text="Isolate - Node.js and claude.exe",
            variable=self.claude_node_isolate_var,
            font=FONT_BOLD, fg=C["accent_peach"] if self.claude_node_isolate_var.get() else C["subtext"],
            bg=C["card_inner"], activebackground=C["card_inner"],
            activeforeground=C["fg"], selectcolor=C["input_bg"],
            cursor="hand2", command=self._on_claude_node_isolate_toggle
        )
        self.claude_ni_cb.pack(anchor="w", pady=(0, 10))

        # Детали маршрута
        details_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        details_frame.configure(highlightbackground=C["border"], highlightthickness=1)
        details_frame.pack(fill="x", pady=(2, 0))

        d_in = tk.Frame(details_frame, bg=C["card_inner"])
        d_in.pack(fill="x", padx=12, pady=6)

        r1 = tk.Frame(d_in, bg=C["card_inner"])
        r1.pack(fill="x", pady=1)
        tk.Label(r1, text="Маршрут:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        self.claude_route_lbl = tk.Label(
            r1,
            text=f"http://{settings_manager.get_claude_proxy_host()}:{10000 + settings_manager.get_claude_proxy_port()}",
            font=FONT_MAIN, fg=C["fg"], bg=C["card_inner"]
        )
        self.claude_route_lbl.pack(side="left")

        r2 = tk.Frame(d_in, bg=C["card_inner"])
        r2.pack(fill="x", pady=1)
        tk.Label(r2, text=t("lbl_direct_ip"), font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        self.claude_ip_lbl = tk.Label(r2, text=t("resolving_ip"), font=FONT_MONO_BOLD, fg=C["accent_peach"], bg=C["card_inner"])
        self.claude_ip_lbl.pack(side="left")

        r3 = tk.Frame(d_in, bg=C["card_inner"])
        r3.pack(fill="x", pady=1)
        tk.Label(r3, text="Anthropic API:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        self.claude_ping_lbl = tk.Label(r3, text=t("waiting_check"), font=FONT_MONO, fg=C["subtext"], bg=C["card_inner"])
        self.claude_ping_lbl.pack(side="left")

        r4 = tk.Frame(d_in, bg=C["card_inner"])
        r4.pack(fill="x", pady=1)
        tk.Label(r4, text="Модели:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        tk.Label(
            r4, text="claude-3-7-sonnet • claude-3-5-sonnet • claude-3-5-haiku • claude-code",
            font=FONT_MAIN, fg=C["tag_fg"], bg=C["card_inner"]
        ).pack(side="left")

        return card

    def _update_claude_proxy_combo(self, refresh_status: bool = False):
        try:
            proxies = proxy_manager.load_proxies(refresh_status=refresh_status)
            items = []
            cur_p = str(self.claude_port_var.get()).strip() if hasattr(self, "claude_port_var") else ""
            cur_h = str(self.claude_host_var.get()).strip() if hasattr(self, "claude_host_var") else "127.0.0.1"
            matched_val = None

            for p in proxies:
                port = p.get("port")
                host = p.get("host", "127.0.0.1")
                st = p.get("status", "unknown")
                lbl = p.get("label", "")
                if lbl:
                    item_text = f"{host}:{port} - {lbl} ({st})"
                else:
                    item_text = f"{host}:{port} ({st})"
                items.append(item_text)

                if str(port) == cur_p:
                    matched_val = item_text

            if hasattr(self, "claude_proxy_combo"):
                self.claude_proxy_combo["values"] = items
                if matched_val:
                    self.claude_proxy_combo.set(matched_val)
                elif items and not self.claude_proxy_combo.get():
                    self.claude_proxy_combo.set(items[0])
        except Exception:
            pass

    def _on_claude_combo_selected(self, event=None):
        val = self.claude_proxy_combo.get()
        if val and ":" in val:
            token = val.split(" ")[0].strip()
            parts = token.split(":")
            if len(parts) == 2:
                self.claude_host_var.set(parts[0])
                self.claude_port_var.set(parts[1])
                self._save_claude_settings_action()

    def _on_claude_killswitch_toggle(self):
        self._save_claude_settings_action()

    def _on_claude_node_isolate_toggle(self):
        self._save_claude_settings_action()
        import node_isolate_manager
        import settings_manager
        h = settings_manager.get_claude_proxy_host()
        p = settings_manager.get_claude_proxy_port()
        is_on = settings_manager.get_claude_node_isolate()
        # Force immediate apply so to avoid 20 sec waiting rule
        try:
            node_isolate_manager.enforce_isolation(h, p, is_on)
        except Exception as e:
            import traceback, logging
            logging.error(f"SILENT CRASH IN ENFORCE_ISOLATION: {e}\n{traceback.format_exc()}")

    def _save_claude_settings_action(self):
        h = self.claude_host_var.get().strip()
        try:
            p = int(self.claude_port_var.get())
        except ValueError:
            return
        ks = bool(self.claude_killswitch_var.get())
        ni = getattr(self, "claude_node_isolate_var", None)
        ni_val = bool(ni.get()) if ni else False
        self._last_claude_accessible = None
        settings_manager.set_claude_proxy_settings(h, p, ks, ni_val)
        claude_manager.save_claude_config(h, p, ks)
        proxy_manager.set_proxy_claude_flag(p, True)
        self.proxies = proxy_manager.load_proxies()
        self.claude_route_lbl.config(text=f"http://{h}:{10000 + p} (SOCKS5 :{p})")
        if hasattr(self, "claude_ks_cb"):
            self.claude_ks_cb.config(fg=C["red"] if ks else C["subtext"])
        if hasattr(self, "claude_ni_cb"):
            self.claude_ni_cb.config(fg=C["accent_peach"] if ni_val else C["subtext"])
        self.refresh_routes_async()


    # =========================================================================
    # СТРАНИЦА 2: SOCKS5 ПРОКСИ
    # =========================================================================
    def _build_page_proxy(self, parent: tk.Frame):
        # Верхняя панель управления прокси
        p_top = tk.Frame(parent, bg=C["bg"])
        p_top.pack(fill="x", pady=(0, 10))

        title_frame = tk.Frame(p_top, bg=C["bg"])
        title_frame.pack(side="left")

        tk.Label(
            title_frame, text=t("proxy_title"), font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(anchor="w")

        self.proxy_sub_lbl = tk.Label(
            title_frame,
            text=t("proxy_subtitle"),
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        )
        self.proxy_sub_lbl.pack(anchor="w")

        btn_box = tk.Frame(p_top, bg=C["bg"])
        btn_box.pack(side="right")

        self.btn_check_proxies = tk.Button(
            btn_box, text=t("btn_check_all_proxies"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.check_current_proxy_page_async
        )
        self.btn_check_proxies.pack(side="left", padx=(0, 8))

        self.btn_add_proxy = tk.Button(
            btn_box, text=t("btn_add_port"), font=FONT_BOLD,
            bg=C["green"], fg="#11111b", activebackground="#a6e3a1",
            bd=0, padx=14, pady=6, cursor="hand2",
            command=self.on_add_proxy_click
        )
        self.btn_add_proxy.pack(side="left")

        # ── Настройка автоподбора Proxy при failure ──────────────
        fo_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        fo_card.configure(highlightbackground=C["border"], highlightthickness=1)
        fo_card.pack(fill="x", pady=(0, 10))

        fo_inner = tk.Frame(fo_card, bg=C["card"])
        fo_inner.pack(fill="x", padx=14, pady=8)

        fo_cb = tk.Checkbutton(
            fo_inner,
            text=t("guard_chk_failover"),
            variable=self.auto_proxy_failover_enabled,
            font=FONT_BOLD, fg=C["accent_peach"], bg=C["card"],
            activebackground=C["card"], activeforeground=C["accent_peach"],
            selectcolor=C["card_inner"], bd=0, cursor="hand2"
        )
        fo_cb.pack(side="left")

        tk.Label(
            fo_inner,
            text="• При сбое туннеля Gemini авто-переключает на рабочий SOCKS5 из той же страны",
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        ).pack(side="left", padx=(10, 0))

        # Таблица прокси
        self.proxy_table_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        self.proxy_table_card.configure(highlightbackground=C["border"], highlightthickness=1)
        self.proxy_table_card.pack(fill="both", expand=True)

        # Заголовки таблицы
        tbl_head = tk.Frame(self.proxy_table_card, bg=C["table_header"], height=36)
        tbl_head.pack(fill="x")
        tbl_head.pack_propagate(False)

        headers = [
            (t("col_status"), 11),
            (t("col_port"), 20),
            (t("col_ip"), 17),
            (t("col_country"), 18),
            (t("col_latency"), 10),
            (t("col_claude"), 10),
            (t("col_actions"), 14),
        ]
        for h_text, h_width in headers:
            align = "center" if h_text in (t("col_status"), t("col_latency"), t("col_claude"), t("col_actions"), "СТАТУС", t("col_claude"), "ДЕЙСТВИЯ", "ОТКЛИК") else "w"
            tk.Label(
                tbl_head, text=h_text, font=FONT_BOLD,
                fg=C["subtext"], bg=C["table_header"], width=h_width, anchor=align
            ).pack(side="left", padx=4, pady=8)

        # Контейнер для строк таблицы
        self.proxy_rows_container = tk.Frame(self.proxy_table_card, bg=C["card"])
        self.proxy_rows_container.pack(fill="both", expand=True, padx=4, pady=4)

        # Панель пагинации
        self.pag_bar = tk.Frame(self.proxy_table_card, bg=C["card_inner"], height=44)
        self.pag_bar.pack(fill="x", side="bottom")
        self.pag_bar.pack_propagate(False)

        p_inner = tk.Frame(self.pag_bar, bg=C["card_inner"])
        p_inner.pack(fill="both", expand=True, padx=14)

        self.btn_prev_page = tk.Button(
            p_inner, text=t("btn_prev_page"), font=FONT_BOLD,
            bg=C["card"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=4, cursor="hand2",
            command=self.on_prev_proxy_page
        )
        self.btn_prev_page.pack(side="left", pady=8)

        self.pag_info_lbl = tk.Label(
            p_inner, text="Страница 1 из 1 (Всего: 1)", font=FONT_MAIN,
            fg=C["subtext"], bg=C["card_inner"]
        )
        self.pag_info_lbl.pack(side="left", padx=16)

        self.btn_next_page = tk.Button(
            p_inner, text=t("btn_next_page"), font=FONT_BOLD,
            bg=C["card"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=4, cursor="hand2",
            command=self.on_next_proxy_page
        )
        self.btn_next_page.pack(side="left", pady=8)

    def load_proxies_data(self):
        """Загрузка списка прокси из файла с проверкой реального статуса локальных портов."""
        self.proxies = proxy_manager.load_proxies(refresh_status=True)

    def render_proxy_table(self):
        """Отрисовка 10 строк текущей страницы."""
        for widget in self.proxy_rows_container.winfo_children():
            widget.destroy()

        page_items, total_pages = proxy_manager.get_paginated_proxies(
            self.proxies, self.current_proxy_page, proxy_manager.PAGE_SIZE
        )

        # Гарантируем актуальный статус локальных портов перед отрисовкой таблицы
        if proxy_manager.refresh_local_ports_status(page_items):
            proxy_manager.save_proxies(self.proxies)

        # Обновляем текст пагинации
        total_count = len(self.proxies)
        self.pag_info_lbl.config(
            text=f"Страница {self.current_proxy_page} из {total_pages} (Всего: {total_count})"
        )
        self.btn_prev_page.config(state="normal" if self.current_proxy_page > 1 else "disabled")
        self.btn_next_page.config(state="normal" if self.current_proxy_page < total_pages else "disabled")

        if not page_items:
            empty_lbl = tk.Label(
                self.proxy_rows_container,
                text="Список прокси пуст. Нажмите '➕ Добавить SOCKS5', чтобы добавить порт 1081.",
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"], pady=30
            )
            empty_lbl.pack(fill="x")
            return

        for idx, item in enumerate(page_items):
            bg_color = C["card"] if idx % 2 == 0 else C["table_row_alt"]
            row = tk.Frame(self.proxy_rows_container, bg=bg_color, height=38)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            status = item.get("status", "unknown")
            if status == "online":
                st_text = "🟢 " + t("status_online")
                st_color = C["green"]
            elif status == "offline":
                st_text = "🔴 " + t("status_offline")
                st_color = C["red"]
            else:
                st_text = "⚪ " + t("status_unknown")
                st_color = C["subtext"]

            # 1. Статус
            tk.Label(
                row, text=st_text, font=FONT_BOLD,
                fg=st_color, bg=bg_color, width=11, anchor="center"
            ).pack(side="left", padx=4)

            # 2. Хост:Порт
            host_port = f"{item.get('host', '127.0.0.1')}:{item.get('port', 1081)}"
            tk.Label(
                row, text=host_port, font=FONT_MONO_BOLD,
                fg=C["fg"], bg=bg_color, width=20, anchor="w"
            ).pack(side="left", padx=4)

            # 3. IP
            ip_str = item.get("ip") or "-"
            tk.Label(
                row, text=ip_str, font=FONT_MONO,
                fg=C["accent_peach"] if ip_str != "-" else C["subtext"],
                bg=bg_color, width=17, anchor="w"
            ).pack(side="left", padx=4)

            # 4. Страна (при невозможности определить — строго undefined!)
            country_str = item.get("country") or "undefined"
            c_color = C["tag_fg"] if country_str != "undefined" else C["yellow"]
            tk.Label(
                row, text=country_str, font=FONT_MAIN,
                fg=c_color, bg=bg_color, width=18, anchor="w"
            ).pack(side="left", padx=4)

            # 5. Пинг
            lat = item.get("latency_ms")
            lat_str = f"{lat} ms" if lat is not None else "-"
            tk.Label(
                row, text=lat_str, font=FONT_MONO,
                fg=C["subtext"], bg=bg_color, width=10, anchor="center"
            ).pack(side="left", padx=4)

            # 6. Флаг Claude
            claude_frame = tk.Frame(row, bg=bg_color, width=10)
            claude_frame.pack(side="left", padx=4)
            is_claude = bool(item.get("claude", False))
            claude_var = tk.BooleanVar(value=is_claude)
            cb_claude = tk.Checkbutton(
                claude_frame, text="Claude",
                variable=claude_var,
                font=FONT_BOLD,
                fg=C["accent_peach"] if is_claude else C["subtext"],
                bg=bg_color, activebackground=bg_color,
                activeforeground=C["accent_peach"],
                selectcolor=C["card_inner"], bd=0, cursor="hand2",
                command=lambda p=item, v=claude_var: self.on_toggle_proxy_claude(p, v)
            )
            cb_claude.pack(anchor="center")

            # 7. Действия
            actions = tk.Frame(row, bg=bg_color, width=14)
            actions.pack(side="left", padx=4)

            btn_check = tk.Button(
                actions, text="🔄", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
                bd=0, padx=6, pady=2, cursor="hand2",
                command=lambda p=item: self.check_single_proxy_async(p)
            )
            btn_check.pack(side="left", padx=(0, 4))

            btn_del = tk.Button(
                actions, text="🗑️", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["red"], activebackground=C["border"],
                bd=0, padx=6, pady=2, cursor="hand2",
                command=lambda pid=item.get("id"): self.on_delete_proxy(pid)
            )
            btn_del.pack(side="left")

    def on_toggle_proxy_claude(self, proxy_item: dict, var: tk.BooleanVar):
        """Переключение флага Claude для прокси."""
        val = bool(var.get())
        proxy_item["claude"] = val
        port = proxy_item.get("port")
        for p in self.proxies:
            if p.get("id") == proxy_item.get("id") or p.get("port") == port:
                p["claude"] = val
                break
        proxy_manager.save_proxies(self.proxies)

        # Если включили флаг Claude для прокси, обновляем настройки Claude на карточке Routes
        if val:
            self._last_claude_accessible = None
            h = proxy_item.get("host", "127.0.0.1")
            p_num = proxy_item.get("port", 1015)
            ks = settings_manager.get_claude_killswitch()
            claude_manager.save_claude_config(h, p_num, ks)
            if hasattr(self, "claude_host_var"):
                self.claude_host_var.set(h)
            if hasattr(self, "claude_port_var"):
                self.claude_port_var.set(str(p_num))
            self._update_claude_proxy_combo()
            self.refresh_routes_async()
            # Прокси с флагом Claude строго не должны использоваться в Gemini:
            gemini_manager.reassign_profiles_using_claude_proxies()
            if hasattr(self, "load_gemini_profiles_data"):
                self.load_gemini_profiles_data()
            if hasattr(self, "gemini_cards_frame") and self.gemini_cards_frame.winfo_exists():
                self.render_gemini_page()

        self.render_proxy_table()

    def on_add_proxy_click(self):
        """Добавляет новый прокси с авто-инкрементом порта."""
        new_p = proxy_manager.add_proxy()
        self.load_proxies_data()

        # Если добавленный прокси попадает на новую страницу, переключаемся на нее
        total_pages = (len(self.proxies) + proxy_manager.PAGE_SIZE - 1) // proxy_manager.PAGE_SIZE
        self.current_proxy_page = total_pages
        self.render_proxy_table()

        # Сразу запускаем проверку для нового прокси
        self.check_single_proxy_async(new_p)

    def on_delete_proxy(self, proxy_id: int):
        """Удаление прокси."""
        target = next((p for p in self.proxies if p.get("id") == proxy_id), None)
        port_num = target.get("port") if target else proxy_id
        if messagebox.askyesno(t("msg_del_proxy_title"), t("msg_del_proxy_text", port=port_num)):
            proxy_manager.delete_proxy(proxy_id)
            self.load_proxies_data()
            total_pages = max(1, (len(self.proxies) + proxy_manager.PAGE_SIZE - 1) // proxy_manager.PAGE_SIZE)
            if self.current_proxy_page > total_pages:
                self.current_proxy_page = total_pages
            self.render_proxy_table()

    def on_prev_proxy_page(self):
        if self.current_proxy_page > 1:
            self.current_proxy_page -= 1
            self.render_proxy_table()

    def on_next_proxy_page(self):
        total_pages = max(1, (len(self.proxies) + proxy_manager.PAGE_SIZE - 1) // proxy_manager.PAGE_SIZE)
        if self.current_proxy_page < total_pages:
            self.current_proxy_page += 1
            self.render_proxy_table()

    def check_single_proxy_async(self, proxy: dict):
        """Асинхронная проверка одного прокси."""
        def worker():
            res = proxy_manager.probe_single_proxy(proxy)
            # Обновляем в локальном списке
            for idx, p in enumerate(self.proxies):
                if p.get("id") == res.get("id"):
                    self.proxies[idx] = res
                    break
            proxy_manager.save_proxies(self.proxies)
            self.after(0, self.render_proxy_table)

        threading.Thread(target=worker, daemon=True).start()

    def check_current_proxy_page_async(self):
        """Асинхронная проверка всех прокси на текущей странице."""
        if self.is_checking_proxies:
            return

        self.is_checking_proxies = True
        self.btn_check_proxies.config(state="disabled", text="⏳ Проверка...")

        page_items, _ = proxy_manager.get_paginated_proxies(
            self.proxies, self.current_proxy_page, proxy_manager.PAGE_SIZE
        )

        def worker():
            checked_items = proxy_manager.check_all_proxies(page_items)
            checked_map = {item["id"]: item for item in checked_items}
            for idx, p in enumerate(self.proxies):
                if p.get("id") in checked_map:
                    self.proxies[idx] = checked_map[p["id"]]
            proxy_manager.save_proxies(self.proxies)

            def update_ui():
                self.is_checking_proxies = False
                self.btn_check_proxies.config(state="normal", text=t("btn_check_all_proxies"))
                self.render_proxy_table()
                self._update_claude_proxy_combo()
                self.refresh_routes_async()

            self.after(0, update_ui)

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # СТРАНИЦА 3: GEMINI OAUTH
    # =========================================================================
    def _build_page_gemini(self, parent: tk.Frame):
        # Верхняя панель Gemini
        g_top = tk.Frame(parent, bg=C["bg"])
        g_top.pack(fill="x", pady=(0, 10))

        title_frame = tk.Frame(g_top, bg=C["bg"])
        title_frame.pack(side="left")

        tk.Label(
            title_frame, text="🔮 Google Gemini OAuth2 Профили", font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(anchor="w")

        tk.Label(
            title_frame,
            text="Использование лимитов Google AI Pro через OAuth (БЕЗ API-ключей!). Мгновенная смена аккаунтов.",
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        btn_box = tk.Frame(g_top, bg=C["bg"])
        btn_box.pack(side="right")

        self.btn_reset_order = tk.Button(
            btn_box, text=t("btn_reset_proxies_order"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["accent_peach"], activebackground=C["border"],
            bd=0, padx=10, pady=6, cursor="hand2",
            command=self.on_reset_gemini_proxies_order
        )
        self.btn_reset_order.pack(side="left", padx=(0, 8))

        self.btn_refresh_gemini = tk.Button(
            btn_box, text="🔄 Обновить профили", font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.refresh_gemini_profiles_async
        )
        self.btn_refresh_gemini.pack(side="left", padx=(0, 8))

        self.btn_add_google_acc = tk.Button(
            btn_box, text="➕ Добавить аккаунт", font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", activebackground="#b4befe",
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.on_add_google_account
        )
        self.btn_add_google_acc.pack(side="left")

        # Карточка активного аккаунта (Spotlight)
        self.active_spotlight_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        self.active_spotlight_card.configure(highlightbackground=C["accent_blue"], highlightthickness=1)
        self.active_spotlight_card.pack(fill="x", pady=(0, 12))

        sp_in = tk.Frame(self.active_spotlight_card, bg=C["card"])
        sp_in.pack(fill="both", expand=True, padx=16, pady=12)

        sp_head = tk.Frame(sp_in, bg=C["card"])
        sp_head.pack(fill="x")

        tk.Label(
            sp_head, text=t("lbl_active_account"),
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        ).pack(side="left")

        self.spotlight_badge = tk.Label(
            sp_head, text="🟢 АКТИВЕН", font=FONT_BOLD,
            bg=C["green"], fg="#11111b", padx=8, pady=2
        )
        self.spotlight_badge.pack(side="right")

        self.spotlight_email_lbl = tk.Label(
            sp_in, text="Определение активного аккаунта...", font=FONT_APP_TITLE,
            fg=C["fg"], bg=C["card"]
        )
        self.spotlight_email_lbl.pack(anchor="w", pady=(4, 2))

        self.spotlight_info_lbl = tk.Label(
            sp_in, text="Загрузка данных токена...", font=FONT_MAIN,
            fg=C["subtext"], bg=C["card"]
        )
        self.spotlight_info_lbl.pack(anchor="w")

        # ── Карточка автосмены (Auto-Failover Guard) ─────────────
        self.guard_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        self.guard_card.configure(highlightbackground=C["border"], highlightthickness=1)
        self.guard_card.pack(fill="x", pady=(0, 12))

        g_in = tk.Frame(self.guard_card, bg=C["card"])
        g_in.pack(fill="both", expand=True, padx=16, pady=10)

        g_left = tk.Frame(g_in, bg=C["card"])
        g_left.pack(side="left", fill="both", expand=True)

        g_title_row = tk.Frame(g_left, bg=C["card"])
        g_title_row.pack(fill="x")

        tk.Label(
            g_title_row, text=t("guard_card_title"),
            font=FONT_BOLD, fg=C["fg"], bg=C["card"]
        ).pack(side="left")

        self.guard_badge = tk.Label(
            g_title_row, text="🟢 АКТИВНА", font=FONT_SUB,
            fg="#11111b", bg=C["green"], padx=6, pady=1
        )
        self.guard_badge.pack(side="left", padx=(10, 0))

        self.guard_desc_lbl = tk.Label(
            g_left,
            text=t("guard_card_desc"),
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        )
        self.guard_desc_lbl.pack(anchor="w", pady=(2, 0))

        fo_row = tk.Frame(g_left, bg=C["card"])
        fo_row.pack(fill="x", pady=(4, 0))

        tk.Checkbutton(
            fo_row,
            text=t("guard_chk_failover"),
            variable=self.auto_proxy_failover_enabled,
            font=FONT_BOLD, fg=C["accent_peach"], bg=C["card"],
            activebackground=C["card"], activeforeground=C["accent_peach"],
            selectcolor=C["card_inner"], bd=0, cursor="hand2"
        ).pack(side="left")

        g_right = tk.Frame(g_in, bg=C["card"])
        g_right.pack(side="right")

        self.btn_toggle_guard = tk.Button(
            g_right, text="Остановить", font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=10, pady=4, cursor="hand2",
            command=self.on_toggle_guard
        )
        self.btn_toggle_guard.pack(side="left", padx=(0, 6))

        self.btn_next_profile = tk.Button(
            g_right, text="⚡ Ротация (Next)", font=FONT_MAIN,
            bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"],
            bd=0, padx=8, pady=4, cursor="hand2",
            command=self.on_rotate_next_profile
        )
        self.btn_next_profile.pack(side="left")

        # Список сохраненных профилей
        tk.Label(
            parent, text="Сохраненные Google-профили (~/.gemini/profiles):",
            font=FONT_BOLD, fg=C["fg"], bg=C["bg"]
        ).pack(anchor="w", pady=(4, 6))

        self.gemini_profiles_scroll = tk.Frame(parent, bg=C["bg"])
        self.gemini_profiles_scroll.pack(fill="both", expand=True)

        self.gemini_cards_container = tk.Frame(self.gemini_profiles_scroll, bg=C["bg"])
        self.gemini_cards_container.pack(fill="both", expand=True)

    def load_gemini_profiles_data(self):
        """Загрузка профилей Gemini из WSL2."""
        self.gemini_profiles = gemini_manager.list_profiles()
        try:
            strategy_manager.seed_initial_history_if_empty(self.gemini_profiles, self.proxies)
        except Exception:
            pass

    def _check_and_update_vault_meta(self):
        """Проверяет, нужно ли фоново обновить метаданные заблокированных токенов."""
        import time
        import token_vault_manager
        import backup_manager
        pw = backup_manager.load_backup_password()
        if not pw or not token_vault_manager.is_vault_locked():
            return
            
        now = time.time()
        if now - token_vault_manager.LAST_GLOBAL_METADATA_UPDATE < 60:
            return
            
        needs_update = False
        
        # Check Gemini
        for p in getattr(self, 'gemini_profiles', []):
            if p.get("is_locked") and (p.get("is_expired") or "Отсутствует" in p.get("email", "")):
                needs_update = True
                break
                
        # Check Claude
        for cp in getattr(self, 'claude_profiles', []):
            if cp.get("is_locked") and cp.get("is_expired"):
                needs_update = True
                break
                
        st = getattr(self, "claude_active_status", {})
        if st and st.get("is_locked") and st.get("is_expired"):
            needs_update = True

        if needs_update:
            try:
                with token_vault_manager.auto_unlock_context():
                    pass # Metadata updates automatically inside context hook
                self.load_gemini_profiles_data()
                self.load_claude_profiles_data()
            except Exception:
                pass

    def render_gemini_page(self):
        """Отрисовка карточек аккаунтов Gemini."""
        self._check_and_update_vault_meta()
        # 1. Обновляем карточку текущего активного аккаунта
        active_prof = next((p for p in self.gemini_profiles if p.get("is_active")), None)
        if active_prof:
            port = active_prof.get("port", gemini_manager.BASE_SOCKS5_PORT)
            p_ip = active_prof.get("proxy_ip", "-")
            p_co = active_prof.get("proxy_country", "undefined")
            self.spotlight_email_lbl.config(
                text=f"🟢 {active_prof.get('email')} ({active_prof.get('name')})"
            )
            self.spotlight_info_lbl.config(
                text=t("spotlight_info", profile=active_prof.get('profile_name'), expiry=active_prof.get('expiry_text'), port=port, ip=p_ip, country=p_co),
                fg=C["green"] if not active_prof.get("is_expired") else C["yellow"]
            )
            self.spotlight_badge.config(text=f"🟢 {t('btn_active')} (:{port})", bg=C["green"], fg="#11111b")
        else:
            self.spotlight_email_lbl.config(text=t("no_active_google"))
            self.spotlight_info_lbl.config(
                text=t("click_login_google"),
                fg=C["subtext"]
            )
            self.spotlight_badge.config(text="⚪ " + t("not_authorized"), bg=C["border"], fg=C["fg"])

        # Обновляем бейдж и кнопку сторожа автосмены
        guard_on = gemini_manager.is_guard_running()
        if guard_on:
            self.guard_badge.config(text="🟢 " + t("lbl_guard_running"), bg=C["green"], fg="#11111b")
            self.btn_toggle_guard.config(text=t("btn_stop"), fg=C["red"])
        else:
            self.guard_badge.config(text="⚪ " + t("lbl_guard_stopped"), bg=C["card_inner"], fg=C["subtext"])
            self.btn_toggle_guard.config(text=t("btn_start"), fg=C["green"])

        # 2. Отрисовка списка профилей
        for widget in self.gemini_cards_container.winfo_children():
            widget.destroy()

        if not self.gemini_profiles:
            empty = tk.Frame(self.gemini_cards_container, bg=C["card"], bd=1, relief="solid")
            empty.configure(highlightbackground=C["border"], highlightthickness=1)
            empty.pack(fill="x", pady=8)
            tk.Label(
                empty,
                text="Нет сохраненных OAuth профилей.\nНажмите кнопку выше, чтобы войти в первый Google-аккаунт через браузер.",
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"], justify="center", pady=24
            ).pack(fill="both")
            return

        for prof in self.gemini_profiles:
            is_active = prof.get("is_active", False)
            p_name = prof.get("profile_name", "unknown")
            email = prof.get("email", "Без email")
            user_name = prof.get("name", "Пользователь")
            expiry = prof.get("expiry_text", "-")
            port = prof.get("port", gemini_manager.BASE_SOCKS5_PORT)
            p_st = prof.get("proxy_status", "unknown")
            p_ip = prof.get("proxy_ip", "-")
            p_co = prof.get("proxy_country", "undefined")

            card = tk.Frame(self.gemini_cards_container, bg=C["card"], bd=1, relief="solid")
            border_color = C["accent_blue"] if is_active else C["border"]
            card.configure(highlightbackground=border_color, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))

            c_in = tk.Frame(card, bg=C["card"])
            c_in.pack(fill="both", expand=True, padx=14, pady=10)

            # Номер профиля (#1, #2...): определяет порт; меняется стрелками или кликом по номеру
            position = prof.get("position", 1)
            total = len(self.gemini_profiles)
            num_col = tk.Frame(c_in, bg=C["card"])
            num_col.pack(side="left", padx=(0, 12))
            nav_btn = dict(font=FONT_SUB, bg=C["card"], fg=C["subtext"], activebackground=C["card_inner"],
                           activeforeground=C["accent_blue"], bd=0, padx=4, pady=0, cursor="hand2", relief="flat")
            tk.Button(
                num_col, text="▲", state="normal" if position > 1 else "disabled",
                command=lambda name=p_name, pos=position: self.on_change_gemini_position(name, pos - 1), **nav_btn
            ).pack()
            tk.Button(
                num_col, text=f"#{position}", font=FONT_APP_TITLE,
                bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"], activeforeground=C["fg"],
                bd=0, padx=8, pady=2, cursor="hand2", relief="flat",
                command=lambda name=p_name: self.on_change_gemini_position(name)
            ).pack()
            tk.Button(
                num_col, text="▼", state="normal" if position < total else "disabled",
                command=lambda name=p_name, pos=position: self.on_change_gemini_position(name, pos + 1), **nav_btn
            ).pack()

            # Левая колонка: Инфо
            left = tk.Frame(c_in, bg=C["card"])
            left.pack(side="left", fill="both", expand=True)

            t_line = tk.Frame(left, bg=C["card"])
            t_line.pack(fill="x")

            title_text = p_name
            tk.Label(
                t_line, text=title_text, font=FONT_TITLE,
                fg=C["fg"], bg=C["card"]
            ).pack(side="left")

            btn_rename_inline = tk.Button(
                t_line, text="✏️", font=FONT_SUB,
                bg=C["card"], fg=C["subtext"], activebackground=C["card_inner"], activeforeground=C["accent_blue"],
                bd=0, padx=4, pady=0, cursor="hand2", relief="flat",
                command=lambda name=p_name: self.on_rename_gemini_profile(name)
            )
            btn_rename_inline.pack(side="left", padx=(4, 0))

            if is_active:
                tk.Label(
                    t_line, text=f" {t('btn_active')} ", font=FONT_SUB,
                    fg="#11111b", bg=C["green"], padx=6, pady=1
                ).pack(side="left", padx=(8, 0))
            else:
                tk.Label(
                    t_line, text=f" {t('badge_standby')} ", font=FONT_SUB,
                    fg=C["subtext"], bg=C["card_inner"], padx=6, pady=1
                ).pack(side="left", padx=(8, 0))

            # Бейдж персонального порта с индикацией ручного / последовательного режима
            is_manual = prof.get("is_manual", False)
            proxy_num = prof.get("proxy_num", (port - gemini_manager.BASE_SOCKS5_PORT + 1))
            mode_text = t("badge_manual_proxy") if is_manual else t("badge_default_proxy")
            port_badge = tk.Label(
                t_line, text=f" Proxy {proxy_num} (:{port}) • {mode_text} ", font=FONT_SUB,
                fg="#11111b" if is_manual else C["accent_mauve"],
                bg=C["accent_peach"] if is_manual else C["tag_bg"],
                padx=6, pady=1
            )
            port_badge.pack(side="left", padx=(8, 0))

            sub_line = tk.Frame(left, bg=C["card"])
            sub_line.pack(fill="x", pady=(3, 0))

            if p_st == "online":
                st_badge = f"🟢 {p_ip} ({p_co})"
            elif p_st == "offline":
                st_badge = f"🔴 Порт :{port} Offline"
            else:
                st_badge = f"⚪ Порт :{port}"

            info_parts = []
            if email and email not in ("Без email", "Не авторизован", "Нет файла"):
                if user_name and user_name != email:
                    info_parts.append(f"Google ID: {email} ({user_name})")
                else:
                    info_parts.append(f"Google ID: {email}")
            else:
                info_parts.append("Google ID: Не авторизован")

            info_parts.append(f"Токен: {expiry}")
            info_parts.append(f"Шлюз: {st_badge}")

            tk.Label(
                sub_line,
                text="  •  ".join(info_parts),
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"]
            ).pack(side="left")

            # Правая колонка: Кнопки
            right = tk.Frame(c_in, bg=C["card"])
            right.pack(side="right")

            # Кнопка ручного выбора SOCKS5 прокси для данного аккаунта
            proxy_choices = proxy_manager.get_proxy_choices()
            proxy_menu_btn = tk.Menubutton(
                right, text=f"🛡️ Proxy {proxy_num} ▾", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"],
                bd=0, padx=8, pady=4, cursor="hand2", relief="flat"
            )
            p_menu = tk.Menu(
                proxy_menu_btn, tearoff=0,
                bg=C["card_inner"], fg=C["fg"],
                activebackground=C["accent_blue"], activeforeground="#11111b"
            )
            for choice in proxy_choices:
                c_port = choice["port"]
                c_label = choice["display"]
                p_menu.add_command(
                    label=c_label,
                    command=lambda name=p_name, pt=c_port: self.on_select_account_proxy(name, pt)
                )
            proxy_menu_btn.config(menu=p_menu)
            proxy_menu_btn.pack(side="left", padx=(0, 6))

            if not is_active:
                btn_sw = tk.Button(
                    right, text="⚡ Активировать", font=FONT_BOLD,
                    bg=C["accent_blue"], fg="#11111b", activebackground="#b4befe",
                    bd=0, padx=10, pady=4, cursor="hand2",
                    command=lambda name=p_name: self.on_switch_gemini_profile(name)
                )
                btn_sw.pack(side="left", padx=(0, 6))

            btn_test = tk.Button(
                right, text="🔍 Проверить", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
                bd=0, padx=8, pady=4, cursor="hand2",
                command=lambda name=p_name: self.on_test_gemini_token(name)
            )
            btn_test.pack(side="left", padx=(0, 6))

            btn_rename = tk.Button(
                right, text="✏️", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"],
                bd=0, padx=6, pady=4, cursor="hand2",
                command=lambda name=p_name: self.on_rename_gemini_profile(name)
            )
            btn_rename.pack(side="left", padx=(0, 6 if not is_active and len(self.gemini_profiles) > 1 else 0))

            if not is_active and len(self.gemini_profiles) > 1:
                btn_del = tk.Button(
                    right, text="🗑️", font=FONT_MAIN,
                    bg=C["card_inner"], fg=C["red"], activebackground=C["border"],
                    bd=0, padx=6, pady=4, cursor="hand2",
                    command=lambda name=p_name: self.on_delete_gemini_profile(name)
                )
                btn_del.pack(side="left")

    def on_change_gemini_position(self, profile_name: str, new_position: int | None = None):
        """Смена номера профиля: профиль встаёт на новое место и получает порт своего номера."""
        total = len(self.gemini_profiles)
        if new_position is None:
            curr = next((p.get("position") for p in self.gemini_profiles if p.get("profile_name") == profile_name), 1)
            new_position = simpledialog.askinteger(
                t("dlg_gemini_position_title"),
                t("dlg_gemini_position_prompt", profile=profile_name, total=total),
                initialvalue=curr, minvalue=1, maxvalue=max(total, 1), parent=self
            )
            if not new_position or new_position == curr:
                return
        ok, msg = gemini_manager.set_profile_position(profile_name, new_position)
        if not ok:
            messagebox.showerror(t("error"), msg)
            return
        self.load_gemini_profiles_data()
        self.render_gemini_page()
        self.refresh_routes_async()

    def on_rename_gemini_profile(self, profile_name: str):
        """Переименование аккаунта/профиля."""
        new_name = simpledialog.askstring(
            "Переименовать аккаунт",
            f"Текущее имя профиля: {profile_name}\n\nВведите новое имя профиля:",
            initialvalue=profile_name,
            parent=self
        )
        if not new_name or new_name.strip() == profile_name:
            return

        ok, msg = gemini_manager.rename_profile(profile_name, new_name.strip())
        if ok:
            self.load_gemini_profiles_data()
            self.render_gemini_page()
            self.render_strategy_page()
            self.refresh_routes_async()
            messagebox.showinfo("Переименование", msg)
        else:
            messagebox.showerror("Ошибка", msg)

    def on_switch_gemini_profile(self, profile_name: str):
        """Быстрое переключение активного профиля в 1 клик."""
        with token_vault_manager.auto_unlock_context():
            ok, msg = gemini_manager.switch_profile(profile_name)
        if ok:
            self.load_gemini_profiles_data()
            self.render_gemini_page()
            # Обновляем также роутер
            self.refresh_routes_async()
            messagebox.showinfo("Переключение", msg)
        else:
            messagebox.showerror("Ошибка", msg)

    def on_test_gemini_token(self, profile_name: str):
        """Онлайн-проверка токена в Google API через персональный порт."""
        def worker():
            res = gemini_manager.check_token_live(profile_name)
            def show_res():
                port = res.get("port", gemini_manager.BASE_SOCKS5_PORT)
                if res.get("valid"):
                    messagebox.showinfo(
                        "Google OAuth2 Валидация",
                        f"✓ Токен активен и полностью валиден в Google API!\n\n"
                        f"Google ID: {res.get('email')}\n"
                        f"Выделенный порт: SOCKS5 127.0.0.1:{port}\n"
                        f"Доступ: Полная квота Google AI Pro / Gemini Advanced"
                    )
                else:
                    messagebox.showwarning(
                        "Google OAuth2 Валидация",
                        f"Токен недоступен или порт не отвечает:\n{res.get('error')}\n\n"
                        f"Проверьте, что в Xray запущен inbound на порту {port}."
                    )
            self.after(0, show_res)

        threading.Thread(target=worker, daemon=True).start()

    def on_add_google_account(self):
        """Запуск терминала для входа в новый аккаунт Google."""
        existing_names = {p.get("profile_name") for p in self.gemini_profiles}
        idx = 1
        while f"account-{idx}" in existing_names:
            idx += 1
        suggested_name = f"account-{idx}"
        next_port = gemini_manager._calculate_default_sequential_port(suggested_name)
        p_name = simpledialog.askstring(
            "Новый Google аккаунт",
            f"Будет привязан новый порт: SOCKS5 127.0.0.1:{next_port}\n\nВведите имя профиля:",
            initialvalue=suggested_name,
            parent=self
        )
        if not p_name:
            return

        p_name = p_name.strip()
        # Пересчитываем порт для фактического имени профиля
        actual_port = gemini_manager._calculate_default_sequential_port(p_name)
        # Резервируем порт заранее, чтобы gemini-oauth в WSL использовал именно его
        gemini_manager.reserve_new_profile_port(p_name, actual_port)

        with token_vault_manager.auto_unlock_context():
            launched = gemini_manager.launch_add_account_terminal(p_name)
        if launched:
            messagebox.showinfo(
                "Авторизация в браузере",
                f"Открыт терминал авторизации для '{p_name}'.\nВыделенный порт: SOCKS5 :{actual_port}\n\n"
                f"1. Перейдите по ссылке в появившемся окне.\n"
                f"2. Войдите в нужный аккаунт Google в браузере.\n"
                f"3. После завершения нажмите 'Обновить профили' здесь."
            )
        else:
            messagebox.showerror("Ошибка", "Не удалось запустить терминал WSL.")

    def on_delete_gemini_profile(self, profile_name: str):
        """Удаление резервного профиля."""
        if messagebox.askyesno("Удаление профиля", f"Удалить сохраненный профиль '{profile_name}'?"):
            with token_vault_manager.auto_unlock_context():
                ok, msg = gemini_manager.delete_profile(profile_name)
            if ok:
                self.load_gemini_profiles_data()
                self.render_gemini_page()
            else:
                messagebox.showerror("Ошибка", msg)

    def on_select_account_proxy(self, profile_name: str, port: int):
        """Ручной выбор SOCKS5-прокси для конкретного профиля."""
        ok, msg = gemini_manager.set_profile_manual_port(profile_name, port)
        if ok:
            self.load_gemini_profiles_data()
            self.render_gemini_page()
            self.refresh_routes_async()
            try:
                email = next((p.get("email") for p in self.gemini_profiles if p.get("profile_name") == profile_name), profile_name)
                curr_proxies = proxy_manager.load_proxies()
                p_obj = next((p for p in curr_proxies if p.get("port") == port), None)
                strategy_manager.log_event(
                    account_email=email,
                    profile_name=profile_name,
                    port=port,
                    ip=p_obj.get("ip", "-") if p_obj else "-",
                    country=p_obj.get("country", "undefined") if p_obj else "undefined",
                    event="manual_proxy_select",
                    note=f"Пользователь вручную назначил SOCKS5 :{port}"
                )
            except Exception:
                pass
            messagebox.showinfo(t("gemini_title"), msg)
        else:
            messagebox.showerror(t("app_name"), msg)

    def on_reset_gemini_proxies_order(self):
        """Сброс ручных привязок и автоматическая расстановка прокси строго по порядку."""
        ok, msg = gemini_manager.reset_all_profiles_to_sequential()
        if ok:
            self.load_gemini_profiles_data()
            self.render_gemini_page()
            self.refresh_routes_async()
            messagebox.showinfo(t("msg_proxies_reset_title"), msg)
        else:
            messagebox.showerror(t("app_name"), msg)

    def on_toggle_guard(self):
        """Включение / выключение службы автосмены при лимитах."""
        if gemini_manager.is_guard_running():
            ok, msg = gemini_manager.stop_guard()
            if ok:
                self.render_gemini_page()
                messagebox.showinfo("Автосмена", "Служба автосмены аккаунтов остановлена.")
        else:
            ok, msg = gemini_manager.start_guard()
            if ok:
                self.render_gemini_page()
                messagebox.showinfo(
                    "Автосмена",
                    "Служба автосмены активна!\nПри получении ошибки 429 (Resource Exhausted) система автоматически переключит аккаунт и SOCKS5-порт."
                )

    def on_rotate_next_profile(self):
        """Ручная ротация на следующий профиль по кругу (Round-Robin)."""
        ok, msg = gemini_manager.switch_next_profile()
        if ok:
            self.load_gemini_profiles_data()
            self.render_gemini_page()
            self.refresh_routes_async()
            messagebox.showinfo("Ротация", msg)
        else:
            messagebox.showwarning("Ротация", msg)

    def refresh_gemini_profiles_async(self):
        """Обновление списка профилей."""
        def worker():
            try:
                import token_vault_manager
                with token_vault_manager.auto_unlock_context():
                    pass # Принудительно разблокируем и обновляем метаданные
            except Exception:
                pass
            self.load_gemini_profiles_data()
            self.after(0, self.render_gemini_page)

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # СТРАНИЦА: CLAUDE OAUTH
    # =========================================================================
    def _build_page_claude_oauth(self, parent: tk.Frame):
        self.claude_profiles = []
        self.claude_active_status = {}

        c_top = tk.Frame(parent, bg=C["bg"])
        c_top.pack(fill="x", pady=(0, 10))

        title_frame = tk.Frame(c_top, bg=C["bg"])
        title_frame.pack(side="left")

        tk.Label(
            title_frame, text=t("claude_oauth_title"), font=FONT_TITLE,
            fg=C["accent_peach"], bg=C["bg"]
        ).pack(anchor="w")

        tk.Label(
            title_frame, text=t("claude_oauth_desc"),
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        btn_box = tk.Frame(c_top, bg=C["bg"])
        btn_box.pack(side="right")

        tk.Button(
            btn_box, text=t("btn_claude_refresh_profiles"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.refresh_claude_profiles_async
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            btn_box, text=t("btn_claude_add_account"), font=FONT_BOLD,
            bg=C["accent_peach"], fg="#11111b", activebackground="#f5c2e7",
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.on_add_claude_account
        ).pack(side="left")

        # Карточка прокси (строго Anthropic Claude Proxy со страницы Routes)
        px_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        px_card.configure(highlightbackground=C["border"], highlightthickness=1)
        px_card.pack(fill="x", pady=(0, 12))
        px_in = tk.Frame(px_card, bg=C["card"])
        px_in.pack(fill="both", expand=True, padx=16, pady=10)
        self.claude_oauth_proxy_lbl = tk.Label(
            px_in, text="...", font=FONT_BOLD, fg=C["fg"], bg=C["card"], justify="left"
        )
        self.claude_oauth_proxy_lbl.pack(anchor="w")
        tk.Label(
            px_in, text=t("claude_oauth_proxy_note"),
            font=FONT_SUB, fg=C["subtext"], bg=C["card"], justify="left"
        ).pack(anchor="w", pady=(2, 0))

        # Карточка активного аккаунта
        sp = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        sp.configure(highlightbackground=C["accent_peach"], highlightthickness=1)
        sp.pack(fill="x", pady=(0, 12))
        sp_in = tk.Frame(sp, bg=C["card"])
        sp_in.pack(fill="both", expand=True, padx=16, pady=12)

        sp_head = tk.Frame(sp_in, bg=C["card"])
        sp_head.pack(fill="x")
        tk.Label(
            sp_head, text=t("lbl_active_claude_account"),
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        ).pack(side="left")
        self.claude_save_active_btn = tk.Button(
            sp_head, text=t("btn_claude_save_active"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["accent_peach"], activebackground=C["border"],
            bd=0, padx=10, pady=2, cursor="hand2",
            command=self.on_save_active_claude_account
        )
        self.claude_spotlight_badge = tk.Label(
            sp_head, text="...", font=FONT_BOLD, bg=C["border"], fg=C["fg"], padx=8, pady=2
        )
        self.claude_spotlight_badge.pack(side="right")

        self.claude_spotlight_email_lbl = tk.Label(
            sp_in, text=t("claude_loading"), font=FONT_APP_TITLE, fg=C["fg"], bg=C["card"]
        )
        self.claude_spotlight_email_lbl.pack(anchor="w", pady=(4, 2))
        self.claude_spotlight_info_lbl = tk.Label(
            sp_in, text="", font=FONT_MAIN, fg=C["subtext"], bg=C["card"], justify="left"
        )
        self.claude_spotlight_info_lbl.pack(anchor="w")

        tk.Label(
            parent, text=t("lbl_claude_saved_profiles"),
            font=FONT_BOLD, fg=C["fg"], bg=C["bg"]
        ).pack(anchor="w", pady=(4, 6))

        self.claude_cards_container = tk.Frame(parent, bg=C["bg"])
        self.claude_cards_container.pack(fill="both", expand=True)

    def load_claude_profiles_data(self):
        """Загрузка профилей Claude OAuth из WSL2."""
        try:
            self.claude_profiles = claude_oauth_manager.list_profiles()
            self.claude_active_status = claude_oauth_manager.get_active_status()
        except Exception as e:
            self.claude_profiles = []
            self.claude_active_status = {"exists": False, "email": f"Ошибка: {e}"}
        try:
            self.claude_proxy_online = claude_oauth_manager.is_claude_proxy_online(timeout=0.5)
        except Exception:
            self.claude_proxy_online = False

    def refresh_claude_profiles_async(self):
        def worker():
            try:
                import token_vault_manager
                with token_vault_manager.auto_unlock_context():
                    pass # Принудительно разблокируем и обновляем метаданные
            except Exception:
                pass
            self.load_claude_profiles_data()
            self.after(0, self.render_claude_oauth_page)

        threading.Thread(target=worker, daemon=True).start()

    def render_claude_oauth_page(self):
        """Отрисовка вкладки Claude OAuth."""
        self._check_and_update_vault_meta()
        px = claude_oauth_manager.get_claude_oauth_proxy()
        online = getattr(self, "claude_proxy_online", False)
        self.claude_oauth_proxy_lbl.config(
            text=t("claude_oauth_proxy_line", host=px["host"], port=px["port"], http_port=px["http_port"],
                   status=t("status_online") if online else t("status_offline")),
            fg=C["green"] if online else C["red"],
        )

        st = self.claude_active_status or {}
        if st.get("exists") and st.get("expires_at"):
            self.claude_spotlight_email_lbl.config(text=f"🟢 {st.get('email')} ({st.get('name')})")
            self.claude_spotlight_info_lbl.config(
                text=t("claude_token_line", expiry=st.get("expiry_text"), refresh=st.get("refresh_expiry_text"),
                       sub=st.get("subscription")),
                fg=C["yellow"] if st.get("is_expired") else C["green"],
            )
            if st.get("saved"):
                self.claude_spotlight_badge.config(text=f"🟢 {st.get('profile_name')}", bg=C["green"], fg="#11111b")
                self.claude_save_active_btn.pack_forget()
            else:
                self.claude_spotlight_badge.config(text=t("badge_claude_not_saved"), bg=C["yellow"], fg="#11111b")
                self.claude_save_active_btn.pack(side="right", padx=(0, 8))
        else:
            self.claude_spotlight_email_lbl.config(text=t("no_active_claude"))
            self.claude_spotlight_info_lbl.config(text=t("click_login_claude"), fg=C["subtext"])
            self.claude_spotlight_badge.config(text="⚪ " + t("not_authorized"), bg=C["border"], fg=C["fg"])
            self.claude_save_active_btn.pack_forget()

        for widget in self.claude_cards_container.winfo_children():
            widget.destroy()

        if not self.claude_profiles:
            empty = tk.Frame(self.claude_cards_container, bg=C["card"], bd=1, relief="solid")
            empty.configure(highlightbackground=C["border"], highlightthickness=1)
            empty.pack(fill="x", pady=8)
            tk.Label(
                empty, text=t("claude_no_profiles"),
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"], justify="center", pady=24
            ).pack(fill="both")
            return

        for prof in self.claude_profiles:
            is_active = prof.get("is_active", False)
            p_name = prof.get("profile_name", "unknown")

            card = tk.Frame(self.claude_cards_container, bg=C["card"], bd=1, relief="solid")
            card.configure(highlightbackground=C["accent_peach"] if is_active else C["border"], highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))
            c_in = tk.Frame(card, bg=C["card"])
            c_in.pack(fill="both", expand=True, padx=14, pady=10)

            left = tk.Frame(c_in, bg=C["card"])
            left.pack(side="left", fill="both", expand=True)

            t_line = tk.Frame(left, bg=C["card"])
            t_line.pack(fill="x")
            tk.Label(t_line, text=p_name, font=FONT_TITLE, fg=C["fg"], bg=C["card"]).pack(side="left")
            if is_active:
                tk.Label(t_line, text=f" {t('btn_active')} ", font=FONT_SUB,
                         fg="#11111b", bg=C["green"], padx=6, pady=1).pack(side="left", padx=(8, 0))
            else:
                tk.Label(t_line, text=f" {t('badge_standby')} ", font=FONT_SUB,
                         fg=C["subtext"], bg=C["card_inner"], padx=6, pady=1).pack(side="left", padx=(8, 0))
            tk.Label(t_line, text=f" Claude Proxy (:{prof.get('proxy_port')}) ", font=FONT_SUB,
                     fg=C["accent_peach"], bg=C["tag_bg"], padx=6, pady=1).pack(side="left", padx=(8, 0))
            tk.Label(t_line, text=f" {prof.get('subscription', '-')} ", font=FONT_SUB,
                     fg=C["accent_mauve"], bg=C["tag_bg"], padx=6, pady=1).pack(side="left", padx=(8, 0))

            tk.Label(
                left, text=f"Claude ID: {prof.get('email')} ({prof.get('name')})",
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"]
            ).pack(anchor="w", pady=(3, 0))

            if not prof.get("exists"):
                token_text = t("claude_login_pending")
                token_color = C["yellow"]
            else:
                token_text = t("claude_token_line", expiry=prof.get("expiry_text"),
                               refresh=prof.get("refresh_expiry_text"), sub=prof.get("subscription"))
                token_color = C["red"] if prof.get("is_expired") else C["subtext"]
            tk.Label(left, text=token_text, font=FONT_MAIN, fg=token_color, bg=C["card"]).pack(anchor="w")

            right = tk.Frame(c_in, bg=C["card"])
            right.pack(side="right")

            if not is_active and prof.get("exists"):
                tk.Button(
                    right, text=t("btn_activate"), font=FONT_BOLD,
                    bg=C["accent_peach"], fg="#11111b", activebackground="#f5c2e7",
                    bd=0, padx=10, pady=4, cursor="hand2",
                    command=lambda name=p_name: self.on_switch_claude_profile(name)
                ).pack(side="left", padx=(0, 6))

            if prof.get("exists"):
                tk.Button(
                    right, text=t("btn_check"), font=FONT_MAIN,
                    bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
                    bd=0, padx=8, pady=4, cursor="hand2",
                    command=lambda name=p_name: self.on_test_claude_token(name)
                ).pack(side="left", padx=(0, 6))

            tk.Button(
                right, text="✏️", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"],
                bd=0, padx=6, pady=4, cursor="hand2",
                command=lambda name=p_name: self.on_rename_claude_profile(name)
            ).pack(side="left", padx=(0, 6))

            tk.Button(
                right, text="🗑️", font=FONT_MAIN,
                bg=C["card_inner"], fg=C["red"], activebackground=C["border"],
                bd=0, padx=6, pady=4, cursor="hand2",
                command=lambda name=p_name: self.on_delete_claude_profile(name)
            ).pack(side="left")

    def on_add_claude_account(self):
        """Вход в новый аккаунт Claude через терминал WSL (строго через Claude Proxy)."""
        px = claude_oauth_manager.get_claude_oauth_proxy()
        p_name = simpledialog.askstring(
            t("dlg_claude_new_account"),
            t("dlg_claude_new_account_prompt", host=px["host"], port=px["port"], http_port=px["http_port"]),
            initialvalue=claude_oauth_manager.suggest_profile_name(),
            parent=self
        )
        if not p_name:
            return
        with token_vault_manager.auto_unlock_context():
            ok, msg = claude_oauth_manager.launch_login_terminal(p_name.strip())
        if ok:
            messagebox.showinfo(t("dlg_claude_new_account"), msg + "\n\n" + t("claude_login_steps"))
            self.refresh_claude_profiles_async()
        else:
            messagebox.showerror(t("error"), msg)

    def on_save_active_claude_account(self):
        st = self.claude_active_status or {}
        email = st.get("email") or ""
        p_name = simpledialog.askstring(
            t("btn_claude_save_active"), t("dlg_profile_name"),
            initialvalue=email if claude_oauth_manager.is_valid_profile_name(email) else claude_oauth_manager.suggest_profile_name(),
            parent=self
        )
        if not p_name:
            return
        ok, msg = claude_oauth_manager.save_active_as_profile(p_name.strip())
        (messagebox.showinfo if ok else messagebox.showerror)(t("tab_claude_oauth"), msg)
        self.refresh_claude_profiles_async()

    def on_switch_claude_profile(self, profile_name: str):
        with token_vault_manager.auto_unlock_context():
            ok, msg = claude_oauth_manager.switch_profile(profile_name)
        (messagebox.showinfo if ok else messagebox.showerror)(t("tab_claude_oauth"), msg)
        self.refresh_claude_profiles_async()

    def on_test_claude_token(self, profile_name: str):
        def worker():
            res = claude_oauth_manager.validate_profile_token(profile_name)

            def show():
                if res.get("success"):
                    messagebox.showinfo(t("tab_claude_oauth"), t("claude_token_valid", email=res.get("email"), port=res.get("port")))
                else:
                    messagebox.showerror(t("tab_claude_oauth"), str(res.get("error")))
            self.after(0, show)

        threading.Thread(target=worker, daemon=True).start()

    def on_rename_claude_profile(self, profile_name: str):
        new_name = simpledialog.askstring(t("tab_claude_oauth"), t("dlg_profile_name"), initialvalue=profile_name, parent=self)
        if not new_name or new_name.strip() == profile_name:
            return
        ok, msg = claude_oauth_manager.rename_profile(profile_name, new_name.strip())
        (messagebox.showinfo if ok else messagebox.showerror)(t("tab_claude_oauth"), msg)
        self.refresh_claude_profiles_async()

    def on_delete_claude_profile(self, profile_name: str):
        if not messagebox.askyesno(t("tab_claude_oauth"), t("dlg_claude_delete", profile=profile_name)):
            return
        with token_vault_manager.auto_unlock_context():
                ok, msg = claude_oauth_manager.delete_profile(profile_name)
        (messagebox.showinfo if ok else messagebox.showerror)(t("tab_claude_oauth"), msg)
        self.refresh_claude_profiles_async()

    # =========================================================================
    # СТРАНИЦА 4: STRATEGY & АНАЛИТИКА ПРОКСИ
    # =========================================================================
    def _build_page_strategy(self, parent: tk.Frame):
        s_top = tk.Frame(parent, bg=C["bg"])
        s_top.pack(fill="x", pady=(0, 10))

        title_frame = tk.Frame(s_top, bg=C["bg"])
        title_frame.pack(side="left")

        tk.Label(
            title_frame, text="📊 Гео-стратегия & Тенденции аккаунтов", font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(anchor="w")

        tk.Label(
            title_frame,
            text="Сводка активности Google-аккаунтов. Сравнение текущего IP с исторической привычкой (ОК / Nice to change).",
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        btn_box = tk.Frame(s_top, bg=C["bg"])
        btn_box.pack(side="right")

        self.btn_ext_log = tk.Button(
            btn_box, text="📜 Расширенный лог", font=FONT_BOLD,
            bg=C["card_inner"], fg=C["accent_peach"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.show_extended_log_dialog
        )
        self.btn_ext_log.pack(side="left", padx=(0, 8))

        self.btn_refresh_strat = tk.Button(
            btn_box, text=t("btn_refresh_analytics"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.render_strategy_page
        )
        self.btn_refresh_strat.pack(side="left")

        # Контейнер для карточек аккаунтов со скроллом
        canvas_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        canvas_card.configure(highlightbackground=C["border"], highlightthickness=1)
        canvas_card.pack(fill="both", expand=True)

        self.strat_canvas = tk.Canvas(canvas_card, bg=C["bg"], highlightthickness=0)
        self.strat_scrollbar = ttk.Scrollbar(canvas_card, orient="vertical", command=self.strat_canvas.yview)
        self.strat_cards_container = tk.Frame(self.strat_canvas, bg=C["bg"])

        self.strat_cards_container.bind(
            "<Configure>",
            lambda e: self.strat_canvas.configure(scrollregion=self.strat_canvas.bbox("all"))
        )
        self.strat_window_id = self.strat_canvas.create_window((0, 0), window=self.strat_cards_container, anchor="nw")
        self.strat_canvas.bind("<Configure>", lambda e: self.strat_canvas.itemconfig(self.strat_window_id, width=e.width))

        self.strat_canvas.configure(yscrollcommand=self.strat_scrollbar.set)
        self.strat_canvas.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        self.strat_scrollbar.pack(side="right", fill="y")

    def render_strategy_page(self):
        for widget in self.strat_cards_container.winfo_children():
            widget.destroy()

        tendencies = strategy_manager.get_account_tendencies()
        profiles = self.gemini_profiles
        if not profiles:
            empty_card = tk.Frame(self.strat_cards_container, bg=C["card"], padx=20, pady=30)
            empty_card.pack(fill="x", pady=10)
            tk.Label(
                empty_card, text=t("empty_strat"),
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"]
            ).pack()
            return

        for prof in profiles:
            p_name = prof.get("profile_name", "account")
            email = prof.get("email", "Без email")
            curr_port = prof.get("port", gemini_manager.BASE_SOCKS5_PORT)
            curr_ip = prof.get("proxy_ip", "-")
            curr_co = prof.get("proxy_country", "undefined")
            is_active = prof.get("is_active", False)

            eval_res = strategy_manager.evaluate_account_strategy(email, curr_co, tendencies, profile_name=p_name)
            st = eval_res.get("status", "neutral")

            card = tk.Frame(self.strat_cards_container, bg=C["card"], bd=1, relief="solid")
            card.configure(highlightbackground=C["border"], highlightthickness=1)
            card.pack(fill="x", pady=5, padx=2)

            c_in = tk.Frame(card, bg=C["card"])
            c_in.pack(fill="both", expand=True, padx=14, pady=10)

            # Верхняя строка
            t_row = tk.Frame(c_in, bg=C["card"])
            t_row.pack(fill="x")

            act_icon = "🟢" if is_active else "⚪"
            strat_title = f"{act_icon} {p_name}"
            tk.Label(
                t_row, text=strat_title, font=FONT_TITLE,
                fg=C["fg"], bg=C["card"]
            ).pack(side="left")

            tk.Label(
                t_row, text=f" SOCKS5 :{curr_port} ", font=FONT_SUB,
                fg=C["accent_mauve"], bg=C["tag_bg"], padx=6, pady=1
            ).pack(side="left", padx=(6, 0))

            # Бейдж соответствия
            if st == "ok":
                b_text = t("status_optimal")
                b_bg = C["green"]
                b_fg = "#11111b"
            elif st == "nice_to_change":
                b_text = t("status_nice_to_change")
                b_bg = C["yellow"]
                b_fg = "#11111b"
            else:
                b_text = t("status_baseline")
                b_bg = C["card_inner"]
                b_fg = C["subtext"]

            tk.Label(
                t_row, text=f" {b_text} ", font=FONT_BOLD,
                fg=b_fg, bg=b_bg, padx=8, pady=2
            ).pack(side="right")

            # Вторая строка
            d_row = tk.Frame(c_in, bg=C["card"])
            d_row.pack(fill="x", pady=(8, 4))

            google_prefix = f"Google: {email}  •  " if email and email not in ("Без email", "Не авторизован", "") else ""
            tk.Label(
                d_row,
                text=f"{google_prefix}Текущий туннель: 127.0.0.1:{curr_port}  ➔  {curr_ip} ({curr_co})",
                font=FONT_MAIN, fg=C["fg"], bg=C["card"]
            ).pack(side="left")

            dom_c = eval_res.get("dominant_country", "undefined")
            dom_p = eval_res.get("dominant_percent", 0)
            dom_dur = eval_res.get("dominant_duration_fmt", "")
            dur_str = f" • {dom_dur}" if dom_dur and dom_dur != "0 сек" else ""

            tk.Label(
                d_row,
                text=f"Привычный регион: {dom_c} ({dom_p}% времени{dur_str})",
                font=FONT_BOLD, fg=C["accent_blue"], bg=C["card"]
            ).pack(side="right")

            # Блок рекомендации
            rec_box = tk.Frame(c_in, bg=C["card_inner"], bd=1, relief="solid")
            rec_box.configure(highlightbackground=C["border"], highlightthickness=1)
            rec_box.pack(fill="x", pady=(4, 2))

            r_in = tk.Frame(rec_box, bg=C["card_inner"])
            r_in.pack(fill="x", padx=10, pady=6)

            tk.Label(
                r_in,
                text=eval_res.get("recommendation", ""),
                font=FONT_SUB, fg=C["accent_peach"] if st == "nice_to_change" else C["fg"],
                bg=C["card_inner"], wraplength=740, justify="left"
            ).pack(side="left")

            if st == "nice_to_change" and dom_c and dom_c.lower() not in ("undefined", "-"):
                target_cand = next(
                    (p for p in self.proxies if (p.get("country") or "").lower() == dom_c.lower() and p.get("port") != curr_port),
                    None
                )
                if target_cand:
                    btn_opt = tk.Button(
                        r_in,
                        text=f"⚡ Выбрать :{target_cand['port']} ({dom_c})",
                        font=FONT_BOLD, bg=C["accent_peach"], fg="#11111b",
                        activebackground="#fab387", bd=0, padx=8, pady=3, cursor="hand2",
                        command=lambda p_target=target_cand['port'], prof_n=p_name: self.on_reassign_account_proxy(prof_n, p_target)
                    )
                    btn_opt.pack(side="right", padx=(8, 0))

    def on_reassign_account_proxy(self, profile_name: str, new_port: int):
        gemini_manager.save_profile_port(profile_name, new_port)
        active_prof = next((p for p in self.gemini_profiles if p.get("is_active")), None)
        if active_prof and active_prof.get("profile_name") == profile_name:
            gemini_manager.write_active_proxy_env(new_port, active_prof.get("email", ""))
        self.load_gemini_profiles_data()
        self.render_strategy_page()
        self.refresh_routes_async()
        messagebox.showinfo("Стратегия", f"Профиль '{profile_name}' успешно перенаправлен на порт :{new_port}!")

    def show_extended_log_dialog(self):
        dlg = tk.Toplevel(self)
        dlg.title("Расширенный журнал сессий, времени соединений и статистика IP")
        dlg.geometry("960x580")
        dlg.minsize(860, 480)
        dlg.configure(bg=C["bg"])

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=C["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=C["card_inner"], foreground=C["fg"], padding=[12, 6])
        style.map("TNotebook.Tab", background=[("selected", C["card"])], foreground=[("selected", C["accent_blue"])])

        nb = ttk.Notebook(dlg)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        tab_history = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_history, text="📜 Журнал сессий и времени соединений")

        tab_ips = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_ips, text="🌐 Сводка по уникальным IP и длительности")

        # Вкладка 1: История с длительностью соединений
        cols1 = ("time", "duration", "account", "port", "ip", "country", "event", "note")
        tree1 = ttk.Treeview(tab_history, columns=cols1, show="headings", height=18)
        tree1.heading("time", text="Время начала")
        tree1.heading("duration", text="Длительность")
        tree1.heading("account", text="Аккаунт Google")
        tree1.heading("port", text="Порт")
        tree1.heading("ip", text="Внешний IP")
        tree1.heading("country", text="Страна")
        tree1.heading("event", text="Событие")
        tree1.heading("note", text="Примечание")

        tree1.column("time", width=135, anchor="w")
        tree1.column("duration", width=95, anchor="center")
        tree1.column("account", width=170, anchor="w")
        tree1.column("port", width=60, anchor="center")
        tree1.column("ip", width=115, anchor="w")
        tree1.column("country", width=95, anchor="w")
        tree1.column("event", width=85, anchor="center")
        tree1.column("note", width=140, anchor="w")

        sc1 = ttk.Scrollbar(tab_history, orient="vertical", command=tree1.yview)
        tree1.configure(yscrollcommand=sc1.set)
        tree1.pack(side="left", fill="both", expand=True)
        sc1.pack(side="right", fill="y")

        history = strategy_manager.load_history()
        for e in reversed(history):
            tree1.insert(
                "", "end",
                values=(
                    e.get("start_time") or e.get("timestamp", "-"),
                    e.get("duration_fmt", "-"),
                    e.get("account_email", "-"),
                    e.get("port", "-"),
                    e.get("ip", "-"),
                    e.get("country", "-"),
                    e.get("event", "-"),
                    e.get("note", "-"),
                )
            )

        # Вкладка 2: Сводка по IP с суммарным временем
        cols2 = ("ip", "country", "duration", "launches", "ports", "accounts", "last_seen")
        tree2 = ttk.Treeview(tab_ips, columns=cols2, show="headings", height=18)
        tree2.heading("ip", text="Внешний IP")
        tree2.heading("country", text="Страна")
        tree2.heading("duration", text="Общее время")
        tree2.heading("launches", text="Сессий")
        tree2.heading("ports", text="Порты")
        tree2.heading("accounts", text="Аккаунты")
        tree2.heading("last_seen", text="Последняя активность")

        tree2.column("ip", width=125, anchor="w")
        tree2.column("country", width=110, anchor="w")
        tree2.column("duration", width=105, anchor="center")
        tree2.column("launches", width=65, anchor="center")
        tree2.column("ports", width=80, anchor="w")
        tree2.column("accounts", width=160, anchor="w")
        tree2.column("last_seen", width=135, anchor="w")

        sc2 = ttk.Scrollbar(tab_ips, orient="vertical", command=tree2.yview)
        tree2.configure(yscrollcommand=sc2.set)
        tree2.pack(side="left", fill="both", expand=True)
        sc2.pack(side="right", fill="y")

        ip_stats = strategy_manager.get_extended_ip_stats()
        for s in ip_stats:
            ports_str = ", ".join(str(p) for p in s.get("ports", []))
            accs_str = ", ".join(s.get("accounts", []))
            tree2.insert(
                "", "end",
                values=(
                    s.get("ip", "-"),
                    s.get("country", "-"),
                    s.get("total_duration_fmt", "-"),
                    s.get("total_launches", 0),
                    ports_str,
                    accs_str,
                    s.get("last_seen", "-"),
                )
            )

    # =========================================================================
    # СТРАНИЦА 5: BACKUP & ВОССТАНОВЛЕНИЕ
    # =========================================================================
    def _build_page_backup(self, parent: tk.Frame):
        b_top = tk.Frame(parent, bg=C["bg"])
        b_top.pack(fill="x", pady=(0, 10))

        title_frame = tk.Frame(b_top, bg=C["bg"])
        title_frame.pack(side="left")

        tk.Label(
            title_frame, text="💾 Резервное копирование & Восстановление", font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(anchor="w")

        tk.Label(
            title_frame,
            text="Полный snapshot системы (настройки GUI, прокси, токены и аккаунты WSL2) с шифрованием AES-256-GCM.",
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        btn_box = tk.Frame(b_top, bg=C["bg"])
        btn_box.pack(side="right")

        self.btn_refresh_backup = tk.Button(
            btn_box, text="🔄 Обновить список", font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.render_backup_page
        )
        self.btn_refresh_backup.pack(side="left")

        # ── 1. Панель пароля шифрования ────────────────────────
        pw_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        pw_card.configure(highlightbackground=C["border"], highlightthickness=1)
        pw_card.pack(fill="x", pady=(0, 10))

        pw_in = tk.Frame(pw_card, bg=C["card"])
        pw_in.pack(fill="both", expand=True, padx=14, pady=10)

        r_pw1 = tk.Frame(pw_in, bg=C["card"])
        r_pw1.pack(fill="x")

        tk.Label(
            r_pw1, text=t("lbl_master_password"), font=FONT_BOLD,
            fg=C["accent_peach"], bg=C["card"]
        ).pack(side="left")

        self.backup_pw_entry = tk.Entry(
            r_pw1, textvariable=self.backup_password_var, show="*",
            font=FONT_MONO_BOLD, bg=C["card_inner"], fg=C["fg"],
            insertbackground=C["fg"], bd=1, relief="solid", width=28
        )
        self.backup_pw_entry.pack(side="left", padx=(10, 8), ipady=3)

        self.btn_eye = tk.Button(
            r_pw1, text=t("btn_show_pw"), font=FONT_SUB,
            bg=C["card_inner"], fg=C["subtext"], activebackground=C["border"],
            bd=0, padx=8, pady=3, cursor="hand2",
            command=self.toggle_backup_pw_visibility
        )
        self.btn_eye.pack(side="left")

        # Хэш-отпечаток
        self.backup_fp_lbl = tk.Label(
            r_pw1, text="Хэш-отпечаток (SHA-256): [ ---------------- ]",
            font=FONT_MONO, fg=C["accent_blue"], bg=C["card"]
        )
        self.backup_fp_lbl.pack(side="right")
        self._on_backup_password_changed()

        tk.Label(
            pw_in,
            text="Пароль хранится в .env и используется для шифрования архивов AES-256-GCM. Без введенного пароля бэкап невозможно создать или расшифровать.",
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        ).pack(anchor="w", pady=(4, 0))

        # ── 1.b. Хранилище токенов ─────────────────────────────
        vault_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        vault_card.configure(highlightbackground=C["border"], highlightthickness=1)
        vault_card.pack(fill="x", pady=(0, 10))

        v_in = tk.Frame(vault_card, bg=C["card"])
        v_in.pack(fill="both", expand=True, padx=14, pady=10)

        # Статус
        is_locked = token_vault_manager.is_vault_locked()
        v_status = t("vault_locked_status") if is_locked else t("vault_unlocked_status")
        v_color = C["accent_peach"] if is_locked else C["accent_blue"]
        
        self.vault_lbl = tk.Label(v_in, text=f"{t('vault_title')}: {v_status}", font=FONT_BOLD, fg=v_color, bg=C["card"])
        self.vault_lbl.pack(side="left")

        # Кнопки
        tk.Button(
            v_in, text=t("btn_lock_vault"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["accent_peach"], activebackground=C["border"],
            bd=0, padx=10, pady=4, cursor="hand2",
            command=self.on_vault_lock
        ).pack(side="right", padx=(10, 0))

        tk.Button(
            v_in, text=t("btn_unlock_vault"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"],
            bd=0, padx=10, pady=4, cursor="hand2",
            command=self.on_vault_unlock
        ).pack(side="right")

        # ── 2. Панель параметров хранения и расписания ─────────
        cfg_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        cfg_card.configure(highlightbackground=C["border"], highlightthickness=1)
        cfg_card.pack(fill="x", pady=(0, 10))

        c_in = tk.Frame(cfg_card, bg=C["card"])
        c_in.pack(fill="both", expand=True, padx=14, pady=10)

        # Папка бэкапов
        r_dir = tk.Frame(c_in, bg=C["card"])
        r_dir.pack(fill="x")

        tk.Label(r_dir, text=t("lbl_backup_dir"), font=FONT_BOLD, fg=C["fg"], bg=C["card"]).pack(side="left")

        self.backup_dir_entry = tk.Entry(
            r_dir, textvariable=self.backup_dir_var, font=FONT_MAIN,
            bg=C["card_inner"], fg=C["fg"], insertbackground=C["fg"],
            bd=1, relief="solid"
        )
        self.backup_dir_entry.pack(side="left", fill="x", expand=True, padx=(10, 8), ipady=3)

        btn_browse = tk.Button(
            r_dir, text=t("btn_browse"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=10, pady=3, cursor="hand2",
            command=self.on_browse_backup_dir
        )
        btn_browse.pack(side="right")

        # Расписание и последний бэкап
        r_sched = tk.Frame(c_in, bg=C["card"])
        r_sched.pack(fill="x", pady=(8, 0))

        cb_auto = tk.Checkbutton(
            r_sched, text=t("lbl_enable_auto_backup"),
            variable=self.auto_backup_enabled_var,
            font=FONT_MAIN, fg=C["fg"], bg=C["card"], activebackground=C["card"],
            activeforeground=C["fg"], selectcolor=C["card_inner"], bd=0
        )
        cb_auto.pack(side="left")

        spin_hours = ttk.Spinbox(
            r_sched, from_=1, to=168, textvariable=self.backup_interval_var,
            width=4
        )
        spin_hours.pack(side="left", padx=(6, 6))

        tk.Label(r_sched, text="ч.", font=FONT_MAIN, fg=C["fg"], bg=C["card"]).pack(side="left")

        b_cfg = backup_manager.get_backup_config()
        self.last_backup_time_lbl = tk.Label(
            r_sched,
            text=f"Последний бэкап: {b_cfg.get('last_backup_time', '-')}",
            font=FONT_SUB, fg=C["accent_blue"], bg=C["card"]
        )
        self.last_backup_time_lbl.pack(side="right")

        # ── 3. Панель кнопок действий (Экспорт / Импорт) ───────
        act_box = tk.Frame(parent, bg=C["bg"])
        act_box.pack(fill="x", pady=(0, 10))

        self.btn_export_backup = tk.Button(
            act_box, text="📤 Создать бэкап сейчас (Экспорт)", font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", activebackground="#b4befe",
            bd=0, padx=16, pady=8, cursor="hand2",
            command=self.on_export_backup
        )
        self.btn_export_backup.pack(side="left", padx=(0, 10))

        self.btn_import_backup = tk.Button(
            act_box, text="📥 Восстановить из файла (Импорт)", font=FONT_BOLD,
            bg=C["green"], fg="#11111b", activebackground="#a6e3a1",
            bd=0, padx=16, pady=8, cursor="hand2",
            command=self.on_import_backup
        )
        self.btn_import_backup.pack(side="left")

        # ── 4. Таблица существующих файлов бэкапа ──────────────
        tbl_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        tbl_card.configure(highlightbackground=C["border"], highlightthickness=1)
        tbl_card.pack(fill="both", expand=True)

        t_head = tk.Frame(tbl_card, bg=C["table_header"], height=34)
        t_head.pack(fill="x")
        t_head.pack_propagate(False)

        tk.Label(t_head, text="ИМЯ ФАЙЛА БЭКАПА (.HBAK)", font=FONT_BOLD, fg=C["subtext"], bg=C["table_header"], width=36, anchor="w").pack(side="left", padx=10)
        tk.Label(t_head, text="РАЗМЕР", font=FONT_BOLD, fg=C["subtext"], bg=C["table_header"], width=12, anchor="center").pack(side="left", padx=4)
        tk.Label(t_head, text="ДАТА СОЗДАНИЯ", font=FONT_BOLD, fg=C["subtext"], bg=C["table_header"], width=20, anchor="w").pack(side="left", padx=4)
        tk.Label(t_head, text="ДЕЙСТВИЕ", font=FONT_BOLD, fg=C["subtext"], bg=C["table_header"], width=14, anchor="center").pack(side="right", padx=10)

        self.backup_list_container = tk.Frame(tbl_card, bg=C["card"])
        self.backup_list_container.pack(fill="both", expand=True, padx=4, pady=4)

    # ── 5. Опасная зона: Полный сброс всех данных ───────────
        danger_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        danger_card.configure(highlightbackground=C["red"], highlightthickness=1)
        danger_card.pack(fill="x", pady=(10, 0))

        d_in = tk.Frame(danger_card, bg=C["card"])
        d_in.pack(fill="both", expand=True, padx=14, pady=8)

        d_left = tk.Frame(d_in, bg=C["card"])
        d_left.pack(side="left")

        tk.Label(
            d_left, text=t("danger_zone_title"), font=FONT_BOLD,
            fg=C["red"], bg=C["card"]
        ).pack(anchor="w")

        tk.Label(
            d_left,
            text=t("danger_zone_desc"),
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        ).pack(anchor="w")

        self.btn_wipe_data = tk.Button(
            d_in, text=t("btn_wipe_all_data"), font=FONT_BOLD,
            bg=C["red"], fg="#11111b", activebackground="#f38ba8",
            bd=0, padx=14, pady=6, cursor="hand2",
            command=self.on_wipe_all_data
        )
        self.btn_wipe_data.pack(side="right")

    def on_vault_lock(self):
        pw = self.backup_password_var.get()
        if not pw:
            messagebox.showwarning(t("error"), t("err_pw_lock"))
            return
            
        success, msg = token_vault_manager.lock_tokens(pw)
        if success:
            messagebox.showinfo(t("success"), t("vault_lock_success"))
            self.render_backup_page()
        else:
            messagebox.showerror(t("error"), t("vault_lock_error"))

    def on_vault_unlock(self):
        pw = self.backup_password_var.get()
        if not pw:
            messagebox.showwarning(t("error"), t("err_pw_unlock"))
            return
            
        success, msg = token_vault_manager.unlock_tokens(pw)
        if success:
            messagebox.showinfo(t("success"), t("vault_unlock_success"))
            self.render_backup_page()
        else:
            messagebox.showerror(t("error"), t("vault_unlock_error"))

    def on_wipe_all_data(self):
        """Полная очистка всех данных программы с подтверждением."""
        if not messagebox.askyesno(t("msg_wipe_confirm_1_title"),
            "Вы действительно хотите безвозвратно стереть ВСЕ данные программы?\n\n"
            "Будут удалены:\n"
            "• Все Google OAuth профили и токены в WSL2\n"
            "• Журнал стратегии и статистика запусков\n"
            "• Все ключи и пароль в .env\n"
            "• Список прокси и настройки будут сброшены к начальным\n\n"
            "Перед сбросом рекомендуется создать резервную копию.\n\n"
            "Продолжить удаление?"
        ):
            return

        if not messagebox.askyesno(t("msg_wipe_confirm_2_title"),
            "Подтвердите окончательное удаление данных. Восстановление будет возможно только при наличии сохраненного файла бэкапа (.hbak).\n\nСтереть все данные сейчас?"
        ):
            return

        ok, msg = backup_manager.wipe_all_data()
        
        # Полный сброс кеша памяти GUI, чтобы удаленные стратегии и профили не всплыли после обновления
        self.integrations_data = []
        if hasattr(self, "claude_profile"): 
            self.claude_profile = None
            
        self.backup_password_var.set("")
        
        # Полностью перезагружаем стейт
        # (раньше здесь были self.load_data() и self.update_content() — таких методов нет,
        #  «Стереть все данные» падало с AttributeError; найдено тестом test_ui_command_refs.py)
        self.load_proxies_data()
        self.load_gemini_profiles_data()

        # Обновляем все UI: перерисовываем текущую вкладку
        self.switch_page(getattr(self, "active_tab", "routes"))

        if ok:
            messagebox.showinfo("Очистка завершена", f"✓ {msg}")
        else:
            messagebox.showwarning("Очистка", msg)

    def _on_backup_password_changed(self, *args):
        pw = self.backup_password_var.get()
        fp = backup_manager.compute_password_fingerprint(pw)
        if hasattr(self, "backup_fp_lbl"):
            self.backup_fp_lbl.config(text=t("lbl_fingerprint", fp=fp) if fp else t("lbl_empty_pw"))
        if pw:
            backup_manager.save_backup_password(pw)

    def toggle_backup_pw_visibility(self):
        self.backup_pw_show = not self.backup_pw_show
        if self.backup_pw_show:
            self.backup_pw_entry.config(show="")
            self.btn_eye.config(text=t("btn_hide_pw"))
        else:
            self.backup_pw_entry.config(show="*")
            self.btn_eye.config(text=t("btn_show_pw"))

    def on_browse_backup_dir(self):
        curr = self.backup_dir_var.get().strip() or str(backup_manager.DEFAULT_BACKUP_DIR)
        d = filedialog.askdirectory(initialdir=curr, title="Выберите папку для сохранения бэкапов")
        if d:
            self.backup_dir_var.set(d)
            self.render_backup_page()

    def render_backup_page(self):
        self._on_backup_password_changed()
        cfg = backup_manager.get_backup_config()
        if hasattr(self, "last_backup_time_lbl"):
            self.last_backup_time_lbl.config(text=f"Последний бэкап: {cfg.get('last_backup_time', '-')}")

        for w in self.backup_list_container.winfo_children():
            w.destroy()

        b_dir = self.backup_dir_var.get().strip() or str(backup_manager.DEFAULT_BACKUP_DIR)
        backups = backup_manager.list_backups_in_dir(b_dir)

        if not backups:
            e_row = tk.Frame(self.backup_list_container, bg=C["card"])
            e_row.pack(fill="x", pady=20)
            tk.Label(
                e_row, text=f"В папке {b_dir} пока нет файлов .hbak. Нажмите 'Создать бэкап сейчас'.",
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"]
            ).pack()
            return

        for idx, b in enumerate(backups[:10]):
            row_bg = C["card"] if idx % 2 == 0 else C["table_row_alt"]
            row = tk.Frame(self.backup_list_container, bg=row_bg, height=36)
            row.pack(fill="x", pady=1)
            row.pack_propagate(False)

            tk.Label(
                row, text=f"📦 {b['filename']}", font=FONT_MONO,
                fg=C["fg"], bg=row_bg, width=36, anchor="w"
            ).pack(side="left", padx=10)

            tk.Label(
                row, text=f"{b['size_kb']} КБ", font=FONT_MAIN,
                fg=C["subtext"], bg=row_bg, width=12, anchor="center"
            ).pack(side="left", padx=4)

            tk.Label(
                row, text=b["modified"], font=FONT_MAIN,
                fg=C["subtext"], bg=row_bg, width=20, anchor="w"
            ).pack(side="left", padx=4)

            btn_rest = tk.Button(
                row, text="⚡ Восстановить", font=FONT_SUB,
                bg=C["card_inner"], fg=C["accent_blue"], activebackground=C["border"],
                bd=0, padx=8, pady=2, cursor="hand2",
                command=lambda fp=b["filepath"]: self.on_restore_specific_backup(fp)
            )
            btn_rest.pack(side="right", padx=10)

    def on_export_backup(self):
        pw = self.backup_password_var.get().strip()
        if not pw:
            messagebox.showwarning("Внимание", "Сначала введите пароль для шифрования бэкапа!")
            return

        target_dir = self.backup_dir_var.get().strip() or str(backup_manager.DEFAULT_BACKUP_DIR)
        self.btn_export_backup.config(state="disabled", text="⏳ Создание бэкапа...")

        def worker():
            ok, msg, path = backup_manager.create_encrypted_backup(pw, target_dir)
            def done():
                self.btn_export_backup.config(state="normal", text="📤 Создать бэкап сейчас (Экспорт)")
                if ok:
                    self.render_backup_page()
                    messagebox.showinfo("Резервное копирование", f"{msg}\n\nРасположение: {path}")
                else:
                    messagebox.showerror("Ошибка бэкапа", msg)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def on_import_backup(self):
        pw = self.backup_password_var.get().strip()
        if not pw:
            messagebox.showwarning("Внимание", "Сначала введите пароль для расшифровки бэкапа!")
            return

        target_dir = self.backup_dir_var.get().strip() or str(backup_manager.DEFAULT_BACKUP_DIR)
        f = filedialog.askopenfilename(
            initialdir=target_dir,
            title="Выберите файл резервной копии Herdr",
            filetypes=[("Herdr Backup (*.hbak)", "*.hbak"), ("Все файлы", "*.*")]
        )
        if not f:
            return

        if not messagebox.askyesno(
            "Подтверждение импорта",
            "Импорт бэкапа полностью восстановит все настройки, прокси, токены и профили аккаунтов.\n\nПродолжить?"
        ):
            return

        self.btn_import_backup.config(state="disabled", text="⏳ Восстановление...")

        def worker():
            ok, msg, manifest = backup_manager.restore_encrypted_backup(f, pw)
            def done():
                self.btn_import_backup.config(state="normal", text="📥 Восстановить из файла (Импорт)")
                if ok:
                    self.load_backup_config_data()
                    self.load_proxies_data()
                    self.load_gemini_profiles_data()
                    self.render_backup_page()
                    if hasattr(self, "_load_initial_console_logs"):
                        self._load_initial_console_logs()
                    if hasattr(self, "on_integrations_status_click"):
                        self.on_integrations_status_click()
                    self.refresh_routes_async()
                    messagebox.showinfo("Импорт бэкапа", msg)
                else:
                    messagebox.showerror("Ошибка импорта", msg)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def load_backup_config_data(self):
        """Перезагружает параметры бэкапа из settings.json в переменные UI."""
        b_cfg = backup_manager.get_backup_config()
        self._suppress_backup_trace = True
        try:
            self.backup_dir_var.set(b_cfg.get("backup_dir", str(backup_manager.DEFAULT_BACKUP_DIR)))
            self.backup_interval_var.set(b_cfg.get("backup_interval_hours", 12))
            self.auto_backup_enabled_var.set(b_cfg.get("auto_backup_enabled", False))
            self.backup_password_var.set(backup_manager.load_backup_password())
        finally:
            self._suppress_backup_trace = False

    def on_restore_specific_backup(self, filepath: str):
        pw = self.backup_password_var.get().strip()
        if not pw:
            messagebox.showwarning("Внимание", "Сначала введите пароль для расшифровки бэкапа!")
            return

        fn = Path(filepath).name
        if not messagebox.askyesno(
            "Подтверждение импорта",
            f"Восстановить систему из файла:\n{fn}?\n\nТекущие данные будут заменены содержимым бэкапа."
        ):
            return

        def worker():
            ok, msg, _ = backup_manager.restore_encrypted_backup(filepath, pw)
            def done():
                if ok:
                    self.load_backup_config_data()
                    self.load_proxies_data()
                    self.load_gemini_profiles_data()
                    self.render_backup_page()
                    if hasattr(self, "_load_initial_console_logs"):
                        self._load_initial_console_logs()
                    if hasattr(self, "on_integrations_status_click"):
                        self.on_integrations_status_click()
                    self.refresh_routes_async()
                    messagebox.showinfo("Импорт бэкапа", msg)
                else:
                    messagebox.showerror("Ошибка импорта", msg)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # СТРАНИЦА: ИНТЕГРАЦИИ (AionUi, OmniRoute, Claude Code, Gemini Farm)
    # =========================================================================
    def _build_page_integrations(self, parent: tk.Frame):
        """Построение вкладки Интеграции."""
        p_top = tk.Frame(parent, bg=C["bg"])
        p_top.pack(fill="x", pady=(0, 10))

        tk.Label(
            p_top, text=t("integrations_title"), font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(anchor="w")

        tk.Label(
            p_top, text=t("integrations_subtitle"), font=FONT_SUB,
            fg=C["subtext"], bg=C["bg"]
        ).pack(anchor="w")

        # ── Карточка 1: Окно AionUi & Быстрый статус ────────────────────────
        card_aion = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        card_aion.configure(highlightbackground=C["border"], highlightthickness=1)
        card_aion.pack(fill="x", pady=(0, 10))

        pad_aion = tk.Frame(card_aion, bg=C["card"])
        pad_aion.pack(fill="both", expand=True, padx=16, pady=12)

        # Заголовок карточки
        head_box = tk.Frame(pad_aion, bg=C["card"])
        head_box.pack(fill="x", pady=(0, 6))

        tk.Label(
            head_box, text=t("card_aionui_title"), font=FONT_BOLD,
            fg=C["fg"], bg=C["card"]
        ).pack(side="left")

        self.lbl_integ_overall = tk.Label(
            head_box, text="READY", font=FONT_SUB,
            fg="#11111b", bg=C["green"], padx=6, pady=1
        )
        self.lbl_integ_overall.pack(side="right")

        tk.Label(
            pad_aion, text=t("card_aionui_desc"), font=FONT_SUB,
            fg=C["subtext"], bg=C["card"]
        ).pack(anchor="w", pady=(0, 10))

        # Блок статусов компонентов (4 плашки в одну линию)
        st_frame = tk.Frame(pad_aion, bg=C["card"])
        st_frame.pack(fill="x", pady=(0, 12))

        # 1. AionUi :25808
        self.badge_aionui = tk.Label(
            st_frame, text="AionUi (:25808): ● ...", font=FONT_SUB,
            bg=C["card_inner"], fg=C["subtext"], padx=8, pady=4, bd=1, relief="solid"
        )
        self.badge_aionui.configure(highlightbackground=C["border"], highlightthickness=1)
        self.badge_aionui.pack(side="left", padx=(0, 6), expand=True, fill="x")

        # 2. OmniRoute :20128
        self.badge_omniroute = tk.Label(
            st_frame, text="OmniRoute (:20128): ● ...", font=FONT_SUB,
            bg=C["card_inner"], fg=C["subtext"], padx=8, pady=4, bd=1, relief="solid"
        )
        self.badge_omniroute.configure(highlightbackground=C["border"], highlightthickness=1)
        self.badge_omniroute.pack(side="left", padx=6, expand=True, fill="x")

        # 3. Claude Code :1015
        self.badge_claude = tk.Label(
            st_frame, text="Claude (:1015): ● ...", font=FONT_SUB,
            bg=C["card_inner"], fg=C["subtext"], padx=8, pady=4, bd=1, relief="solid"
        )
        self.badge_claude.configure(highlightbackground=C["border"], highlightthickness=1)
        self.badge_claude.pack(side="left", padx=6, expand=True, fill="x")

        # 4. Gemini Farm :1081+
        self.badge_gemini = tk.Label(
            st_frame, text="Gemini Farm: ● ...", font=FONT_SUB,
            bg=C["card_inner"], fg=C["subtext"], padx=8, pady=4, bd=1, relief="solid"
        )
        self.badge_gemini.configure(highlightbackground=C["border"], highlightthickness=1)
        self.badge_gemini.pack(side="left", padx=(6, 0), expand=True, fill="x")

        # Панель кнопок действий
        btn_bar = tk.Frame(pad_aion, bg=C["card"])
        btn_bar.pack(fill="x")

        self.btn_integ_status = tk.Button(
            btn_bar, text=t("btn_aionui_status"), font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", activebackground="#b4befe",
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.on_integrations_status_click
        )
        self.btn_integ_status.pack(side="left", padx=(0, 6))

        self.btn_integ_sync_claude = tk.Button(
            btn_bar, text=t("btn_sync_claude"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["accent_peach"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.on_integrations_sync_claude_click
        )
        self.btn_integ_sync_claude.pack(side="left", padx=6)

        self.btn_integ_sync_gemini = tk.Button(
            btn_bar, text=t("btn_sync_gemini"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["accent_mauve"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.on_integrations_sync_gemini_click
        )
        self.btn_integ_sync_gemini.pack(side="left", padx=6)

        self.btn_integ_history = tk.Button(
            btn_bar, text=t("btn_view_logs"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.show_log_history_dialog
        )
        self.btn_integ_history.pack(side="right", padx=(6, 0))

        self.btn_ext_history = tk.Button(
            btn_bar, text=t("ext_logs_btn"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["accent_peach"], activebackground=C["border"],
            bd=0, padx=12, pady=6, cursor="hand2",
            command=self.show_extended_logs_dialog
        )
        self.btn_ext_history.pack(side="right", padx=(6, 6))

        self.btn_integ_clear_console = tk.Button(
            btn_bar, text=t("btn_clear_console"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["subtext"], activebackground=C["border"],
            bd=0, padx=10, pady=6, cursor="hand2",
            command=self.clear_integrations_console
        )
        self.btn_integ_clear_console.pack(side="right", padx=6)

        # ── Карточка 2: Консоль хода выполнения (Логи) ──────────────────────
        con_card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        con_card.configure(highlightbackground=C["border"], highlightthickness=1)
        con_card.pack(fill="both", expand=True)

        con_pad = tk.Frame(con_card, bg=C["card"])
        con_pad.pack(fill="both", expand=True, padx=16, pady=12)

        con_head = tk.Frame(con_pad, bg=C["card"])
        con_head.pack(fill="x", pady=(0, 8))

        tk.Label(
            con_head, text=t("lbl_console_title"), font=FONT_BOLD,
            fg=C["fg"], bg=C["card"]
        ).pack(side="left")

        self.lbl_log_stats = tk.Label(
            con_head,
            text=t("lbl_log_stats", file="integrations.log", size=integrations_manager.logger.get_file_size_str()),
            font=FONT_SUB, fg=C["subtext"], bg=C["card"]
        )
        self.lbl_log_stats.pack(side="right")

        # Окно консоли с прокруткой
        con_wrap = tk.Frame(con_pad, bg="#11111b", bd=1, relief="solid")
        con_wrap.configure(highlightbackground=C["border"], highlightthickness=1)
        con_wrap.pack(fill="both", expand=True)

        self.integ_console = tk.Text(
            con_wrap, bg="#11111b", fg=C["fg"], insertbackground=C["fg"],
            selectbackground=C["border"], font=FONT_MONO, wrap="word",
            relief="flat", bd=0, padx=10, pady=10, state="disabled"
        )
        sc_con = ttk.Scrollbar(con_wrap, orient="vertical", command=self.integ_console.yview)
        self.integ_console.configure(yscrollcommand=sc_con.set)

        self.integ_console.pack(side="left", fill="both", expand=True)
        sc_con.pack(side="right", fill="y")

        # Теги для подсветки синтаксиса логов
        self.integ_console.tag_config("TIMESTAMP", foreground="#6c7086")
        self.integ_console.tag_config("INFO", foreground="#cdd6f4")
        self.integ_console.tag_config("SUCCESS", foreground=C["green"])
        self.integ_console.tag_config("WARN", foreground=C["yellow"])
        self.integ_console.tag_config("ERROR", foreground=C["red"])
        self.integ_console.tag_config("STEP", foreground=C["accent_blue"], font=FONT_MONO_BOLD)
        self.integ_console.tag_config("SYSTEM", foreground=C["accent_mauve"])

        self._load_initial_console_logs()

    def _load_initial_console_logs(self):
        """Загрузка недавних логов при старте интерфейса."""
        if not hasattr(self, "integ_console") or not self.integ_console.winfo_exists():
            return
        hist = integrations_manager.logger.read_history()
        lines = hist.strip().splitlines()
        last_lines = lines[-40:] if len(lines) > 40 else lines
        self.integ_console.config(state="normal")
        self.integ_console.delete("1.0", "end")
        for line in last_lines:
            lvl = "INFO"
            if "[SUCCESS]" in line:
                lvl = "SUCCESS"
            elif "[WARN]" in line:
                lvl = "WARN"
            elif "[ERROR]" in line:
                lvl = "ERROR"
            elif "[STEP]" in line:
                lvl = "STEP"
            elif "[SYSTEM]" in line:
                lvl = "SYSTEM"
            self.integ_console.insert("end", line + "\n", lvl)
        self.integ_console.see("end")
        self.integ_console.config(state="disabled")
        self._update_log_stats_label()

    def _on_integration_log(self, ts: str, level: str, message: str):
        """Слушатель логов для динамического добавления в консоль."""
        def _ui_update():
            if not hasattr(self, "integ_console") or not self.integ_console.winfo_exists():
                return
            self.integ_console.config(state="normal")
            self.integ_console.insert("end", f"[{ts}] ", "TIMESTAMP")
            self.integ_console.insert("end", f"[{level}] ", level)
            self.integ_console.insert("end", f"{message}\n", level)
            self.integ_console.see("end")
            self.integ_console.config(state="disabled")
            self._update_log_stats_label()
        self.after(0, _ui_update)

    def _update_log_stats_label(self):
        """Обновление плашки размера файла логов."""
        if hasattr(self, "lbl_log_stats") and self.lbl_log_stats.winfo_exists():
            sz = integrations_manager.logger.get_file_size_str()
            self.lbl_log_stats.config(text=t("lbl_log_stats", file="integrations.log", size=sz))

    def clear_integrations_console(self):
        """Очищает экранную консоль GUI."""
        if hasattr(self, "integ_console") and self.integ_console.winfo_exists():
            self.integ_console.config(state="normal")
            self.integ_console.delete("1.0", "end")
            self.integ_console.config(state="disabled")
            integrations_manager.logger.log("Экранная консоль очищена (полный журнал сохранен в integrations.log)", "SYSTEM")

    def render_integrations_page(self):
        """Обновление страницы интеграций при переключении на неё."""
        self._update_log_stats_label()
        if not getattr(self, "_has_checked_integ_once", False):
            self._has_checked_integ_once = True
            self.on_integrations_status_click()

    def on_integrations_status_click(self):
        """Запуск комплексной проверки статуса AionUi, OmniRoute, Claude и Gemini."""
        if getattr(self, "_integ_task_running", False):
            return
        self._integ_task_running = True
        self.btn_integ_status.config(state="disabled", text="⏳ Проверка...")
        self.lbl_integ_overall.config(text="CHECKING...", bg=C["yellow"], fg="#11111b")

        def worker():
            try:
                res = integrations_manager.check_aionui_status()
            except Exception as e:
                integrations_manager.logger.log(f"Ошибка проверки статуса: {e}", "ERROR")
                res = {}
            def done():
                self._integ_task_running = False
                self.btn_integ_status.config(state="normal", text=t("btn_aionui_status"))
                self._apply_integrations_status(res)
            try:
                if not getattr(self, "_closing", False):
                    self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _apply_integrations_status(self, res: dict):
        """Обновление плашек статуса на основе диагностики."""
        if not hasattr(self, "badge_aionui") or not self.badge_aionui.winfo_exists():
            return
        # 1. AionUi
        aion = res.get("aionui", {})
        if aion.get("online"):
            self.badge_aionui.config(text="AionUi (:25808): ● ONLINE", fg=C["green"])
        else:
            self.badge_aionui.config(text="AionUi (:25808): ● OFFLINE", fg=C["red"])

        # 2. OmniRoute
        omni = res.get("omniroute", {})
        if omni.get("online"):
            self.badge_omniroute.config(text="OmniRoute (:20128): ● ONLINE", fg=C["green"])
        else:
            self.badge_omniroute.config(text="OmniRoute (:20128): ● OFFLINE", fg=C["red"])

        # 3. Claude
        claude = res.get("claude", {})
        c_socks = claude.get("socks5_1015", False)
        c_auth = claude.get("oauth", {}).get("authorized", False)
        if c_socks and c_auth:
            sub = claude.get("oauth", {}).get("subscription_type", "Pro").upper()
            self.badge_claude.config(text=f"Claude (:1015): ● {sub} OK", fg=C["green"])
        elif c_socks:
            self.badge_claude.config(text="Claude (:1015): ● NO AUTH", fg=C["yellow"])
        else:
            self.badge_claude.config(text="Claude (:1015): ● OFFLINE", fg=C["red"])

        # 4. Gemini
        gemini = res.get("gemini", {})
        act_ports = gemini.get("active_ports", [])
        tot_prof = gemini.get("total_profiles", 0)
        if act_ports:
            self.badge_gemini.config(text=f"Gemini Farm: ● {len(act_ports)}/{tot_prof} Online", fg=C["green"])
        elif tot_prof > 0:
            self.badge_gemini.config(text=f"Gemini Farm: ● 0/{tot_prof} Offline", fg=C["yellow"])
        else:
            self.badge_gemini.config(text="Gemini Farm: ● 0 Profiles", fg=C["subtext"])

        # Overall badge
        all_ok = aion.get("online") and omni.get("online") and c_socks and bool(act_ports)
        if all_ok:
            self.lbl_integ_overall.config(text="ACTIVE", bg=C["green"], fg="#11111b")
        else:
            self.lbl_integ_overall.config(text="PARTIAL", bg=C["yellow"], fg="#11111b")

    def on_integrations_sync_claude_click(self):
        """Диагностика Claude Code."""
        if getattr(self, "_integ_task_running", False):
            return
        self._integ_task_running = True
        self.btn_integ_sync_claude.config(state="disabled", text="⏳ Диагностика...")
        self.lbl_integ_overall.config(text="CLAUDE DIAGNOSTICS...", bg=C["accent_peach"], fg="#11111b")

        def worker():
            try:
                res = integrations_manager.sync_claude()
            except Exception as e:
                integrations_manager.logger.log(f"Критическая ошибка Claude: {e}", "ERROR")
                res = {"success": False, "error": str(e)}

            def done():
                self._integ_task_running = False
                self.btn_integ_sync_claude.config(state="normal", text=t("btn_sync_claude"))
                self.on_integrations_status_click()
                
                # Показываем таблицу
                if res.get("success"):
                    msg = "ОТЧЕТ ПО СТАТУСУ CLAUDE CODE\n"
                    msg += "-"*40 + "\n"
                    msg += f"Токен Claude (OAuth): {'АВТОРИЗОВАН' if res.get('auth') else 'ОТСУТСТВУЕТ'}\n"
                    msg += f"Сокет Claude SOCKS5 (1015): {'ONLINE' if res.get('socks') else 'OFFLINE'}\n"
                    msg += f"Связь с AionUi WebUI: {'ONLINE' if res.get('aion') else 'OFFLINE'}\n"
                    msg += "-"*40 + "\n"
                    msg += "ПРИМЕЧАНИЕ:\nДля активной привязки/настройки системы вызовите ИИ Агента (см. SKILLS.md)."
                    show_toast(self, "Диагностика Claude", msg)
                else:
                    messagebox.showwarning("Внимание", "Не удалось завершить диагностику. Подробности в логе.")

            try:
                if not getattr(self, "_closing", False):
                    self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
    def on_integrations_sync_gemini_click(self):
        """Диагностика Gemini Farm."""
        if getattr(self, "_integ_task_running", False):
            return
        self._integ_task_running = True
        self.btn_integ_sync_gemini.config(state="disabled", text="⏳ Диагностика...")
        self.lbl_integ_overall.config(text="GEMINI DIAGNOSTICS...", bg=C["accent_mauve"], fg="#11111b")

        def worker():
            try:
                res = integrations_manager.sync_gemini()
            except Exception as e:
                integrations_manager.logger.log(f"Критическая ошибка Gemini: {e}", "ERROR")
                res = {"success": False, "error": str(e)}

            def done():
                self._integ_task_running = False
                self.btn_integ_sync_gemini.config(state="normal", text=t("btn_sync_gemini"))
                self.on_integrations_status_click()
                
                if res.get("success"):
                    msg = "СВОДНАЯ ТАБЛИЦА GEMINI FARM\n"
                    msg += "-"*40 + "\n"
                    msg += f"Шлюз OmniRoute (20128): {'ONLINE' if res.get('omni_ok') else 'OFFLINE'}\n\n"
                    msg += "ПРОФИЛИ В СИСТЕМЕ HERDR:\n"
                    profs = res.get("profiles", [])
                    if profs:
                        for p in profs:
                            state = "🟢 Слушает" if p.get("alive") else "🔴 Офлайн"
                            msg += f"{p['name']} -> SOCKS {p['port']} [{state}]\n"
                    else:
                        msg += "Профили не найдены.\n"
                    msg += "-"*40 + "\n"
                    msg += "ПРИМЕЧАНИЕ:\nАктивная конфигурация маршрутизаторов делегирована ИИ-Агентам (см. SKILLS.md)."
                    show_toast(self, "Диагностика Gemini Farm", msg)
                else:
                    messagebox.showwarning("Внимание", "Не удалось завершить диагностику. Подробности в логе.")

            try:
                if not getattr(self, "_closing", False):
                    self.after(0, done)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def show_extended_logs_dialog(self):
        """Окно просмотра расширенной истории логов (extended.log)."""
        dlg = tk.Toplevel(self)
        dlg.title(t("ext_logs_title"))
        dlg.geometry("900x600")
        dlg.minsize(800, 500)
        dlg.configure(bg=C["bg"])

        top_f = tk.Frame(dlg, bg=C["bg"])
        top_f.pack(fill="x", padx=16, pady=(14, 8))

        tk.Label(
            top_f, text=t("ext_logs_btn"), font=FONT_TITLE,
            fg=C["accent_peach"], bg=C["bg"]
        ).pack(side="left")

        import extended_logger
        size_bytes = 0
        if extended_logger.EXTENDED_LOG_FILE.exists():
            size_bytes = extended_logger.EXTENDED_LOG_FILE.stat().st_size
        size_str = f"{size_bytes / 1024:.1f} KB"

        dlg_stats_lbl = tk.Label(
            top_f, text=t("ext_logs_file", size=size_str),
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        )
        dlg_stats_lbl.pack(side="right")

        card_wrap = tk.Frame(dlg, bg=C["card"], bd=1, relief="solid")
        card_wrap.configure(highlightbackground=C["border"], highlightthickness=1)
        card_wrap.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        scrollbar = tk.Scrollbar(card_wrap)
        scrollbar.pack(side="right", fill="y")
        
        tv = tk.Text(
            card_wrap, bg=C["card_inner"], fg=C["fg"], yscrollcommand=scrollbar.set,
            font=FONT_MONO, bd=0, padx=8, pady=8, state="normal"
        )
        tv.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        scrollbar.config(command=tv.yview)

        if extended_logger.EXTENDED_LOG_FILE.exists():
            try:
                with open(extended_logger.EXTENDED_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    tv.insert("end", f.read())
            except Exception as e:
                tv.insert("end", t("ext_logs_error").format(e))
        else:
            tv.insert("end", t("ext_logs_empty"))
            
        tv.see("end")
        tv.config(state="disabled")

    def show_log_history_dialog(self):
        """Окно просмотра истории логов с синхронизацией из файла."""
        dlg = tk.Toplevel(self)
        dlg.title(t("dlg_log_history_title"))
        dlg.geometry("860x580")
        dlg.minsize(740, 480)
        dlg.configure(bg=C["bg"])

        top_f = tk.Frame(dlg, bg=C["bg"])
        top_f.pack(fill="x", padx=16, pady=(14, 8))

        tk.Label(
            top_f, text=t("dlg_log_history_title"), font=FONT_TITLE,
            fg=C["accent_blue"], bg=C["bg"]
        ).pack(side="left")

        dlg_stats_lbl = tk.Label(
            top_f,
            text=t("lbl_log_stats", file="integrations.log", size=integrations_manager.logger.get_file_size_str()),
            font=FONT_SUB, fg=C["subtext"], bg=C["bg"]
        )
        dlg_stats_lbl.pack(side="right")

        # Панель кнопок управления логами
        bar = tk.Frame(dlg, bg=C["bg"])
        bar.pack(fill="x", padx=16, pady=(0, 8))

        card_wrap = tk.Frame(dlg, bg=C["card"], bd=1, relief="solid")
        card_wrap.configure(highlightbackground=C["border"], highlightthickness=1)
        card_wrap.pack(fill="both", expand=True, padx=16, pady=(0, 14))

        txt = tk.Text(
            card_wrap, bg="#11111b", fg=C["fg"], insertbackground=C["fg"],
            selectbackground=C["border"], font=FONT_MONO, wrap="word",
            relief="flat", bd=0, padx=10, pady=10
        )
        sc = ttk.Scrollbar(card_wrap, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sc.set)
        txt.pack(side="left", fill="both", expand=True)
        sc.pack(side="right", fill="y")

        txt.tag_config("TIMESTAMP", foreground="#6c7086")
        txt.tag_config("INFO", foreground="#cdd6f4")
        txt.tag_config("SUCCESS", foreground=C["green"])
        txt.tag_config("WARN", foreground=C["yellow"])
        txt.tag_config("ERROR", foreground=C["red"])
        txt.tag_config("STEP", foreground=C["accent_blue"], font=FONT_MONO_BOLD)
        txt.tag_config("SYSTEM", foreground=C["accent_mauve"])

        def load_content():
            content = integrations_manager.logger.read_history()
            txt.config(state="normal")
            txt.delete("1.0", "end")
            for line in content.splitlines():
                lvl = "INFO"
                if "[SUCCESS]" in line:
                    lvl = "SUCCESS"
                elif "[WARN]" in line:
                    lvl = "WARN"
                elif "[ERROR]" in line:
                    lvl = "ERROR"
                elif "[STEP]" in line:
                    lvl = "STEP"
                elif "[SYSTEM]" in line:
                    lvl = "SYSTEM"
                txt.insert("end", line + "\n", lvl)
            txt.see("end")
            txt.config(state="disabled")
            sz = integrations_manager.logger.get_file_size_str()
            dlg_stats_lbl.config(text=t("lbl_log_stats", file="integrations.log", size=sz))

        def copy_all():
            content = integrations_manager.logger.read_history()
            self.clipboard_clear()
            self.clipboard_append(content)
            messagebox.showinfo(t("app_name"), t("msg_copied_clipboard"), parent=dlg)

        def clear_file():
            if messagebox.askyesno(t("app_name"), t("msg_confirm_clear_log"), parent=dlg):
                integrations_manager.logger.clear_history()
                load_content()
                self._update_log_stats_label()
                messagebox.showinfo(t("app_name"), t("msg_log_cleared"), parent=dlg)

        btn_reload = tk.Button(
            bar, text=t("btn_reload_log"), font=FONT_BOLD,
            bg=C["accent_blue"], fg="#11111b", activebackground="#b4befe",
            bd=0, padx=12, pady=5, cursor="hand2", command=load_content
        )
        btn_reload.pack(side="left", padx=(0, 6))

        btn_copy = tk.Button(
            bar, text=t("btn_copy_all"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["fg"], activebackground=C["border"],
            bd=0, padx=12, pady=5, cursor="hand2", command=copy_all
        )
        btn_copy.pack(side="left", padx=6)

        btn_clear = tk.Button(
            bar, text=t("btn_clear_log_file"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["red"], activebackground=C["border"],
            bd=0, padx=12, pady=5, cursor="hand2", command=clear_file
        )
        btn_clear.pack(side="left", padx=6)

        btn_close = tk.Button(
            bar, text=t("btn_close_dialog"), font=FONT_MAIN,
            bg=C["card_inner"], fg=C["subtext"], activebackground=C["border"],
            bd=0, padx=12, pady=5, cursor="hand2", command=dlg.destroy
        )
        btn_close.pack(side="right")

        load_content()

    # =========================================================================
    # ОБЩАЯ ЛОГИКА И СТАТУС
    # =========================================================================
    def on_global_refresh(self):
        """Кнопка 'Проверить всё'."""
        token_vault_manager.ensure_locked()
        self.refresh_routes_async()
        if self.active_tab == "proxy":
            self.check_current_proxy_page_async()
        elif self.active_tab == "gemini":
            self.refresh_gemini_profiles_async()
        elif self.active_tab == "strategy":
            self.render_strategy_page()
        elif self.active_tab == "backup":
            self.render_backup_page()
        elif self.active_tab == "integrations":
            self.on_integrations_status_click()

    def refresh_routes_async(self):
        """Запуск фоновой проверки маршрутов."""
        if self.is_checking_routes:
            return

        self.is_checking_routes = True
        self.refresh_btn.config(state="disabled", text="⏳ Проверка...")
        self.quick_status.config(text="● Опрос WSL2 и маршрутов...", fg=C["yellow"])

        def worker():
            try:
                res = sync_manager.check_all_routes()
                if not getattr(self, "_closing", False):
                    self.after(0, self._apply_route_results, res)
            except Exception as e:
                err_str = str(e)
                def _handle_err():
                    self.is_checking_routes = False
                    self.refresh_btn.config(state="normal", text=t("btn_check_all"))
                    self.quick_status.config(text=f"✕ Ошибка проверки: {err_str}", fg=C["red"])
                try:
                    if not getattr(self, "_closing", False):
                        self.after(0, _handle_err)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _apply_route_results(self, res: dict):
        self.is_checking_routes = False
        self.refresh_btn.config(state="normal", text=t("btn_check_all"))
        self.last_check_lbl.config(text=t("lbl_last_check", time=res.get("timestamp", "--:--:--")))

        gemini = res.get("gemini", {})
        claude = res.get("claude", {})

        # 2. Gemini
        active_port = gemini.get("port", gemini_manager.BASE_SOCKS5_PORT)
        active_route = gemini.get("route", f"socks5h://127.0.0.1:{active_port}")
        self.gemini_route_lbl.config(text=f"{active_route} ({t('pill_socks5')})")

        if gemini.get("failback_occurred"):
            fb_msg = gemini.get("failback_msg", "Auto-failback to bound proxy")
            lat = gemini.get("latency_ms")
            lat_str = f" ({lat} ms)" if lat else ""
            self.gemini_status_lbl.config(
                text=f"✓ {fb_msg}",
                fg=C["green"]
            )
            self.gemini_ping_lbl.config(
                text=f"SOCKS5 :{active_port}{lat_str}",
                fg=C["green"]
            )
            self.load_gemini_profiles_data()
            self.load_proxies_data()
            if self.active_tab == "gemini":
                self.render_gemini_page()
            elif self.active_tab == "proxy":
                self.render_proxy_table()
        elif gemini.get("failover_occurred"):
            fo_msg = gemini.get("failover_msg", "Auto-failover triggered")
            lat = gemini.get("latency_ms")
            lat_str = f" ({lat} ms)" if lat else ""
            self.gemini_status_lbl.config(
                text=f"🔄 {fo_msg}",
                fg=C["accent_peach"]
            )
            self.gemini_ping_lbl.config(
                text=f"SOCKS5 :{active_port}{lat_str}",
                fg=C["green"] if gemini.get("online") else C["red"]
            )
            # Обновляем профили и прокси в интерфейсе, чтобы отразить новый порт
            self.load_gemini_profiles_data()
            self.load_proxies_data()
            if self.active_tab == "gemini":
                self.render_gemini_page()
            elif self.active_tab == "proxy":
                self.render_proxy_table()
        elif gemini.get("in_failover"):
            target_port = gemini.get("bound_port", gemini_manager.BASE_SOCKS5_PORT)
            rem_s = gemini.get("next_failback_sec", 0)
            lat = gemini.get("latency_ms")
            lat_str = f" ({lat} ms)" if lat else ""
            self.gemini_status_lbl.config(
                text=f"🔄 Failover :{active_port} ➔ Стремится к :{target_port} (возврат через {rem_s}с)",
                fg=C["accent_peach"]
            )
            self.gemini_ping_lbl.config(
                text=f"SOCKS5 :{active_port}{lat_str}",
                fg=C["green"] if gemini.get("online") else C["red"]
            )
        elif gemini.get("online"):
            self.gemini_status_lbl.config(
                text=t("route_gemini_active", port=active_port),
                fg=C["green"]
            )
            lat = gemini.get("latency_ms")
            self.gemini_ping_lbl.config(
                text=t("route_gemini_ping", port=active_port, lat=lat),
                fg=C["green"]
            )
        else:
            self.gemini_status_lbl.config(
                text=f"✕ {gemini.get('status_text')}",
                fg=C["red"]
            )
            self.gemini_ping_lbl.config(
                text=t("route_gemini_offline", port=active_port),
                fg=C["red"]
            )

        # 3. Claude
        self._last_claude_accessible = claude.get("online", False)
        self._apply_claude_route_ui(claude)

        # 4. Общий статус
        if gemini.get("online") and claude.get("online"):
            self.quick_status.config(text=t("status_routes_ok"), fg=C["green"])
        else:
            self.quick_status.config(text=t("status_monitoring_ok"), fg=C["yellow"])

    def _apply_claude_route_ui(self, claude: dict):
        """Обновляет элементы интерфейса маршрута Claude Code."""
        c_host = claude.get("host", settings_manager.get_claude_proxy_host())
        c_port = claude.get("port", settings_manager.get_claude_proxy_port())
        c_http_port = claude.get("http_port", 10000 + c_port)
        ks_engaged = claude.get("killswitch_engaged", False)
        ks_enabled = claude.get("killswitch_enabled", True)

        if hasattr(self, "claude_host_var") and c_host:
            self.claude_host_var.set(str(c_host))
        if hasattr(self, "claude_port_var") and c_port:
            self.claude_port_var.set(str(c_port))
        self._update_claude_proxy_combo()

        if hasattr(self, "claude_ks_cb"):
            self.claude_ks_cb.config(fg=C["red"] if ks_enabled else C["subtext"])

        if claude.get("online"):
            if hasattr(self, "claude_pill"):
                self.claude_pill.config(text=t("pill_claude_proxy"), bg=C["green"], fg="#11111b")
            self.claude_status_lbl.config(
                text=claude.get("status_text", t("route_claude_active", host=c_host, port=c_port)),
                fg=C["green"]
            )
            ip_str = claude.get("ip", "-")
            co_str = claude.get("country", "")
            lbl_ip = f"{ip_str} ({co_str})" if co_str and co_str != "undefined" else ip_str
            self.claude_ip_lbl.config(text=t("route_claude_ip", ip=lbl_ip), fg=C["accent_peach"])
            if hasattr(self, "claude_route_lbl"):
                self.claude_route_lbl.config(text=f"http://{c_host}:{c_http_port} (SOCKS5 :{c_port})")
            lat = claude.get("latency_ms")
            self.claude_ping_lbl.config(
                text=t("route_claude_ping", lat=lat),
                fg=C["green"]
            )
        elif ks_engaged:
            if hasattr(self, "claude_pill"):
                self.claude_pill.config(text=t("pill_killswitch_engaged"), bg=C["red"], fg="#ffffff")
            self.claude_status_lbl.config(
                text=claude.get("status_text", t("route_claude_killswitch_active", host=c_host, port=c_port)),
                fg=C["red"]
            )
            self.claude_ip_lbl.config(text=t("claude_safe_killswitch"), fg=C["red"])
            if hasattr(self, "claude_route_lbl"):
                self.claude_route_lbl.config(text=f"http://{c_host}:{c_http_port} [KILLSWITCH BLOCK]")
            self.claude_ping_lbl.config(text="Ожидание восстановления прокси...", fg=C["red"])
        else:
            if hasattr(self, "claude_pill"):
                self.claude_pill.config(text="✕ ОФЛАЙН", bg=C["subtext"], fg="#ffffff")
            self.claude_status_lbl.config(
                text=claude.get("status_text", f"✕ {t('route_claude_offline')}"),
                fg=C["red"]
            )
            self.claude_ip_lbl.config(text=claude.get("ip", "-"), fg=C["subtext"])
            if hasattr(self, "claude_route_lbl"):
                self.claude_route_lbl.config(text=f"http://{c_host}:{c_http_port}")
            self.claude_ping_lbl.config(text=t("route_claude_offline"), fg=C["red"])

    def _schedule_fast_port_check(self):
        """Регулярный быстрый опрос локальных SOCKS5-портов и мгновенное управление Killswitch."""
        if self._closing:
            return

        interval_sec = settings_manager.get_port_check_interval()
        interval_ms = int(interval_sec * 1000)

        if not self._is_checking_fast_ports:
            self._is_checking_fast_ports = True

            def worker():
                claude_res = None
                ports_changed = False
                try:
                    # 1. Быстрый опрос статуса локальных портов из proxies.json
                    ports_changed = proxy_manager.refresh_local_ports_status(self.proxies, timeout=0.15)
                    if ports_changed:
                        proxy_manager.save_proxies(self.proxies)

                    # 2. Быстрая проверка целевого порта Claude Code
                    c_host = settings_manager.get_claude_proxy_host()
                    c_port = settings_manager.get_claude_proxy_port()
                    c_ks = settings_manager.get_claude_killswitch()
                    port_ok = claude_manager.check_port_accessible(c_host, c_port, timeout=0.15)

                    if self._last_claude_accessible != port_ok:
                        self._last_claude_accessible = port_ok
                        claude_res = claude_manager.probe_claude_route(
                            host=c_host, port=c_port, killswitch=c_ks, fast=True
                        )
                except Exception:
                    pass
                finally:
                    self._is_checking_fast_ports = False

                if not self._closing:
                    self.after(0, self._apply_fast_port_results, claude_res, ports_changed)

            threading.Thread(target=worker, daemon=True).start()

        if not self._closing:
            self.after(interval_ms, self._schedule_fast_port_check)

    def _apply_fast_port_results(self, claude_res: dict | None, ports_changed: bool):
        if self._closing:
            return
        if ports_changed and self.active_tab == "proxy":
            self.render_proxy_table()
        if claude_res is not None:
            self._apply_claude_route_ui(claude_res)

    def _schedule_auto_refresh(self):
        if self.auto_refresh_enabled.get() and not self.is_checking_routes:
            self.refresh_routes_async()

        # Фоновая проверка планового автобэкапа
        try:
            threading.Thread(target=backup_manager.check_and_run_auto_backup, daemon=True).start()
        except Exception:
            pass

        self.after(15000, self._schedule_auto_refresh)

    # ── Системный трей ──────────────────────────────────────────
    def _init_tray(self):
        if not HAS_TRAY:
            return

        icon_img = make_tray_icon("blue")
        menu = pystray.Menu(
            pystray.MenuItem(t("tray_show"), self.show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_exit"), self.quit_app)
        )
        self.tray_icon = pystray.Icon("herdr_router", icon_img, t("app_name"), menu)

        try:
            self.tray_icon.run_detached()
        except Exception:
            pass

    def on_close_button(self):
        if HAS_TRAY:
            if not self.tray_icon:
                self._init_tray()
            self.withdraw()

        else:
            self.destroy()

    def bring_to_front(self):
        try:
            self.deiconify()
            self.state("normal")
            self.lift()
            self.attributes("-topmost", True)
            self.after_idle(self.attributes, "-topmost", False)
            self.focus_force()
        except Exception:
            pass

    def show_window(self, icon=None, item=None):
        self.after(0, self.bring_to_front)

    def quit_app(self, icon=None, item=None):
        self.after(0, self.destroy)

    def destroy(self):
        self._closing = True
        if getattr(self, "aiwatcher", None) is not None:
            try:
                self.aiwatcher.stop(timeout=1.0)
            except Exception:
                pass
        if getattr(self, "_aiw_poll_id", None):
            try:
                self.after_cancel(self._aiw_poll_id)
            except Exception:
                pass
        if hasattr(self, "_on_integration_log_cb"):
            try:
                integrations_manager.logger.remove_listener(self._on_integration_log_cb)
            except Exception:
                pass
        if hasattr(self, "tray_icon") and self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        super().destroy()
_INSTANCE_SOCKET = None


def _acquire_instance_lock() -> bool:
    """Захватывает сокет единственного экземпляра приложения на порту SINGLE_INSTANCE_PORT (38123)."""
    global _INSTANCE_SOCKET
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(5)
        _INSTANCE_SOCKET = s
        return True
    except OSError:
        return False


def _release_instance_lock() -> None:
    """Освобождает сокет приложения."""
    global _INSTANCE_SOCKET
    if _INSTANCE_SOCKET is not None:
        try:
            _INSTANCE_SOCKET.close()
        except Exception:
            pass
        _INSTANCE_SOCKET = None


def start_instance_server(app: HerdrConfigApp):
    """Слушает входящие сигналы SHOW на уже захваченном сокете для передачи фокуса главному окну."""
    global _INSTANCE_SOCKET
    if _INSTANCE_SOCKET is None:
        return

    def server_loop():
        while _INSTANCE_SOCKET:
            try:
                conn, _ = _INSTANCE_SOCKET.accept()
                with conn:
                    try:
                        data = conn.recv(1024)
                        if b"SHOW" in data:
                            app.after(0, app.bring_to_front)
                    except Exception:
                        pass
            except Exception:
                break

    t = threading.Thread(target=server_loop, daemon=True)
    t.start()


def _show_already_running_notice(duration_ms: int = 1200) -> None:
    """Отображает окно уведомления о том, что программа уже запущена, на duration_ms миллисекунд и закрывается."""
    try:
        root = tk.Tk()
        root.title("Herdr Control Center")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#1e1e2e")

        frame = tk.Frame(root, bg="#1e1e2e", highlightbackground="#89b4fa", highlightthickness=2, padx=24, pady=16)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="⚠️  " + t("already_running"),
            font=("Segoe UI", 11, "bold"),
            fg="#cdd6f4",
            bg="#1e1e2e"
        ).pack()

        root.update_idletasks()
        w = root.winfo_reqwidth()
        h = root.winfo_reqheight()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")

        root.after(duration_ms, root.destroy)
        root.mainloop()
    except Exception:
        pass


def main():
    print(f"[{time.strftime('%X')}] main() start PID={os.getpid()}", flush=True)
    if not _acquire_instance_lock():
        print(f"[{time.strftime('%X')}] PID={os.getpid()} _acquire_instance_lock failed! Already running.", flush=True)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                s.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
                s.sendall(b"SHOW\n")
        except Exception:
            pass
        _show_already_running_notice(1200)
        return 0

    print(f"[{time.strftime('%X')}] PID={os.getpid()} lock acquired, initializing HerdrConfigApp", flush=True)
    try:
        app = HerdrConfigApp()
        # Встроенный aiWatcher (сторож AionUi / OmniRoute в WSL2) запускается только в настоящем приложении,
        # а не при создании окна в тестах: иначе тесты запускали бы сервисы в WSL.
        app.aiwatcher.start()
        print(f"[{time.strftime('%X')}] PID={os.getpid()} HerdrConfigApp initialized, starting server", flush=True)
        start_instance_server(app)
        print(f"[{time.strftime('%X')}] PID={os.getpid()} entering mainloop", flush=True)
        app.mainloop()
        print(f"[{time.strftime('%X')}] PID={os.getpid()} mainloop exited cleanly", flush=True)
    except BaseException:
        print(f"[{time.strftime('%X')}] PID={os.getpid()} EXCEPTION in mainloop:", flush=True)
        traceback.print_exc()
        with open(BASE_DIR / "crash.log", "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        raise
    finally:
        _release_instance_lock()


if __name__ == "__main__":
    main()
