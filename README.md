# NoteStack

A local desktop application for managing notes and AI prompts, built with Python and PyQt6.

NoteStack lets you store, organise, search, and copy notes from a clean, high-DPI-aware GUI — no cloud, no account, just a local SQLite database on your machine.

---

## Features

### Organisation
- **Folders** — create, rename, and delete folders; assign notes to folders; filter the sidebar by folder.
- **Tags** — add arbitrary tags to notes (`#react`, `#email`, …); autocomplete while typing; sidebar shows all tags with counts; click a tag to filter.
- **Favorites** — star/un-star notes from any view; dedicated Favorites section in the sidebar.

### Searching & Filtering
- Quick search bar filters by title, content, and tags.
- **Advanced Search** modal with keyword input, multi-tag selection, and a clear-all button.
- Sort results: newest first, oldest first, A→Z, or Z→A.
- Toggle between **grid** and **list** views.

### Note Management
- Create, edit, and delete notes via a modal dialog.
- Note cards show title, preview, tags, and creation date with a context menu.
- Detail modal shows the full note with folder/date metadata, plus copy-to-clipboard and edit buttons.
- Delete confirmation dialogs to prevent accidents.

### UI
- Responsive, high-DPI aware PyQt6 interface.
- Sidebar with logo, navigation, folder/tag lists, and settings.
- Flow layout for tags and cards.
- Data persists under OS-specific per-user application data folders.


---

## Requirements

- Windows 10/11 or Linux
- Python 3.11+
- Dependencies listed in [NoteStack/requirements.txt](NoteStack/requirements.txt):

```
PyQt6>=6.6.0
PyQt6-Qt6>=6.6.0
PyQt6-sip>=13.6.0
```

---

## Running from Source

```powershell
# 1. Clone the repo
git clone <repo-url>
cd NoteStack

# 2. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r NoteStack/requirements.txt

# 4. Run the app
cd NoteStack
python main.py
```

---

## Project Structure

```
prompt_management/
├── NoteStack/              # Application source
│   ├── main.py             # Entry point
│   ├── requirements.txt
│   ├── database/           # SQLite layer (notes, folders, tags)
│   └── ui/                 # PyQt6 windows, widgets, modals, styles
├── build_for_windows/      # Build pipeline (PyInstaller + Inno Setup)
│   ├── build.ps1
│   ├── convert_icon.py
│   ├── generate_template_db.py
│   ├── installer.iss
│   └── artifacts/          # Build outputs
├── resources/              # Logo and project assets
└── mockup/                 # HTML/CSS/JS UI mockup reference
```

---

## Data Storage

| Path | Purpose |
|---|---|
| Windows: `%LOCALAPPDATA%\ABasu_apps\NoteStack\notestack.db` | User's notes database |
| Linux: `${XDG_DATA_HOME:-$HOME/.local/share}/ABasu_apps/NoteStack/notestack.db` | User's notes database |
| Windows: `%LOCALAPPDATA%\ABasu_apps\NoteStack\install-*.log` | Installer logs |

On first launch the app copies a blank template database to the user data directory. Existing databases are never overwritten on reinstall or upgrade.

---

## License

Private project — all rights reserved.
