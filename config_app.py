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

import os
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path
import traceback

BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# Защита от сбоев в pythonw
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

import sync_manager
import proxy_manager
import gemini_manager
import settings_manager
import strategy_manager
import backup_manager
import i18n
from i18n import t

try:
    from PIL import Image, ImageDraw
    import pystray
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

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
    "green":       "#a6e3a1",
    "red":         "#f38ba8",
    "yellow":      "#f9e2af",
    "border":      "#313244",
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

SINGLE_INSTANCE_PORT = 38123


def make_tray_icon():
    """Генерация стильной иконки 'H' для трея."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=14, fill="#1e1e2e", outline="#89b4fa", width=3)
    d.rectangle([16, 14, 24, 50], fill="#89b4fa")
    d.rectangle([40, 14, 48, 50], fill="#89b4fa")
    d.rectangle([24, 28, 40, 36], fill="#89b4fa")
    return img


class HerdrConfigApp(tk.Tk):
    def __init__(self):
        super().__init__()
        i18n.init_language()
        self.title(t("app_title"))
        self.geometry("920x720")
        self.minsize(860, 640)
        self.configure(bg=C["bg"])

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
        self.backup_password_var = tk.StringVar(value=backup_manager.load_backup_password())
        self.backup_dir_var = tk.StringVar(value=b_cfg.get("backup_dir", str(backup_manager.DEFAULT_BACKUP_DIR)))
        self.backup_interval_var = tk.IntVar(value=b_cfg.get("backup_interval_hours", 12))
        self.auto_backup_enabled_var = tk.BooleanVar(value=b_cfg.get("auto_backup_enabled", True))
        self.backup_pw_show = False

        self.backup_password_var.trace_add("write", self._on_backup_password_changed)
        self.backup_dir_var.trace_add("write", lambda *_: backup_manager.update_backup_config("backup_dir", self.backup_dir_var.get()))
        self.backup_interval_var.trace_add("write", lambda *_: backup_manager.update_backup_config("backup_interval_hours", self.backup_interval_var.get()))
        self.auto_backup_enabled_var.trace_add("write", lambda *_: backup_manager.update_backup_config("auto_backup_enabled", self.auto_backup_enabled_var.get()))

        # Состояние прокси
        self.proxies: list[dict] = []
        self.current_proxy_page = 1
        self.is_checking_proxies = False

        # Состояние Gemini OAuth
        self.gemini_profiles: list[dict] = []
        self.is_checking_gemini = False

        self._build_ui()
        if HAS_TRAY:
            self._init_tray()

        # Первоначальная загрузка данных
        self.load_proxies_data()
        self.load_gemini_profiles_data()

        # Запуск проверки маршрутов
        self.after(300, self.refresh_routes_async)

        # Автообновление каждые 30 секунд
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
            ("strategy", t("tab_strategy")),
            ("backup", t("tab_backup")),
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
        self.page_strategy = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_backup = tk.Frame(self.pages_container, bg=C["bg"])
        self.page_localization = tk.Frame(self.pages_container, bg=C["bg"])

        self._build_page_routes(self.page_routes)
        self._build_page_proxy(self.page_proxy)
        self._build_page_gemini(self.page_gemini)
        self._build_page_strategy(self.page_strategy)
        self._build_page_backup(self.page_backup)
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
        for p in (self.page_routes, self.page_proxy, self.page_gemini, self.page_strategy, self.page_backup, self.page_localization):
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
        elif page_id == "proxy":
            self.page_proxy.pack(fill="both", expand=True)
            self.render_proxy_table()
        elif page_id == "gemini":
            self.page_gemini.pack(fill="both", expand=True)
            self.render_gemini_page()
        elif page_id == "strategy":
            self.page_strategy.pack(fill="both", expand=True)
            self.render_strategy_page()
        elif page_id == "backup":
            self.page_backup.pack(fill="both", expand=True)
            self.render_backup_page()
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
        # ── Карточка 1: Herdr & Агенты в WSL2 ───────────────────
        self.herdr_card = self._build_herdr_card(parent)
        self.herdr_card.pack(fill="x", pady=(0, 10))

        # ── Карточка 2: Google Gemini (SOCKS5) ──────────────────
        self.gemini_card = self._build_gemini_card(parent)
        self.gemini_card.pack(fill="x", pady=(0, 10))

        # ── Карточка 3: Anthropic Claude (Оригинальный IP) ──────
        self.claude_card = self._build_claude_card(parent)
        self.claude_card.pack(fill="x")

    def _build_herdr_card(self, parent: tk.Frame) -> tk.Frame:
        card = tk.Frame(parent, bg=C["card"], bd=1, relief="solid")
        card.configure(highlightbackground=C["border"], highlightthickness=1)

        pad = tk.Frame(card, bg=C["card"])
        pad.pack(fill="both", expand=True, padx=16, pady=10)

        head = tk.Frame(pad, bg=C["card"])
        head.pack(fill="x", pady=(0, 6))

        tk.Label(head, text=t("herdr_title"), font=FONT_TITLE, fg=C["fg"], bg=C["card"]).pack(side="left")

        self.herdr_badge = tk.Label(
            head, text=t("herdr_badge_waiting"), font=FONT_BOLD,
            bg=C["card_inner"], fg=C["subtext"], padx=8, pady=2
        )
        self.herdr_badge.pack(side="right")

        self.panes_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        self.panes_frame.configure(highlightbackground=C["border"], highlightthickness=1)
        self.panes_frame.pack(fill="x", pady=(4, 0))

        self.panes_inner = tk.Frame(self.panes_frame, bg=C["card_inner"])
        self.panes_inner.pack(fill="x", padx=12, pady=6)

        self.herdr_panes_lbl = tk.Label(
            self.panes_inner,
            text=t("herdr_reading_panes"),
            font=FONT_MAIN, fg=C["subtext"], bg=C["card_inner"], anchor="w", justify="left"
        )
        self.herdr_panes_lbl.pack(fill="x")

        return card

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

        pill = tk.Label(
            head, text=t("pill_direct"), font=FONT_BOLD,
            bg=C["accent_peach"], fg="#11111b", padx=8, pady=2
        )
        pill.pack(side="right")

        self.claude_status_lbl = tk.Label(
            pad, text=t("claude_checking"), font=FONT_MAIN,
            fg=C["yellow"], bg=C["card"], anchor="w"
        )
        self.claude_status_lbl.pack(fill="x", pady=(0, 4))

        details_frame = tk.Frame(pad, bg=C["card_inner"], bd=1, relief="solid")
        details_frame.configure(highlightbackground=C["border"], highlightthickness=1)
        details_frame.pack(fill="x", pady=(2, 0))

        d_in = tk.Frame(details_frame, bg=C["card_inner"])
        d_in.pack(fill="x", padx=12, pady=6)

        r1 = tk.Frame(d_in, bg=C["card_inner"])
        r1.pack(fill="x", pady=1)
        tk.Label(r1, text="Маршрут:", font=FONT_BOLD, fg=C["subtext"], bg=C["card_inner"], width=14, anchor="w").pack(side="left")
        tk.Label(r1, text=t("route_direct_val"), font=FONT_MAIN, fg=C["fg"], bg=C["card_inner"]).pack(side="left")

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
            ("СТАТУС", 12),
            ("ХОСТ : ПОРТ", 22),
            ("ВНЕШНИЙ IP", 18),
            ("СТРАНА", 20),
            ("ОТКЛИК", 12),
            ("ДЕЙСТВИЯ", 16),
        ]
        for h_text, h_width in headers:
            align = "center" if h_text in ("СТАТУС", "ДЕЙСТВИЯ", "ОТКЛИК") else "w"
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
        """Загрузка списка прокси из файла."""
        self.proxies = proxy_manager.load_proxies()

    def render_proxy_table(self):
        """Отрисовка 10 строк текущей страницы."""
        for widget in self.proxy_rows_container.winfo_children():
            widget.destroy()

        page_items, total_pages = proxy_manager.get_paginated_proxies(
            self.proxies, self.current_proxy_page, proxy_manager.PAGE_SIZE
        )

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
                fg=st_color, bg=bg_color, width=12, anchor="center"
            ).pack(side="left", padx=4)

            # 2. Хост:Порт
            host_port = f"{item.get('host', '127.0.0.1')}:{item.get('port', 1081)}"
            tk.Label(
                row, text=host_port, font=FONT_MONO_BOLD,
                fg=C["fg"], bg=bg_color, width=22, anchor="w"
            ).pack(side="left", padx=4)

            # 3. IP
            ip_str = item.get("ip") or "-"
            tk.Label(
                row, text=ip_str, font=FONT_MONO,
                fg=C["accent_peach"] if ip_str != "-" else C["subtext"],
                bg=bg_color, width=18, anchor="w"
            ).pack(side="left", padx=4)

            # 4. Страна (при невозможности определить — строго undefined!)
            country_str = item.get("country") or "undefined"
            c_color = C["tag_fg"] if country_str != "undefined" else C["yellow"]
            tk.Label(
                row, text=country_str, font=FONT_MAIN,
                fg=c_color, bg=bg_color, width=20, anchor="w"
            ).pack(side="left", padx=4)

            # 5. Пинг
            lat = item.get("latency_ms")
            lat_str = f"{lat} ms" if lat is not None else "-"
            tk.Label(
                row, text=lat_str, font=FONT_MONO,
                fg=C["subtext"], bg=bg_color, width=12, anchor="center"
            ).pack(side="left", padx=4)

            # 6. Действия
            actions = tk.Frame(row, bg=bg_color, width=16)
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

    def render_gemini_page(self):
        """Отрисовка карточек аккаунтов Gemini."""
        # 1. Обновляем карточку текущего активного аккаунта
        active_prof = next((p for p in self.gemini_profiles if p.get("is_active")), None)
        if active_prof:
            port = active_prof.get("port", 1081)
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
            port = prof.get("port", 1081)
            p_st = prof.get("proxy_status", "unknown")
            p_ip = prof.get("proxy_ip", "-")
            p_co = prof.get("proxy_country", "undefined")

            card = tk.Frame(self.gemini_cards_container, bg=C["card"], bd=1, relief="solid")
            border_color = C["accent_blue"] if is_active else C["border"]
            card.configure(highlightbackground=border_color, highlightthickness=1)
            card.pack(fill="x", pady=(0, 8))

            c_in = tk.Frame(card, bg=C["card"])
            c_in.pack(fill="both", expand=True, padx=14, pady=10)

            # Левая колонка: Инфо
            left = tk.Frame(c_in, bg=C["card"])
            left.pack(side="left", fill="both", expand=True)

            t_line = tk.Frame(left, bg=C["card"])
            t_line.pack(fill="x")

            tk.Label(
                t_line, text=email, font=FONT_TITLE,
                fg=C["fg"], bg=C["card"]
            ).pack(side="left")

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

            # Бейдж персонального порта
            port_badge = tk.Label(
                t_line, text=f" SOCKS5 :{port} ", font=FONT_SUB,
                fg=C["accent_mauve"], bg=C["tag_bg"], padx=6, pady=1
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

            tk.Label(
                sub_line,
                text=f"Имя: {user_name}  •  Профиль: {p_name}  •  Токен: {expiry}  •  Шлюз: {st_badge}",
                font=FONT_MAIN, fg=C["subtext"], bg=C["card"]
            ).pack(side="left")

            # Правая колонка: Кнопки
            right = tk.Frame(c_in, bg=C["card"])
            right.pack(side="right")

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

            if not is_active and len(self.gemini_profiles) > 1:
                btn_del = tk.Button(
                    right, text="🗑️", font=FONT_MAIN,
                    bg=C["card_inner"], fg=C["red"], activebackground=C["border"],
                    bd=0, padx=6, pady=4, cursor="hand2",
                    command=lambda name=p_name: self.on_delete_gemini_profile(name)
                )
                btn_del.pack(side="left")

    def on_switch_gemini_profile(self, profile_name: str):
        """Быстрое переключение активного профиля в 1 клик."""
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
                port = res.get("port", 1081)
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
        suggested_name = f"account-{len(self.gemini_profiles) + 1}"
        next_port = 1081 + len(self.gemini_profiles)
        p_name = simpledialog.askstring(
            "Новый Google аккаунт",
            f"Будет привязан новый порт: SOCKS5 127.0.0.1:{next_port}\n\nВведите имя профиля:",
            initialvalue=suggested_name,
            parent=self
        )
        if not p_name:
            return

        launched = gemini_manager.launch_add_account_terminal(p_name.strip())
        if launched:
            messagebox.showinfo(
                "Авторизация в браузере",
                f"Открыт терминал авторизации для '{p_name}'.\nВыделенный порт: SOCKS5 :{next_port}\n\n"
                f"1. Перейдите по ссылке в появившемся окне.\n"
                f"2. Войдите в нужный аккаунт Google в браузере.\n"
                f"3. После завершения нажмите 'Обновить профили' здесь."
            )
        else:
            messagebox.showerror("Ошибка", "Не удалось запустить терминал WSL.")

    def on_delete_gemini_profile(self, profile_name: str):
        """Удаление резервного профиля."""
        if messagebox.askyesno("Удаление профиля", f"Удалить сохраненный профиль '{profile_name}'?"):
            ok, msg = gemini_manager.delete_profile(profile_name)
            if ok:
                self.load_gemini_profiles_data()
                self.render_gemini_page()
            else:
                messagebox.showerror("Ошибка", msg)

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
            self.load_gemini_profiles_data()
            self.after(0, self.render_gemini_page)

        threading.Thread(target=worker, daemon=True).start()

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
            curr_port = prof.get("port", 1081)
            curr_ip = prof.get("proxy_ip", "-")
            curr_co = prof.get("proxy_country", "undefined")
            is_active = prof.get("is_active", False)

            eval_res = strategy_manager.evaluate_account_strategy(email, curr_co, tendencies)
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
            tk.Label(
                t_row, text=f"{act_icon} {email}", font=FONT_TITLE,
                fg=C["fg"], bg=C["card"]
            ).pack(side="left")

            tk.Label(
                t_row, text=f" {p_name} ", font=FONT_SUB,
                fg=C["subtext"], bg=C["card_inner"], padx=6, pady=1
            ).pack(side="left", padx=(8, 0))

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

            tk.Label(
                d_row,
                text=f"Текущий туннель: 127.0.0.1:{curr_port}  ➔  {curr_ip} ({curr_co})",
                font=FONT_MAIN, fg=C["fg"], bg=C["card"]
            ).pack(side="left")

            dom_c = eval_res.get("dominant_country", "undefined")
            dom_p = eval_res.get("dominant_percent", 0)
            tot = eval_res.get("total_launches", 0)
            tot_str = f" ({tot} сессий)" if tot else ""

            tk.Label(
                d_row,
                text=f"Привычный регион: {dom_c} ({dom_p}% активности{tot_str})",
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
        dlg.title("Расширенный журнал запусков и статистика по IP")
        dlg.geometry("860x560")
        dlg.minsize(780, 480)
        dlg.configure(bg=C["bg"])

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=C["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=C["card_inner"], foreground=C["fg"], padding=[12, 6])
        style.map("TNotebook.Tab", background=[("selected", C["card"])], foreground=[("selected", C["accent_blue"])])

        nb = ttk.Notebook(dlg)
        nb.pack(fill="both", expand=True, padx=12, pady=12)

        tab_history = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_history, text="📜 Журнал всех событий (Логи)")

        tab_ips = tk.Frame(nb, bg=C["bg"])
        nb.add(tab_ips, text="🌐 Сводка по уникальным IP")

        # Вкладка 1
        cols1 = ("time", "account", "port", "ip", "country", "event", "note")
        tree1 = ttk.Treeview(tab_history, columns=cols1, show="headings", height=18)
        tree1.heading("time", text="Время")
        tree1.heading("account", text="Аккаунт Google")
        tree1.heading("port", text="Порт")
        tree1.heading("ip", text="Внешний IP")
        tree1.heading("country", text="Страна")
        tree1.heading("event", text="Событие")
        tree1.heading("note", text="Примечание")

        tree1.column("time", width=140, anchor="w")
        tree1.column("account", width=170, anchor="w")
        tree1.column("port", width=65, anchor="center")
        tree1.column("ip", width=120, anchor="w")
        tree1.column("country", width=100, anchor="w")
        tree1.column("event", width=90, anchor="center")
        tree1.column("note", width=130, anchor="w")

        sc1 = ttk.Scrollbar(tab_history, orient="vertical", command=tree1.yview)
        tree1.configure(yscrollcommand=sc1.set)
        tree1.pack(side="left", fill="both", expand=True)
        sc1.pack(side="right", fill="y")

        history = strategy_manager.load_history()
        for e in reversed(history):
            tree1.insert(
                "", "end",
                values=(
                    e.get("timestamp", "-"),
                    e.get("account_email", "-"),
                    e.get("port", "-"),
                    e.get("ip", "-"),
                    e.get("country", "-"),
                    e.get("event", "-"),
                    e.get("note", "-"),
                )
            )

        # Вкладка 2
        cols2 = ("ip", "country", "launches", "ports", "accounts", "last_seen")
        tree2 = ttk.Treeview(tab_ips, columns=cols2, show="headings", height=18)
        tree2.heading("ip", text="Внешний IP")
        tree2.heading("country", text="Страна")
        tree2.heading("launches", text="Всего запусков")
        tree2.heading("ports", text="Порты")
        tree2.heading("accounts", text="Аккаунты")
        tree2.heading("last_seen", text="Последний запуск")

        tree2.column("ip", width=130, anchor="w")
        tree2.column("country", width=120, anchor="w")
        tree2.column("launches", width=95, anchor="center")
        tree2.column("ports", width=100, anchor="w")
        tree2.column("accounts", width=180, anchor="w")
        tree2.column("last_seen", width=140, anchor="w")

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
        self.backup_password_var.set("")
        self.load_proxies_data()
        self.load_gemini_profiles_data()
        self.render_backup_page()
        self.render_proxy_table()
        self.refresh_routes_async()

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
                    self.load_proxies_data()
                    self.load_gemini_profiles_data()
                    self.render_backup_page()
                    self.refresh_routes_async()
                    messagebox.showinfo("Импорт бэкапа", msg)
                else:
                    messagebox.showerror("Ошибка импорта", msg)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

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
                    self.load_proxies_data()
                    self.load_gemini_profiles_data()
                    self.render_backup_page()
                    self.refresh_routes_async()
                    messagebox.showinfo("Импорт бэкапа", msg)
                else:
                    messagebox.showerror("Ошибка импорта", msg)
            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    # =========================================================================
    # ОБЩАЯ ЛОГИКА И СТАТУС
    # =========================================================================
    def on_global_refresh(self):
        """Кнопка 'Проверить всё'."""
        self.refresh_routes_async()
        if self.active_tab == "proxy":
            self.check_current_proxy_page_async()
        elif self.active_tab == "gemini":
            self.refresh_gemini_profiles_async()
        elif self.active_tab == "strategy":
            self.render_strategy_page()
        elif self.active_tab == "backup":
            self.render_backup_page()

    def refresh_routes_async(self):
        """Запуск фоновой проверки маршрутов."""
        if self.is_checking_routes:
            return

        self.is_checking_routes = True
        self.refresh_btn.config(state="disabled", text="⏳ Проверка...")
        self.quick_status.config(text="● Опрос WSL2 и маршрутов...", fg=C["yellow"])

        def worker():
            res = sync_manager.check_all_routes()
            self.after(0, self._apply_route_results, res)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_route_results(self, res: dict):
        self.is_checking_routes = False
        self.refresh_btn.config(state="normal", text=t("btn_check_all"))
        self.last_check_lbl.config(text=t("lbl_last_check", time=res.get("timestamp", "--:--:--")))

        gemini = res.get("gemini", {})
        claude = res.get("claude", {})
        herdr = res.get("herdr_wsl", {})

        # 1. Herdr в WSL2
        if herdr.get("server_running"):
            self.herdr_badge.config(
                text=f"{t('herdr_badge_active')} ({herdr.get('pane_count', 0)})",
                bg=C["green"], fg="#11111b"
            )
            agents = herdr.get("agents", [])
            lines = []
            for a in agents:
                agent_name = a.get("agent", "unknown")
                status = a.get("status", "running")
                pane_id = a.get("pane_id", "")
                if agent_name == "agy":
                    icon = "🔮"
                    desc = "AGY CLI (Gemini) • SOCKS5"
                elif agent_name == "claude":
                    icon = "🧡"
                    desc = "Claude Code • Direct IP"
                else:
                    icon = "💻"
                    desc = f"{t('pane_terminal')} ({agent_name})"
                lines.append(f"{icon} {t('pane_panel')} {pane_id}: {desc} | {t('pane_status')}: [{status}]")

            if lines:
                self.herdr_panes_lbl.config(text="\n".join(lines), fg=C["fg"])
            else:
                self.herdr_panes_lbl.config(text=t("herdr_no_panes"), fg=C["subtext"])
        else:
            self.herdr_badge.config(text=t("herdr_not_running"), bg=C["red"], fg="#11111b")
            self.herdr_panes_lbl.config(
                text=t("herdr_wsl_stopped"),
                fg=C["red"]
            )

        # 2. Gemini
        active_port = gemini.get("port", 1081)
        active_route = gemini.get("route", f"socks5h://127.0.0.1:{active_port}")
        self.gemini_route_lbl.config(text=f"{active_route} ({t('pill_socks5')})")

        if gemini.get("failover_occurred"):
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
        if claude.get("online"):
            self.claude_status_lbl.config(
                text=t("route_claude_active"),
                fg=C["green"]
            )
            self.claude_ip_lbl.config(
                text=t("route_claude_ip", ip=claude.get('ip', 'Unknown'))
            )
            lat = claude.get("latency_ms")
            self.claude_ping_lbl.config(
                text=t("route_claude_ping", lat=lat),
                fg=C["green"]
            )
        else:
            self.claude_status_lbl.config(
                text=f"✕ {claude.get('status_text')}",
                fg=C["red"]
            )
            self.claude_ip_lbl.config(text=claude.get("ip", "Unknown"))
            self.claude_ping_lbl.config(text=t("route_claude_offline"), fg=C["red"])

        # 4. Общий статус
        if herdr.get("server_running") and gemini.get("online") and claude.get("online"):
            self.quick_status.config(text=t("status_herdr_active"), fg=C["green"])
        else:
            self.quick_status.config(text=t("status_monitoring_ok"), fg=C["yellow"])

    def _schedule_auto_refresh(self):
        if self.auto_refresh_enabled.get() and not self.is_checking_routes:
            self.refresh_routes_async()

        # Фоновая проверка планового автобэкапа
        try:
            threading.Thread(target=backup_manager.check_and_run_auto_backup, daemon=True).start()
        except Exception:
            pass

        self.after(30000, self._schedule_auto_refresh)

    # ── Системный трей ──────────────────────────────────────────
    def _init_tray(self):
        if not HAS_TRAY:
            return

        icon_img = make_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem(t("tray_show"), self.show_window, default=True),
            pystray.MenuItem(t("tray_exit"), self.quit_app)
        )
        self.tray_icon = pystray.Icon("herdr_router", icon_img, "Herdr AI Control Center", menu)

        def run_tray():
            try:
                self.tray_icon.run()
            except Exception:
                pass

        threading.Thread(target=run_tray, daemon=True).start()

    def on_close_button(self):
        if HAS_TRAY and self.tray_icon:
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
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.after(0, self.destroy)


def start_instance_server(app: HerdrConfigApp):
    def server_loop():
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
            srv.listen(5)
            while True:
                conn, _ = srv.accept()
                with conn:
                    try:
                        data = conn.recv(1024)
                        if b"SHOW" in data:
                            app.after(0, app.bring_to_front)
                    except Exception:
                        pass
        except Exception:
            pass

    t = threading.Thread(target=server_loop, daemon=True)
    t.start()


def main():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.6)
            s.connect(("127.0.0.1", SINGLE_INSTANCE_PORT))
            s.sendall(b"SHOW\n")
            sys.exit(0)
    except (ConnectionRefusedError, OSError):
        pass

    try:
        app = HerdrConfigApp()
        start_instance_server(app)
        app.mainloop()
    except BaseException:
        with open(BASE_DIR / "crash.log", "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        raise


if __name__ == "__main__":
    main()
