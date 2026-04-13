"""
NoteStack — Design tokens and QSS stylesheet for PyQt6.
"""

# ─── Color palette ────────────────────────────────────────────────────────────
_THEME_DARK = {
    "bg_app":        "#0D0D14",
    "bg_sidebar":    "#08080F",
    "bg_topbar":     "#0D0D14",
    "bg_card":       "#13131F",
    "bg_card_hov":   "#1A1A2E",
    "bg_input":      "#1C1C2C",
    "bg_modal":      "#11111A",
    "bg_btn_2nd":    "#1E1E30",
    "border":        "#1F1F35",
    "border_med":    "#2A2A45",
    "border_tag":    "#2D2D50",
    "accent":        "#4F6EF7",
    "accent_hov":    "#6b85ff",
    "accent_dim":    "#1A2456",
    "accent_text":   "#A5B4FC",
    "text_primary":  "#F0F0F8",
    "text_secondary":"#8B8BAA",
    "text_muted":    "#52526A",
    "text_tag":      "#9898BB",
    "star_on":       "#F5A623",
    "star_off":      "#3A3A58",
    "danger":        "#EF4444",
    "danger_hov":    "#DC2626",
    "success":       "#22C55E",
    "tag_sel_bg":    "#1A2456",
    "tag_sel_bdr":   "#4F6EF7",
    "tag_sel_text":  "#A5B4FC",
    "scrollbar_bg":  "#13131F",
    "scrollbar_hnd": "#2A2A45",
    "bg_panel":      "#1C1C2C",
}

_THEME_LIGHT = {
    "bg_app":        "#F5F7FB",
    "bg_sidebar":    "#EDF1F8",
    "bg_topbar":     "#F5F7FB",
    "bg_card":       "#FFFFFF",
    "bg_card_hov":   "#F3F6FD",
    "bg_input":      "#FFFFFF",
    "bg_modal":      "#FFFFFF",
    "bg_btn_2nd":    "#EEF2FB",
    "border":        "#D8DFEF",
    "border_med":    "#C9D3EA",
    "border_tag":    "#B8C6E6",
    "accent":        "#4F6EF7",
    "accent_hov":    "#3E5EEA",
    "accent_dim":    "#E1E8FF",
    "accent_text":   "#2F49B8",
    "text_primary":  "#1B2333",
    "text_secondary":"#4E5B75",
    "text_muted":    "#6D7B95",
    "text_tag":      "#5F6E89",
    "star_on":       "#D08B00",
    "star_off":      "#A0ABBF",
    "danger":        "#DC2626",
    "danger_hov":    "#B91C1C",
    "success":       "#16A34A",
    "tag_sel_bg":    "#E1E8FF",
    "tag_sel_bdr":   "#4F6EF7",
    "tag_sel_text":  "#2F49B8",
    "scrollbar_bg":  "#E6ECF7",
    "scrollbar_hnd": "#B9C5DF",
    "bg_panel":      "#FFFFFF",
}

THEMES = {
    "dark": _THEME_DARK,
    "light": _THEME_LIGHT,
}

THEME_LABELS = {
    "dark": "Dark",
    "light": "Light",
}

_CURRENT_THEME = "dark"
C = dict(THEMES[_CURRENT_THEME])


def normalize_theme(theme: str | None) -> str:
    if not theme:
        return "dark"
    key = theme.strip().lower()
    return key if key in THEMES else "dark"


def set_theme(theme: str | None) -> str:
    global _CURRENT_THEME
    normalized = normalize_theme(theme)
    _CURRENT_THEME = normalized
    C.clear()
    C.update(THEMES[normalized])
    return normalized


def get_current_theme() -> str:
    return _CURRENT_THEME


def get_theme_options() -> list[tuple[str, str]]:
    return [(key, THEME_LABELS.get(key, key.title())) for key in THEMES.keys()]

SIDEBAR_W  = 220
SIDEBAR_MIN_W = 180
TOPBAR_H   = 58
CARD_W_MIN = 260
CARD_W_MAX = 360
CARD_GAP   = 12


def make_stylesheet(theme: str | None = None) -> str:
    if theme is not None:
        set_theme(theme)
    c = C
    return f"""
/* ── Global ─────────────────────────────────────── */
QWidget {{
    background-color: {c['bg_app']};
    color: {c['text_primary']};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 12px;
    border: none;
    outline: none;
}}

QLabel {{
    background: transparent;
}}

QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: {c['bg_app']};
    border: none;
}}

/* ── Scrollbars ──────────────────────────────────── */
QScrollBar:vertical {{
    background: {c['scrollbar_bg']};
    width: 6px;
    margin: 0;
    border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {c['scrollbar_hnd']};
    min-height: 30px;
    border-radius: 3px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['border_med']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0; background: none;
}}
QScrollBar:horizontal {{ height: 0; }}

/* ── Sidebar ─────────────────────────────────────── */
#Sidebar {{
    background-color: {c['bg_sidebar']};
    border-right: 1px solid {c['border']};
    min-width: {SIDEBAR_MIN_W}px;
}}

#SidebarScroll,
#SidebarScrollContent,
#SidebarScroll > QWidget,
#SidebarRecentScroll,
#SidebarRecentScroll > QWidget,
#SidebarRecentScroll > QWidget > QWidget {{
    background-color: {c['bg_sidebar']};
    border: none;
}}

QSplitter::handle:horizontal {{
    background: {c['bg_sidebar']};
    border-left: 1px solid {c['border']};
    border-right: 1px solid {c['border']};
}}
QSplitter::handle:horizontal:hover {{
    background: {c['bg_card']};
}}

#LogoLabel {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.5px;
    padding: 20px 20px 4px 20px;
}}

#SidebarSectionLabel {{
    color: {c['text_muted']};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.5px;
    padding: 10px 20px 4px 20px;
}}

#SidebarIconBtn {{
    background: transparent;
    color: {c['text_secondary']};
    border: none;
    font-size: 14px;
    padding: 0;
    border-radius: 4px;
}}
#SidebarIconBtn:hover {{
    color: {c['text_primary']};
    background: {c['bg_card']};
}}

#NavBtn {{
    background: transparent;
    color: {c['text_secondary']};
    text-align: left;
    padding: 7px 20px;
    font-size: 12px;
    border-radius: 0;
    border: none;
}}
#NavBtn:hover {{
    background: {c['bg_card']};
    color: {c['text_primary']};
}}
#NavBtn[active="true"] {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
    border-left: 2px solid {c['accent']};
    padding-left: 18px;
}}

#SidebarTagBtn {{
    background: transparent;
    color: {c['text_secondary']};
    text-align: left;
    padding: 4px 20px;
    font-size: 11px;
    border: none;
    border-radius: 0;
}}
#SidebarTagBtn:hover {{
    color: {c['text_primary']};
}}

/* ── Top bar ─────────────────────────────────────── */
#WindowChromeBar {{
    background-color: {c['bg_topbar']};
    border-bottom: 1px solid {c['border']};
    min-height: 36px;
    max-height: 36px;
}}

#WindowChromeIcon {{
    background: transparent;
}}

#WindowChromeTitle {{
    color: {c['text_primary']};
    font-size: 11px;
    font-weight: 600;
    padding: 0;
}}

#WindowChromeBtn {{
    background: transparent;
    border: none;
    color: {c['text_secondary']};
    font-size: 12px;
    padding: 0;
    border-radius: 6px;
}}
#WindowChromeBtn:hover {{
    background: {c['bg_btn_2nd']};
    color: {c['text_primary']};
}}

#WindowChromeCloseBtn {{
    background: transparent;
    border: none;
    color: {c['text_secondary']};
    font-size: 12px;
    padding: 0;
    border-radius: 6px;
}}
#WindowChromeCloseBtn:hover {{
    background: {c['danger']};
    color: #ffffff;
}}

#TopBar {{
    background-color: {c['bg_topbar']};
    border-bottom: 1px solid {c['border']};
    min-height: {TOPBAR_H}px;
    max-height: {TOPBAR_H}px;
}}

#SearchBox {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 8px;
    padding: 8px 14px;
    color: {c['text_primary']};
    font-size: 12px;
    selection-background-color: {c['accent_dim']};
}}
#SearchBox:focus {{
    border-color: {c['accent']};
}}

#BtnPrimary {{
    background: {c['accent']};
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 18px;
    font-size: 12px;
    font-weight: 700;
}}
#BtnPrimary:hover {{
    background: {c['accent_hov']};
}}
#BtnPrimary:pressed {{
    background: {c['accent_dim']};
}}

#AvatarLabel {{
    background: {c['accent']};
    color: #fff;
    border-radius: 16px;
    font-size: 11px;
    font-weight: 700;
    min-width: 32px;
    max-width: 32px;
    min-height: 32px;
    max-height: 32px;
    padding: 0;
}}

#UserName {{
    color: {c['text_primary']};
    font-size: 12px;
    font-weight: 600;
}}

/* ── Content area ────────────────────────────────── */
#ContentHeader {{
    font-size: 22px;
    font-weight: 700;
    color: {c['text_primary']};
}}
#ContentSubtitle {{
    font-size: 12px;
    color: {c['text_secondary']};
    margin-top: 2px;
}}

#FilterBtn {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 8px;
    color: {c['text_secondary']};
    padding: 7px 14px;
    font-size: 11px;
}}
#FilterBtn:hover {{
    border-color: {c['accent']};
    color: {c['accent_text']};
}}
#FilterBtn[active="true"] {{
    border-color: {c['accent']};
    color: {c['accent_text']};
    background: {c['accent_dim']};
}}

#ModeToggleBtn {{
    background: transparent;
    border: 1px solid {c['border_med']};
    border-radius: 6px;
    padding: 6px 10px;
    color: {c['text_muted']};
    font-size: 14px;
    font-weight: 700;
}}
#ModeToggleBtn:hover {{
    color: {c['text_primary']};
    border-color: {c['border_tag']};
}}
#ModeToggleBtn:pressed {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
    border-color: {c['accent']};
}}

#HeaderOverflowBtn {{
    background: transparent;
    border: 1px solid {c['border_med']};
    border-radius: 6px;
    padding: 6px 10px;
    color: {c['text_muted']};
    font-size: 14px;
}}
#HeaderOverflowBtn:hover {{
    color: {c['text_primary']};
    border-color: {c['border_tag']};
}}

#FloatingAddBtn {{
    background: {c['accent']};
    color: #ffffff;
    border: none;
    border-radius: 28px;
    font-size: 28px;
    font-weight: 600;
    padding-bottom: 2px;
}}
#FloatingAddBtn:hover {{
    background: {c['accent_hov']};
}}
#FloatingAddBtn:pressed {{
    background: {c['accent_dim']};
}}

/* ── Prompt Card ─────────────────────────────────── */
#PromptCard {{
    background: {c['bg_card']};
    border: 1px solid {c['border']};
    border-radius: 12px;
}}
#PromptCard:hover {{
    border-color: {c['border_med']};
    background: {c['bg_card_hov']};
}}
#PromptCard[kbd_selected="true"] {{
    border-color: {c['accent']};
    background: {c['bg_card_hov']};
}}

#CardTitle {{
    color: {c['text_primary']};
    background: transparent;
    font-size: 13px;
    font-weight: 700;
}}

#CardBody {{
    color: {c['text_secondary']};
    font-size: 10px;
    line-height: 1.5;
}}

#CardBodyFrame {{
    background: {c['bg_input']};
    border: 1px solid {c['border']};
    border-radius: 6px;
}}

#CardDate {{
    color: {c['text_muted']};
    font-size: 9px;
}}

#CardFolderLabel {{
    color: {c['text_muted']};
    font-size: 9px;
}}
#CardFolderLabel[hovered="true"] {{
    color: {c['accent']};
    font-weight: 600;
}}

#StarBtn {{
    background: transparent;
    border: none;
    color: {c['star_off']};
    font-size: 15px;
    padding: 2px 4px;
}}
#StarBtn[favorited="true"] {{
    color: {c['star_on']};
}}
#StarBtn:hover {{
    color: {c['star_on']};
}}

#CardTagLabel {{
    background: transparent;
    border: 1px solid {c['border_tag']};
    border-radius: 10px;
    color: {c['text_tag']};
    font-size: 9px;
    font-weight: 600;
    padding: 2px 8px;
    letter-spacing: 0.4px;
    text-transform: uppercase;
}}
#CardTagLabel:hover {{
    border-color: {c['accent']};
    color: {c['accent']};
}}

#CardMenuBtn {{
    background: transparent;
    border: none;
    color: {c['text_muted']};
    font-size: 16px;
    padding: 0 6px;
}}
#CardMenuBtn:hover {{
    color: {c['text_secondary']};
}}

/* Card/list selection checkbox */
#PromptCard QCheckBox::indicator,
#ListRow QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {c['border_med']};
    border-radius: 4px;
    background: {c['bg_input']};
}}
#PromptCard QCheckBox::indicator:hover,
#ListRow QCheckBox::indicator:hover {{
    border-color: {c['accent']};
}}
#PromptCard QCheckBox::indicator:checked,
#ListRow QCheckBox::indicator:checked {{
    border-color: {c['accent']};
    background: {c['accent']};
}}
#PromptCard QCheckBox::indicator:disabled,
#ListRow QCheckBox::indicator:disabled {{
    border-color: {c['border']};
    background: {c['bg_btn_2nd']};
}}

/* ── Modals ──────────────────────────────────────── */
#ModalPanel {{
    background: {c['bg_modal']};
    border: 1px solid {c['border_med']};
    border-radius: 16px;
}}

#ModalTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {c['text_primary']};
}}

#ModalTitleInput {{
    background: transparent;
    border: none;
    border-bottom: 1px solid transparent;
    border-radius: 0px;
    color: {c['text_primary']};
    font-size: 16px;
    font-weight: 700;
    padding: 2px 4px;
    selection-background-color: {c['accent']};
    selection-color: #FFFFFF;
}}
#ModalTitleInput:focus {{
    border-bottom: 1px solid {c['accent']};
}}
#ModalTitleInput[placeholder] {{
    color: {c['text_muted']};
}}

#ModalSectionLabel {{
    color: {c['text_muted']};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1.5px;
}}

#VersionCurrentBadge {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 2px 8px;
    border-radius: 8px;
    border: 1px solid {c['accent']};
}}

#ModalInput {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
    color: {c['text_primary']};
    font-size: 12px;
    padding: 10px 14px;
    selection-background-color: {c['accent']};
    selection-color: #FFFFFF;
}}
#ModalInput:focus {{
    border-color: {c['accent']};
}}

#ModalTextEdit {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
    color: {c['text_primary']};
    font-size: 11px;
    padding: 10px 14px;
    selection-background-color: {c['accent']};
    selection-color: #FFFFFF;
}}
#ModalTextEdit:focus {{
    border-color: {c['accent']};
}}

#RichToolbar {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
}}

#FindReplaceBar {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
}}

#FindStatusLabel {{
    color: {c['text_muted']};
    font-size: 11px;
    min-width: 72px;
}}

#FindReplaceInlineGroup {{
    background: {c['bg_panel']};
    border: 1px dashed {c['border_tag']};
    border-radius: 8px;
}}

#MetaDropBtn {{
    background: transparent;
    border: none;
    color: {c['text_secondary']};
    font-size: 11px;
    font-weight: 600;
    padding: 2px 6px 2px 2px;
    text-align: left;
}}
#MetaDropBtn:hover {{
    color: {c['accent']};
}}
#MetaDropBtn:pressed {{
    color: {c['accent_hov']};
}}

#MetaHandle {{
    background: transparent;
    border: none;
}}

#MetaSection {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
}}

#MetaPill {{
    background: {c['tag_sel_bg']};
    border: 1px solid {c['tag_sel_bdr']};
    border-radius: 8px;
    color: {c['tag_sel_text']};
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
}}

#MetaPillEmpty {{
    background: transparent;
    border: 1px dashed {c['border_tag']};
    border-radius: 8px;
    color: {c['text_muted']};
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
}}

#MetaTagScroll,
#MetaTagScroll > QWidget,
#MetaTagScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}

#OutlineList {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
    color: {c['text_primary']};
    font-size: 11px;
    padding: 6px;
}}
#OutlineList::item {{
    padding: 4px 6px;
    border-radius: 4px;
}}
#OutlineList::item:selected {{
    background: {c['accent']};
    color: #FFFFFF;
}}

#ContentStats {{
    color: {c['text_muted']};
    font-size: 11px;
    padding-top: 8px;
}}

#RichBtn {{
    background: transparent;
    border: 1px solid {c['border_med']};
    border-radius: 6px;
    color: {c['text_secondary']};
    font-size: 12px;
    font-weight: 700;
    min-width: 28px;
    min-height: 28px;
}}
#RichBtn:hover {{
    border-color: {c['border_tag']};
    color: {c['text_primary']};
}}
#RichBtn:checked {{
    background: {c['accent_dim']};
    border-color: {c['accent']};
    color: {c['accent_text']};
}}

#ModalCombo {{
    background: {c['bg_input']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
    color: {c['text_primary']};
    padding: 10px 14px;
    font-size: 12px;
}}
#ModalCombo::drop-down {{
    border: none;
    padding-right: 8px;
    subcontrol-origin: padding;
    subcontrol-position: center right;
}}
#ModalCombo QAbstractItemView {{
    background: {c['bg_modal']};
    border: 1px solid {c['border_med']};
    color: {c['text_primary']};
    selection-background-color: {c['accent']};
    selection-color: #FFFFFF;
    border-radius: 10px;
}}

#CloseBtn {{
    background: transparent;
    color: {c['text_muted']};
    border: none;
    font-size: 20px;
    padding: 0;
}}
#CloseBtn:hover {{
    color: {c['text_primary']};
}}

#ModalIconBtn {{
    background: transparent;
    color: {c['text_muted']};
    border: none;
    font-size: 16px;
    padding: 0;
}}
#ModalIconBtn:hover {{
    color: {c['text_primary']};
}}

#BtnSecondary {{
    background: {c['bg_btn_2nd']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
    color: {c['text_secondary']};
    padding: 9px 18px;
    font-size: 12px;
    font-weight: 600;
}}
#BtnSecondary:hover {{
    border-color: {c['border_tag']};
    color: {c['text_primary']};
}}

#BtnDanger {{
    background: transparent;
    border: 1px solid {c['danger']};
    border-radius: 10px;
    color: {c['danger']};
    padding: 9px 18px;
    font-size: 12px;
    font-weight: 600;
}}
#BtnDanger:hover {{
    background: {c['danger']};
    color: #fff;
}}

/* ── Filter tag chips ────────────────────────────── */
#FilterTagChip {{
    background: transparent;
    border: 1px solid {c['border_tag']};
    border-radius: 14px;
    color: {c['text_tag']};
    font-size: 10px;
    font-weight: 600;
    padding: 5px 14px;
    letter-spacing: 0.3px;
}}
#FilterTagChip:hover {{
    border-color: {c['border_med']};
    color: {c['text_primary']};
}}
#FilterTagChip[selected="true"] {{
    background: {c['tag_sel_bg']};
    border-color: {c['tag_sel_bdr']};
    color: {c['tag_sel_text']};
}}
#FilterTagChip:checked {{
    background: {c['tag_sel_bg']};
    border-color: {c['tag_sel_bdr']};
    color: {c['tag_sel_text']};
}}

/* ── Divider ─────────────────────────────────────── */
#Divider {{
    background: {c['border']};
    max-height: 1px;
    min-height: 1px;
}}

/* ── Empty state ─────────────────────────────────── */
#EmptyIcon {{
    font-size: 48px;
    color: {c['text_muted']};
}}
#EmptyTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {c['text_secondary']};
}}
#EmptySubtitle {{
    font-size: 12px;
    color: {c['text_muted']};
}}

/* ── Notification / icon buttons ─────────────────── */
#NotifBtn {{
    background: transparent;
    border: none;
    color: {c['text_muted']};
    font-size: 18px;
    padding: 4px 8px;
}}
#NotifBtn:hover {{
    color: {c['text_primary']};
}}

/* ── Subfolder chip strip ───────────────────────── */
#SubfolderLabel {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    color: {c['text_muted']};
    padding: 0 4px 0 0;
}}
#BackBtn {{
    background: transparent;
    border: 1px solid {c['border_med']};
    border-radius: 8px;
    color: {c['text_muted']};
    font-size: 11px;
    font-weight: 600;
    padding: 4px 12px;
}}
#BackBtn:hover {{
    background: {c['bg_card_hov']};
    border-color: {c['border_tag']};
    color: {c['text_primary']};
}}
#BackBtn:pressed {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
    border-color: {c['accent']};
}}
#SubfolderChip {{
    background: transparent;
    border: 1px solid {c['border_med']};
    border-radius: 8px;
    color: {c['text_secondary']};
    font-size: 11px;
    font-weight: 600;
    padding: 4px 12px;
    text-align: left;
}}
#SubfolderChip:hover {{
    background: {c['bg_card_hov']};
    border-color: {c['accent']};
    color: {c['text_primary']};
}}
#SubfolderChip:pressed {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
}}

/* ── Active filter pill ─────────────────────────── */
#ActiveFilterPill {{
    background: {c['tag_sel_bg']};
    border: 1px solid {c['tag_sel_bdr']};
    border-radius: 12px;
    color: {c['tag_sel_text']};
    font-size: 10px;
    font-weight: 600;
    padding: 4px 10px;
}}
#ClearFiltersBtn {{
    background: transparent;
    border: none;
    color: {c['text_muted']};
    font-size: 11px;
    padding: 0;
    text-decoration: underline;
}}
#ClearFiltersBtn:hover {{
    color: {c['text_secondary']};
}}
/* ── List view ───────────────────────────────────── */
#ListRow {{
    background: {c['bg_card']};
    border: 1px solid {c['border']};
    border-radius: 10px;
}}
#ListRow:hover {{
    border-color: {c['border_med']};
    background: {c['bg_card_hov']};
}}
#ListRow[kbd_selected="true"] {{
    border-color: {c['accent']};
    background: {c['bg_card_hov']};
}}

QMenu {{
    background: {c['bg_modal']};
    border: 1px solid {c['border_med']};
    border-radius: 8px;
    padding: 4px 0;
    color: {c['text_primary']};
    font-size: 12px;
}}
QMenu::item {{
    padding: 7px 20px 7px 10px;
}}
QMenu::item:selected {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
}}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 4px 0;
}}

#FolderTree {{
    background: transparent;
    color: {c['text_secondary']};
    border: none;
    padding: 0 8px;
}}
#FolderTree::item {{
    height: 26px;
}}
#FolderTree::item:selected {{
    background: {c['accent_dim']};
    color: {c['accent_text']};
    border-radius: 6px;
}}

#BulkBar {{
    background: {c['bg_card']};
    border: 1px solid {c['border_med']};
    border-radius: 10px;
    margin-top: 8px;
}}

#BulkBarSep {{
    background: {c['border_med']};
    min-width: 1px;
    max-width: 1px;
    min-height: 20px;
    max-height: 20px;
    margin: 0 4px;
}}

#ToastBar {{
    background: {c['bg_modal']};
    border: 1px solid {c['accent']};
    border-radius: 10px;
    margin-top: 8px;
}}

/* ── Trash view ──────────────────────────────────── */
#TrashItemRow {{
    background: {c['bg_card']};
    border: 1px solid {c['border']};
    border-radius: 8px;
}}

#TrashItemRow:hover {{
    background: {c['bg_card_hov']};
    border-color: {c['border_med']};
}}

#TrashExpiry {{
    font-size: 11px;
    color: {c['text_muted']};
}}
"""
