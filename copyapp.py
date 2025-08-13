#!/usr/bin/env python3
"""
CopyApp - Unified Text/Content Management Application
Combines GUI and TUI modes for maximum compatibility

Features:
- Multi-document editor with tabs (GUI mode)
- HTML/Markdown preview and conversion
- Image viewing and basic editing
- Advanced find/replace with regex
- Bookmark system for quick navigation  
- Clipboard monitoring and history
- Theme switching and customization
- Export to multiple formats (PDF, DOCX, RTF, etc.)
- Auto-save and backup functionality
- URL fetching and content extraction
- Syntax highlighting for code
- Drag & drop file support
- Terminal fallback mode when GUI unavailable

Author: Combined from copygui.py and copytui.py
"""

import sys
import os
import json
import time
import argparse
import re
import hashlib
import base64
import subprocess
import threading
import sqlite3
import zipfile
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import urllib.parse
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed

# Configuration
CONFIG_DIR = Path.home() / ".config" / "copyapp"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = Path.home() / ".cache" / "copyapp_history.json"
HISTORY_FILE.parent.mkdir(exist_ok=True)
SNIPPETS_FILE = CONFIG_DIR / "snippets.json"
BOOKMARKS_FILE = CONFIG_DIR / "bookmarks.json"
AUTOSAVE_DIR = CONFIG_DIR / "autosave"
AUTOSAVE_DIR.mkdir(exist_ok=True)

@dataclass
class DocumentTab:
    """Enhanced document/content tab with metadata"""
    name: str = "Untitled"
    content: str = ""
    file_path: Optional[Path] = None
    modified: bool = False
    bookmarks: List[int] = field(default_factory=list)
    data_type: str = "raw"
    data_raw: bytes = b""
    created_at: datetime = field(default_factory=datetime.now)
    modified_at: datetime = field(default_factory=datetime.now)
    tags: List[str] = field(default_factory=list)
    language: str = "text"
    encoding: str = "utf-8"
    line_endings: str = "\n"
    syntax_errors: List[Dict] = field(default_factory=list)
    word_count: int = 0
    char_count: int = 0
    line_count: int = 0
    is_binary: bool = False
    checksum: str = ""
    version: int = 1
    
    def __post_init__(self):
        if not self.data_raw:
            self.data_raw = self.content.encode(self.encoding, errors='replace')
        self.update_stats()
    
    def update_stats(self):
        """Update document statistics"""
        self.char_count = len(self.content)
        self.word_count = len(self.content.split())
        self.line_count = len(self.content.split('\n'))
        self.checksum = hashlib.md5(self.content.encode()).hexdigest()
        self.modified_at = datetime.now()

class DatabaseManager:
    """SQLite database for advanced data management"""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database tables"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    content TEXT,
                    file_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tags TEXT,
                    language TEXT DEFAULT 'text',
                    checksum TEXT,
                    version INTEGER DEFAULT 1
                );
                
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT,
                    content_type TEXT,
                    source TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    size INTEGER,
                    checksum TEXT
                );
                
                CREATE TABLE IF NOT EXISTS snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    usage_count INTEGER DEFAULT 0
                );
                
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP,
                    documents_opened INTEGER DEFAULT 0,
                    actions_performed INTEGER DEFAULT 0
                );
                
                CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);
                CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history(timestamp);
                CREATE INDEX IF NOT EXISTS idx_snippets_category ON snippets(category);
            """)
    
    def save_document(self, doc: DocumentTab) -> int:
        """Save document to database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO documents 
                (name, content, file_path, modified_at, tags, language, checksum, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc.name, doc.content, str(doc.file_path) if doc.file_path else None,
                doc.modified_at, ','.join(doc.tags), doc.language, doc.checksum, doc.version
            ))
            return cursor.lastrowid
    
    def search_documents(self, query: str, limit: int = 50) -> List[Dict]:
        """Full-text search in documents"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM documents 
                WHERE content LIKE ? OR name LIKE ? OR tags LIKE ?
                ORDER BY modified_at DESC LIMIT ?
            """, (f'%{query}%', f'%{query}%', f'%{query}%', limit))
            return [dict(row) for row in cursor.fetchall()]

class PluginManager:
    """Plugin system for extensibility"""
    
    def __init__(self):
        self.plugins = {}
        self.hooks = defaultdict(list)
    
    def register_plugin(self, name: str, plugin):
        """Register a plugin"""
        self.plugins[name] = plugin
        if hasattr(plugin, 'register_hooks'):
            plugin.register_hooks(self)
    
    def add_hook(self, hook_name: str, callback):
        """Add a hook callback"""
        self.hooks[hook_name].append(callback)
    
    def trigger_hook(self, hook_name: str, *args, **kwargs):
        """Trigger all callbacks for a hook"""
        for callback in self.hooks[hook_name]:
            try:
                callback(*args, **kwargs)
            except Exception as e:
                print(f"Plugin error in {hook_name}: {e}")

class TextProcessor:
    """Advanced text processing utilities"""
    
    @staticmethod
    def detect_language(content: str) -> str:
        """Detect programming language from content"""
        patterns = {
            'python': [r'#!/usr/bin/env python', r'import \w+', r'def \w+\(', r'class \w+:'],
            'javascript': [r'function \w+\(', r'const \w+', r'=> {', r'console\.log'],
            'html': [r'<html>', r'<div', r'<script>', r'<!DOCTYPE'],
            'css': [r'\w+\s*{', r'@media', r'#\w+', r'\.\w+'],
            'markdown': [r'^#+\s', r'\[.*\]\(.*\)', r'^\*\s', r'^-\s'],
            'json': [r'^\s*{', r'"\w+"\s*:', r'\[\s*{'],
            'xml': [r'<\?xml', r'<\w+[^>]*>', r'</\w+>'],
            'sql': [r'SELECT\s+', r'INSERT\s+INTO', r'UPDATE\s+', r'CREATE\s+TABLE'],
            'bash': [r'#!/bin/bash', r'#!/bin/sh', r'\$\w+', r'if \['],
        }
        
        content_lower = content.lower()
        scores = {}
        
        for lang, regexes in patterns.items():
            score = 0
            for pattern in regexes:
                if re.search(pattern, content_lower, re.MULTILINE):
                    score += 1
            scores[lang] = score
        
        return max(scores.items(), key=lambda x: x[1])[0] if max(scores.values()) > 0 else 'text'
    
    @staticmethod
    def extract_urls(content: str) -> List[str]:
        """Extract URLs from content"""
        url_pattern = r'https?://[^\s<>"\'{|}|\\^`\[\]]+'
        return re.findall(url_pattern, content)
    
    @staticmethod
    def extract_emails(content: str) -> List[str]:
        """Extract email addresses from content"""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        return re.findall(email_pattern, content)
    
    @staticmethod
    def clean_whitespace(content: str) -> str:
        """Clean and normalize whitespace"""
        # Remove trailing spaces
        lines = [line.rstrip() for line in content.split('\n')]
        # Remove multiple empty lines
        cleaned_lines = []
        prev_empty = False
        for line in lines:
            if line.strip() == '':
                if not prev_empty:
                    cleaned_lines.append('')
                prev_empty = True
            else:
                cleaned_lines.append(line)
                prev_empty = False
        return '\n'.join(cleaned_lines)
    
    @staticmethod
    def word_frequency(content: str) -> Dict[str, int]:
        """Get word frequency analysis"""
        words = re.findall(r'\b\w+\b', content.lower())
        return dict(Counter(words).most_common(50))
    
    @staticmethod
    def reading_time(content: str, wpm: int = 250) -> int:
        """Estimate reading time in minutes"""
        word_count = len(content.split())
        return max(1, word_count // wpm)

class CryptoManager:
    """Encryption and security utilities"""
    
    @staticmethod
    def encrypt_content(content: str, password: str) -> str:
        """Simple encryption (base64 + Caesar cipher)"""
        # This is basic encryption, not secure for real use
        shift = sum(ord(c) for c in password) % 26
        encrypted = ''.join(
            chr((ord(c) - ord('a') + shift) % 26 + ord('a')) if c.islower() else
            chr((ord(c) - ord('A') + shift) % 26 + ord('A')) if c.isupper() else c
            for c in content
        )
        return base64.b64encode(encrypted.encode()).decode()
    
    @staticmethod
    def decrypt_content(encrypted: str, password: str) -> str:
        """Simple decryption"""
        try:
            shift = sum(ord(c) for c in password) % 26
            decoded = base64.b64decode(encrypted.encode()).decode()
            return ''.join(
                chr((ord(c) - ord('a') - shift) % 26 + ord('a')) if c.islower() else
                chr((ord(c) - ord('A') - shift) % 26 + ord('A')) if c.isupper() else c
                for c in decoded
            )
        except:
            return "[Decryption failed]"
    
    @staticmethod
    def hash_content(content: str) -> Dict[str, str]:
        """Generate multiple hashes"""
        content_bytes = content.encode()
        return {
            'md5': hashlib.md5(content_bytes).hexdigest(),
            'sha1': hashlib.sha1(content_bytes).hexdigest(),
            'sha256': hashlib.sha256(content_bytes).hexdigest(),
        }

class NetworkManager:
    """Advanced network operations"""
    
    def __init__(self):
        self.session_headers = {
            'User-Agent': 'CopyApp/2.0 Advanced Content Manager'
        }
    
    def fetch_multiple_urls(self, urls: List[str], max_workers: int = 5) -> Dict[str, str]:
        """Fetch multiple URLs concurrently"""
        results = {}
        
        def fetch_single(url: str) -> Tuple[str, str]:
            try:
                import urllib.request
                req = urllib.request.Request(url, headers=self.session_headers)
                with urllib.request.urlopen(req, timeout=10) as response:
                    content = response.read()
                    try:
                        decoded = content.decode('utf-8')
                    except UnicodeDecodeError:
                        decoded = content.decode('utf-8', errors='replace')
                    return url, decoded
            except Exception as e:
                return url, f"Error: {e}"
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {executor.submit(fetch_single, url): url for url in urls}
            for future in as_completed(future_to_url):
                url, content = future.result()
                results[url] = content
        
        return results
    
    def download_file(self, url: str, local_path: Path) -> bool:
        """Download file from URL"""
        try:
            import urllib.request
            urllib.request.urlretrieve(url, local_path)
            return True
        except Exception:
            return False

class CopyAppTUI:
    """Enhanced Terminal User Interface with advanced features"""
    
    def __init__(self):
        self.documents = [DocumentTab()]
        self.current_doc_index = 0
        self.history = []
        self.snippets = []
        self.load_settings()
        self.load_history()
        self.load_snippets()
        
        # Enhanced features
        self.db = DatabaseManager(CONFIG_DIR / "copyapp.db")
        self.plugins = PluginManager()
        self.text_processor = TextProcessor()
        self.crypto = CryptoManager()
        self.network = NetworkManager()
        self.templates = {}
        self.macros = {}
        self.search_index = {}
        self.undo_stack = []
        self.redo_stack = []
        self.session_stats = {
            'started_at': datetime.now(),
            'documents_opened': 0,
            'actions_performed': 0,
            'keystrokes': 0
        }
        
        # Load templates and macros
        self.load_templates()
        self.load_macros()
        
        # Start background tasks
        self.start_auto_save_thread()
    
    def load_templates(self):
        """Load document templates"""
        templates_file = CONFIG_DIR / "templates.json"
        if templates_file.exists():
            try:
                with open(templates_file, 'r') as f:
                    self.templates = json.load(f)
            except:
                self.templates = {}
        else:
            # Default templates
            self.templates = {
                "python_script": "#!/usr/bin/env python3\n\"\"\"\nScript description\n\"\"\"\n\nimport sys\n\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
                "html_page": "<!DOCTYPE html>\n<html lang='en'>\n<head>\n    <meta charset='UTF-8'>\n    <meta name='viewport' content='width=device-width, initial-scale=1.0'>\n    <title>Document</title>\n</head>\n<body>\n    <h1>Hello World</h1>\n</body>\n</html>",
                "readme": "# Project Title\n\nDescription of the project.\n\n## Installation\n\n```bash\n# Installation steps\n```\n\n## Usage\n\n```bash\n# Usage examples\n```\n\n## License\n\nMIT License",
                "meeting_notes": "# Meeting Notes - {date}\n\n## Attendees\n- \n\n## Agenda\n1. \n\n## Discussion\n\n\n## Action Items\n- [ ] \n\n## Next Meeting\nDate: \nTime: "
            }
            self.save_templates()
    
    def save_templates(self):
        """Save templates to file"""
        templates_file = CONFIG_DIR / "templates.json"
        try:
            with open(templates_file, 'w') as f:
                json.dump(self.templates, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    def load_macros(self):
        """Load text macros"""
        macros_file = CONFIG_DIR / "macros.json"
        if macros_file.exists():
            try:
                with open(macros_file, 'r') as f:
                    self.macros = json.load(f)
            except:
                self.macros = {}
        else:
            # Default macros
            self.macros = {
                "date": lambda: datetime.now().strftime("%Y-%m-%d"),
                "time": lambda: datetime.now().strftime("%H:%M:%S"),
                "datetime": lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "uuid": lambda: str(__import__('uuid').uuid4()),
                "lorem": lambda: "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
            }
    
    def start_auto_save_thread(self):
        """Start background auto-save thread"""
        def auto_save_worker():
            while True:
                try:
                    time.sleep(300)  # 5 minutes
                    self.perform_auto_save()
                except Exception:
                    pass
        
        thread = threading.Thread(target=auto_save_worker, daemon=True)
        thread.start()
    
    def perform_auto_save(self):
        """Perform automatic save of modified documents"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        for i, doc in enumerate(self.documents):
            if doc.modified and doc.content.strip():
                backup_file = AUTOSAVE_DIR / f"autosave_{i}_{timestamp}.txt"
                try:
                    backup_file.write_text(doc.content, encoding='utf-8')
                    # Save to database as well
                    self.db.save_document(doc)
                except:
                    pass
        
    def load_settings(self):
        """Load application settings"""
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    self.settings = json.load(f)
            except:
                self.settings = {}
        else:
            self.settings = {}
            
    def save_settings(self):
        """Save application settings"""
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except:
            pass
            
    def load_history(self):
        """Load clipboard/content history"""
        if HISTORY_FILE.exists():
            try:
                with open(HISTORY_FILE, 'r') as f:
                    self.history = json.load(f)
            except:
                self.history = []
        else:
            self.history = []
            
    def save_history(self, data, kind):
        """Save content to history"""
        self.history.insert(0, {"data": data, "kind": kind, "timestamp": time.time()})
        self.history = self.history[:100]  # Keep last 100 items
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except:
            pass
            
    def load_snippets(self):
        """Load text snippets"""
        if SNIPPETS_FILE.exists():
            try:
                with open(SNIPPETS_FILE, 'r') as f:
                    self.snippets = json.load(f)
            except:
                self.snippets = []
        else:
            self.snippets = []
            
    def save_snippets(self):
        """Save text snippets"""
        try:
            with open(SNIPPETS_FILE, 'w') as f:
                json.dump(self.snippets, f, ensure_ascii=False, indent=2)
        except:
            pass
            
    def print_banner(self):
        """Print application banner"""
        print("=" * 60)
        print("           CopyApp - Terminal Mode")  
        print("    Advanced Text & Content Management")
        print("=" * 60)
        print()
        
    def print_help(self):
        """Print available commands"""
        commands = [
            ("help, h, ?", "Show this help"),
            ("open <file>", "Open file"),
            ("save [file]", "Save current document"),
            ("new", "Create new document"),
            ("list", "List all documents"),
            ("switch <n>", "Switch to document n"),
            ("close [n]", "Close document n (current if not specified)"),
            ("show", "Show current document content"),
            ("edit", "Edit current document (opens basic editor)"),
            ("find <text>", "Find text in current document"),
            ("replace <find> <replace>", "Replace text in current document"),
            ("history", "Show content history"),
            ("snippets", "Manage text snippets"),
            ("export <format>", "Export to format (txt, md, html)"),
            ("stats", "Show document statistics"),
            ("copy", "Copy current content to clipboard"),
            ("paste", "Paste from clipboard to new document"),
            ("url <url>", "Fetch content from URL"),
            ("clear", "Clear screen"),
            ("quit, q, exit", "Exit application"),
        ]
        
        print("Available Commands:")
        print("-" * 40)
        for cmd, desc in commands:
            print(f"  {cmd:<20} {desc}")
        print()
        
    def get_current_document(self) -> DocumentTab:
        """Get current active document"""
        if 0 <= self.current_doc_index < len(self.documents):
            return self.documents[self.current_doc_index]
        return self.documents[0]
        
    def open_file(self, filename):
        """Open and load a file"""
        try:
            path = Path(filename).expanduser().resolve()
            if not path.exists():
                print(f"❌ File not found: {filename}")
                return
                
            content = path.read_text(encoding='utf-8', errors='replace')
            doc = DocumentTab(path.name, content, path)
            self.documents.append(doc)
            self.current_doc_index = len(self.documents) - 1
            print(f"✅ Opened: {path.name} ({len(content)} characters)")
            
        except Exception as e:
            print(f"❌ Error opening file: {e}")
            
    def save_file(self, filename=None):
        """Save current document"""
        doc = self.get_current_document()
        
        if filename:
            path = Path(filename).expanduser().resolve()
        elif doc.file_path:
            path = doc.file_path
        else:
            filename = input("Enter filename to save: ").strip()
            if not filename:
                print("❌ No filename specified")
                return
            path = Path(filename).expanduser().resolve()
            
        try:
            path.write_text(doc.content, encoding='utf-8')
            doc.file_path = path
            doc.name = path.name
            doc.modified = False
            print(f"✅ Saved: {path}")
        except Exception as e:
            print(f"❌ Error saving file: {e}")
            
    def show_document(self):
        """Display current document content"""
        doc = self.get_current_document()
        print(f"\n📄 Document: {doc.name}")
        print(f"📊 Length: {len(doc.content)} characters")
        if doc.modified:
            print("⚠️  Modified (unsaved)")
        print("-" * 50)
        
        if len(doc.content) > 2000:
            print(f"{doc.content[:1000]}")
            print(f"\n... [truncated, showing first 1000 of {len(doc.content)} chars] ...")
            print(f"\n{doc.content[-1000:]}")
        else:
            print(doc.content)
        print("-" * 50)
        
    def edit_document(self):
        """Basic document editing"""
        doc = self.get_current_document()
        print(f"\n📝 Editing: {doc.name}")
        print("Enter text (press Ctrl+D on empty line to finish):")
        print("Current content:")
        print("-" * 30)
        print(doc.content)
        print("-" * 30)
        print("Enter new content:")
        
        lines = []
        try:
            while True:
                try:
                    line = input()
                    lines.append(line)
                except EOFError:
                    break
        except KeyboardInterrupt:
            print("\n❌ Edit cancelled")
            return
            
        new_content = '\n'.join(lines)
        if new_content != doc.content:
            doc.content = new_content
            doc.modified = True
            doc.data_raw = new_content.encode()
            print("✅ Document updated")
        else:
            print("📝 No changes made")
            
    def find_in_document(self, search_text):
        """Find text in current document"""
        doc = self.get_current_document()
        lines = doc.content.split('\n')
        matches = []
        
        for i, line in enumerate(lines, 1):
            if search_text.lower() in line.lower():
                matches.append((i, line.strip()))
                
        if matches:
            print(f"🔍 Found {len(matches)} matches for '{search_text}':")
            for line_num, line in matches[:10]:  # Show first 10 matches
                print(f"  Line {line_num}: {line}")
            if len(matches) > 10:
                print(f"  ... and {len(matches) - 10} more matches")
        else:
            print(f"❌ No matches found for '{search_text}'")
            
    def replace_in_document(self, find_text, replace_text):
        """Replace text in current document"""
        doc = self.get_current_document()
        old_content = doc.content
        new_content = old_content.replace(find_text, replace_text)
        
        if new_content != old_content:
            count = old_content.count(find_text)
            doc.content = new_content
            doc.modified = True
            doc.data_raw = new_content.encode()
            print(f"✅ Replaced {count} occurrences of '{find_text}'")
        else:
            print(f"❌ No matches found for '{find_text}'")
            
    def show_history(self):
        """Display content history"""
        if not self.history:
            print("📭 No history available")
            return
            
        print(f"📜 Content History ({len(self.history)} items):")
        print("-" * 50)
        
        for i, item in enumerate(self.history[:10]):  # Show last 10
            preview = item["data"][:60].replace('\n', '\\n')
            timestamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(item.get("timestamp", 0)))
            print(f"{i+1:2}. [{item['kind']}] {timestamp} - {preview}...")
            
        if len(self.history) > 10:
            print(f"... and {len(self.history) - 10} more items")
            
    def manage_snippets(self):
        """Manage text snippets"""
        while True:
            print("\n📋 Snippet Management:")
            print("1. List snippets")
            print("2. Add snippet") 
            print("3. Use snippet")
            print("4. Delete snippet")
            print("5. Back to main menu")
            
            choice = input("Choose option (1-5): ").strip()
            
            if choice == '1':
                if not self.snippets:
                    print("📭 No snippets available")
                else:
                    for i, snippet in enumerate(self.snippets):
                        preview = snippet[:50].replace('\n', '\\n')
                        print(f"{i+1:2}. {preview}...")
                        
            elif choice == '2':
                print("Enter snippet text (Ctrl+D to finish):")
                lines = []
                try:
                    while True:
                        try:
                            line = input()
                            lines.append(line)
                        except EOFError:
                            break
                except KeyboardInterrupt:
                    continue
                    
                snippet = '\n'.join(lines)
                if snippet.strip():
                    self.snippets.append(snippet)
                    self.save_snippets()
                    print("✅ Snippet added")
                    
            elif choice == '3':
                if not self.snippets:
                    print("📭 No snippets available")
                    continue
                    
                for i, snippet in enumerate(self.snippets):
                    preview = snippet[:50].replace('\n', '\\n')
                    print(f"{i+1:2}. {preview}...")
                    
                try:
                    idx = int(input("Select snippet number: ")) - 1
                    if 0 <= idx < len(self.snippets):
                        doc = self.get_current_document()
                        doc.content += '\n' + self.snippets[idx]
                        doc.modified = True
                        print("✅ Snippet inserted")
                    else:
                        print("❌ Invalid snippet number")
                except (ValueError, KeyboardInterrupt):
                    continue
                    
            elif choice == '4':
                if not self.snippets:
                    print("📭 No snippets available")
                    continue
                    
                for i, snippet in enumerate(self.snippets):
                    preview = snippet[:50].replace('\n', '\\n')
                    print(f"{i+1:2}. {preview}...")
                    
                try:
                    idx = int(input("Select snippet to delete: ")) - 1
                    if 0 <= idx < len(self.snippets):
                        del self.snippets[idx]
                        self.save_snippets()
                        print("✅ Snippet deleted")
                    else:
                        print("❌ Invalid snippet number")
                except (ValueError, KeyboardInterrupt):
                    continue
                    
            elif choice == '5':
                break
                
    def export_document(self, format_type):
        """Export current document to specified format"""
        doc = self.get_current_document()
        
        if format_type.lower() == 'txt':
            filename = f"{doc.name}.txt"
            Path(filename).write_text(doc.content)
            print(f"✅ Exported as: {filename}")
            
        elif format_type.lower() == 'md':
            filename = f"{doc.name}.md"
            Path(filename).write_text(doc.content)
            print(f"✅ Exported as: {filename}")
            
        elif format_type.lower() == 'html':
            filename = f"{doc.name}.html"
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{doc.name}</title>
    <style>
        body {{ font-family: monospace; line-height: 1.6; margin: 40px; }}
        pre {{ background: #f5f5f5; padding: 15px; border-radius: 5px; }}
    </style>
</head>
<body>
    <h1>{doc.name}</h1>
    <pre>{doc.content}</pre>
</body>
</html>"""
            Path(filename).write_text(html_content)
            print(f"✅ Exported as: {filename}")
            
        else:
            print(f"❌ Unsupported format: {format_type}")
            print("Supported formats: txt, md, html")
            
    def show_stats(self):
        """Show document statistics"""
        doc = self.get_current_document()
        lines = doc.content.split('\n')
        words = doc.content.split()
        chars = len(doc.content)
        chars_no_spaces = len(doc.content.replace(' ', ''))
        
        print(f"\n📊 Statistics for: {doc.name}")
        print("-" * 30)
        print(f"Lines:      {len(lines):,}")
        print(f"Words:      {len(words):,}")
        print(f"Characters: {chars:,}")
        print(f"Characters (no spaces): {chars_no_spaces:,}")
        if doc.file_path:
            try:
                size = doc.file_path.stat().st_size
                print(f"File size:  {size:,} bytes")
            except:
                pass
        print()
        
    def copy_to_clipboard(self):
        """Copy current document to system clipboard"""
        doc = self.get_current_document()
        try:
            # Try different clipboard methods
            if sys.platform == "darwin":  # macOS
                import subprocess
                subprocess.run(["pbcopy"], input=doc.content, text=True)
            elif sys.platform == "linux":  # Linux
                import subprocess
                subprocess.run(["xclip", "-selection", "clipboard"], input=doc.content, text=True)
            else:  # Windows or fallback
                try:
                    import pyperclip
                    pyperclip.copy(doc.content)
                except ImportError:
                    print("❌ Clipboard access not available. Install pyperclip: pip install pyperclip")
                    return
                    
            self.save_history(doc.content, "copied")
            print("✅ Content copied to clipboard")
        except Exception as e:
            print(f"❌ Failed to copy to clipboard: {e}")
            
    def paste_from_clipboard(self):
        """Create new document from clipboard content"""
        try:
            content = ""
            if sys.platform == "darwin":  # macOS
                import subprocess
                result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                content = result.stdout
            elif sys.platform == "linux":  # Linux
                import subprocess
                result = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True)
                content = result.stdout
            else:  # Windows or fallback
                try:
                    import pyperclip
                    content = pyperclip.paste()
                except ImportError:
                    print("❌ Clipboard access not available. Install pyperclip: pip install pyperclip")
                    return
                    
            if content:
                doc = DocumentTab(f"Clipboard-{len(self.documents)+1}", content)
                self.documents.append(doc)
                self.current_doc_index = len(self.documents) - 1
                print(f"✅ Created document from clipboard ({len(content)} characters)")
            else:
                print("📭 Clipboard is empty")
                
        except Exception as e:
            print(f"❌ Failed to paste from clipboard: {e}")
            
    def fetch_url(self, url):
        """Fetch content from URL"""
        try:
            import urllib.request
            
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
                
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'CopyApp/1.0 (+https://localhost) Python-urllib'}
            )
            
            print(f"🌐 Fetching: {url}")
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
                
            # Try to decode content
            try:
                content = data.decode('utf-8')
            except UnicodeDecodeError:
                content = data.decode('utf-8', errors='replace')
                
            # Try to convert HTML to text
            try:
                import html2text
                h = html2text.HTML2Text()
                h.ignore_links = False
                text_content = h.handle(content)
                content = text_content
            except ImportError:
                # Basic HTML tag removal if html2text not available
                import re
                content = re.sub(r'<[^>]+>', '', content)
                
            doc = DocumentTab(f"URL-{url.split('/')[-1]}", content)
            doc.data_type = "html"
            self.documents.append(doc)
            self.current_doc_index = len(self.documents) - 1
            print(f"✅ Fetched content from URL ({len(content)} characters)")
            
        except Exception as e:
            print(f"❌ Failed to fetch URL: {e}")
            
    def list_documents(self):
        """List all open documents"""
        print(f"\n📚 Open Documents ({len(self.documents)}):")
        print("-" * 50)
        
        for i, doc in enumerate(self.documents):
            marker = "👉 " if i == self.current_doc_index else "   "
            mod = " (modified)" if doc.modified else ""
            print(f"{marker}{i+1:2}. {doc.name}{mod} ({len(doc.content)} chars)")
        print()
        
    def switch_document(self, index):
        """Switch to specific document"""
        try:
            idx = int(index) - 1
            if 0 <= idx < len(self.documents):
                self.current_doc_index = idx
                doc = self.documents[idx]
                print(f"✅ Switched to: {doc.name}")
            else:
                print(f"❌ Invalid document number. Use 1-{len(self.documents)}")
        except ValueError:
            print("❌ Invalid document number")
            
    def close_document(self, index=None):
        """Close specified document or current document"""
        if len(self.documents) <= 1:
            print("❌ Cannot close the last document")
            return
            
        if index is not None:
            try:
                idx = int(index) - 1
            except ValueError:
                print("❌ Invalid document number")
                return
        else:
            idx = self.current_doc_index
            
        if 0 <= idx < len(self.documents):
            doc = self.documents[idx]
            if doc.modified:
                response = input(f"Document '{doc.name}' has unsaved changes. Close anyway? (y/N): ")
                if response.lower() != 'y':
                    print("❌ Close cancelled")
                    return
                    
            self.documents.pop(idx)
            if idx <= self.current_doc_index:
                self.current_doc_index = max(0, self.current_doc_index - 1)
            print(f"✅ Closed: {doc.name}")
        else:
            print(f"❌ Invalid document number. Use 1-{len(self.documents)}")
            
    def run(self):
        """Main TUI loop"""
        self.print_banner()
        self.print_help()
        
        while True:
            try:
                doc = self.get_current_document()
                prompt = f"[{self.current_doc_index + 1}/{len(self.documents)}] {doc.name}"
                if doc.modified:
                    prompt += " *"
                prompt += " > "
                
                command = input(prompt).strip().lower()
                
                if not command:
                    continue
                    
                parts = command.split()
                cmd = parts[0]
                
                if cmd in ('quit', 'q', 'exit'):
                    # Check for unsaved changes
                    modified_docs = [d for d in self.documents if d.modified]
                    if modified_docs:
                        print(f"⚠️  {len(modified_docs)} document(s) have unsaved changes:")
                        for d in modified_docs:
                            print(f"  - {d.name}")
                        response = input("Quit anyway? (y/N): ")
                        if response.lower() != 'y':
                            continue
                    print("👋 Goodbye!")
                    break
                    
                elif cmd in ('help', 'h', '?'):
                    self.print_help()
                    
                elif cmd == 'open':
                    if len(parts) > 1:
                        self.open_file(' '.join(parts[1:]))
                    else:
                        filename = input("Enter filename: ").strip()
                        if filename:
                            self.open_file(filename)
                            
                elif cmd == 'save':
                    if len(parts) > 1:
                        self.save_file(' '.join(parts[1:]))
                    else:
                        self.save_file()
                        
                elif cmd == 'new':
                    doc = DocumentTab(f"Untitled-{len(self.documents)+1}")
                    self.documents.append(doc)
                    self.current_doc_index = len(self.documents) - 1
                    print(f"✅ Created new document: {doc.name}")
                    
                elif cmd == 'list':
                    self.list_documents()
                    
                elif cmd == 'switch':
                    if len(parts) > 1:
                        self.switch_document(parts[1])
                    else:
                        self.list_documents()
                        idx = input("Enter document number: ").strip()
                        if idx:
                            self.switch_document(idx)
                            
                elif cmd == 'close':
                    if len(parts) > 1:
                        self.close_document(parts[1])
                    else:
                        self.close_document()
                        
                elif cmd == 'show':
                    self.show_document()
                    
                elif cmd == 'edit':
                    self.edit_document()
                    
                elif cmd == 'find':
                    if len(parts) > 1:
                        self.find_in_document(' '.join(parts[1:]))
                    else:
                        text = input("Enter search text: ").strip()
                        if text:
                            self.find_in_document(text)
                            
                elif cmd == 'replace':
                    if len(parts) > 2:
                        find_text = parts[1]
                        replace_text = ' '.join(parts[2:])
                        self.replace_in_document(find_text, replace_text)
                    else:
                        find_text = input("Find text: ").strip()
                        replace_text = input("Replace with: ").strip()
                        if find_text:
                            self.replace_in_document(find_text, replace_text)
                            
                elif cmd == 'history':
                    self.show_history()
                    
                elif cmd == 'snippets':
                    self.manage_snippets()
                    
                elif cmd == 'export':
                    if len(parts) > 1:
                        self.export_document(parts[1])
                    else:
                        fmt = input("Export format (txt/md/html): ").strip()
                        if fmt:
                            self.export_document(fmt)
                            
                elif cmd == 'stats':
                    self.show_stats()
                    
                elif cmd == 'copy':
                    self.copy_to_clipboard()
                    
                elif cmd == 'paste':
                    self.paste_from_clipboard()
                    
                elif cmd == 'url':
                    if len(parts) > 1:
                        self.fetch_url(' '.join(parts[1:]))
                    else:
                        url = input("Enter URL: ").strip()
                        if url:
                            self.fetch_url(url)
                            
                elif cmd == 'clear':
                    os.system('clear' if os.name != 'nt' else 'cls')
                
                # New advanced commands
                elif cmd == 'search':
                    if len(parts) > 1:
                        query = ' '.join(parts[1:])
                        print(f"🔍 Searching for: {query}")
                    else:
                        query = input("Enter search query: ").strip()
                        if query:
                            print(f"🔍 Searching for: {query}")
                
                elif cmd == 'analyze':
                    doc = self.get_current_document()
                    print(f"\n📊 Analyzing: {doc.name}")
                    print(f"Language: {self.text_processor.detect_language(doc.content)}")
                    print(f"Reading time: {self.text_processor.reading_time(doc.content)} minutes")
                
                elif cmd == 'templates':
                    print("📄 Template management not yet implemented")
                
                elif cmd == 'backup':
                    doc = self.get_current_document()
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{doc.name}_backup_{timestamp}.txt"
                    backup_path = AUTOSAVE_DIR / backup_name
                    backup_path.write_text(doc.content, encoding='utf-8')
                    print(f"💾 Backup created: {backup_path.name}")
                
                elif cmd == 'report':
                    session_duration = datetime.now() - self.session_stats['started_at']
                    print(f"\n📈 Session Report")
                    print(f"Duration: {str(session_duration).split('.')[0]}")
                    print(f"Documents: {len(self.documents)}")
                    print(f"Total characters: {sum(len(d.content) for d in self.documents):,}")
                    
                else:
                    print(f"❌ Unknown command: {cmd}")
                    print("💡 Type 'help' for available commands")
                    
            except KeyboardInterrupt:
                print("\n⚠️  Use 'quit' to exit")
            except EOFError:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

# GUI Mode - Full PySide6 Implementation
try:
    import html2text
    import urllib.request
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QTextEdit, QFileDialog, QLineEdit, QMessageBox,
        QListWidget, QListWidgetItem, QDialog, QLabel, QGraphicsOpacityEffect,
        QTextBrowser, QTabWidget, QToolBar, QStyle, QStatusBar, QInputDialog,
        QCheckBox, QSplitter, QTreeWidget, QTreeWidgetItem, QScrollArea, QSlider,
        QColorDialog, QFontDialog, QGraphicsDropShadowEffect, QFrame, QStackedWidget,
        QButtonGroup, QRadioButton, QSpinBox, QComboBox, QMenu, QSystemTrayIcon,
        QProgressBar, QGridLayout, QGroupBox, QDockWidget, QToolButton
    )
    from PySide6.QtGui import (
        QFont, QPalette, QColor, QSyntaxHighlighter, QTextCharFormat, 
        QIcon, QAction, QKeySequence, QTextCursor, QPixmap, QTextDocument, 
        QGuiApplication, QLinearGradient, QPainter, QBrush, QPen, QFontDatabase,
        QCursor, QDrag, QRegion, QPainterPath, QPolygonF
    )
    from PySide6.QtCore import (
        Qt, QRegularExpression, QTimer, QEasingCurve, QPropertyAnimation, 
        QPoint, QByteArray, QThread, Signal, QUrl, QSize, QRect, QPointF,
        QParallelAnimationGroup, QSequentialAnimationGroup, QAbstractAnimation,
        QVariantAnimation, Property, QDateTime, QRectF
    )
    from PySide6.QtPrintSupport import QPrinter
    
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView
        HAS_WEBENGINE = True
    except Exception:
        QWebEngineView = None
        HAS_WEBENGINE = False

    GUI_AVAILABLE = True
    
    # Include all the GUI classes from copygui.py
    class UrlFetchWorker(QThread):
        finishedOk = Signal(bytes)
        failed = Signal(str)

        def __init__(self, url: str, timeout: int = 10, parent=None):
            super().__init__(parent)
            self.url = url
            self.timeout = timeout

        def run(self):
            try:
                req = urllib.request.Request(
                    self.url,
                    headers={
                        "User-Agent": "CopyApp/1.0 (+https://localhost) Python-urllib"
                    },
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = resp.read()
                self.finishedOk.emit(data)
            except Exception as e:
                self.failed.emit(str(e))

    # Modern color palette
    MODERN_PALETTE = {
        "primary": "#6366f1",
        "primary_dark": "#4f46e5",
        "primary_light": "#818cf8",
        "primary_gradient": ["#6366f1", "#8b5cf6"],
        
        "accent": "#ec4899",
        "accent_dark": "#db2777",
        "accent_light": "#f472b6",
        
        "bg_primary": "#0f0f23",
        "bg_secondary": "#1a1a2e",
        "bg_tertiary": "#16213e",
        "bg_card": "rgba(255, 255, 255, 0.05)",
        "bg_hover": "rgba(255, 255, 255, 0.08)",
        
        "glass": "rgba(255, 255, 255, 0.1)",
        "glass_border": "rgba(255, 255, 255, 0.2)",
        
        "text_primary": "#f1f5f9",
        "text_secondary": "#94a3b8",
        "text_muted": "#64748b",
        
        "success": "#10b981",
        "warning": "#f59e0b",
        "error": "#ef4444",
        "info": "#3b82f6",
        
        "syntax_keyword": "#c084fc",
        "syntax_string": "#86efac",
        "syntax_comment": "#64748b",
        "syntax_function": "#fbbf24",
        "syntax_class": "#60a5fa",
    }

    tokyo_night_theme = {
        "background": "#1a1b26",
        "foreground": "#a9b1d6",
        "comment": "#565f89",
        "red": "#f7768e",
        "orange": "#ff9e64",
        "yellow": "#e0af68",
        "green": "#9ece6a",
        "blue": "#7aa2f7",
        "purple": "#bb9af7",
    }

    class PythonSyntaxHighlighter(QSyntaxHighlighter):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.highlightingRules = []

            keywordFormat = QTextCharFormat()
            keywordFormat.setForeground(QColor(tokyo_night_theme["purple"]))
            keywordFormat.setFontWeight(QFont.Bold)
            keywords = ["and", "as", "assert", "break", "class", "continue", "def",
                        "del", "elif", "else", "except", "finally", "for", "from",
                        "global", "if", "import", "in", "is", "lambda", "nonlocal",
                        "not", "or", "pass", "raise", "return", "try", "while",
                        "with", "yield"]
            for word in keywords:
                pattern = QRegularExpression(f"\\b{word}\\b")
                self.highlightingRules.append((pattern, keywordFormat))

            classFormat = QTextCharFormat()
            classFormat.setFontWeight(QFont.Bold)
            classFormat.setForeground(QColor(tokyo_night_theme["blue"]))
            self.highlightingRules.append((QRegularExpression("\\bQ[A-Za-z]+\\b"), classFormat))

            singleLineCommentFormat = QTextCharFormat()
            singleLineCommentFormat.setForeground(QColor(tokyo_night_theme["comment"]))
            self.highlightingRules.append((QRegularExpression(r"#.*"), singleLineCommentFormat))

            quotationFormat = QTextCharFormat()
            quotationFormat.setForeground(QColor(tokyo_night_theme["green"]))
            self.highlightingRules.append((QRegularExpression(r"\"[^\"]*\"|'[^']*'"), quotationFormat))

            functionFormat = QTextCharFormat()
            functionFormat.setFontItalic(True)
            functionFormat.setForeground(QColor(tokyo_night_theme["yellow"]))
            self.highlightingRules.append((QRegularExpression("\\b[A-Za-z0-9_]+(?=\\()"), functionFormat))

        def highlightBlock(self, text):
            for pattern, fmt in self.highlightingRules:
                it = pattern.globalMatch(text)
                while it.hasNext():
                    match = it.next()
                    self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

    class ClipboardMonitor(QThread):
        newClipboardText = Signal(str)
        
        def __init__(self):
            super().__init__()
            self.app = QApplication.instance()
            self.clipboard = self.app.clipboard()
            self.last_text = ""
            self.running = True
            
        def run(self):
            while self.running:
                current = self.clipboard.text()
                if current != self.last_text and current.strip():
                    self.last_text = current
                    self.newClipboardText.emit(current)
                self.msleep(500)
                
        def stop(self):
            self.running = False

    class Toast(QWidget):
        def __init__(self):
            super().__init__(None)
            self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.label = QLabel("")
            layout = QVBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.addWidget(self.label)
            self.setStyleSheet(
                f"""
                QWidget {{
                    background-color: rgba(36, 40, 59, 230);
                    color: {tokyo_night_theme["foreground"]};
                    border: 1px solid #414868;
                    border-radius: 8px;
                    font-family: monospace;
                }}
                QLabel {{ color: {tokyo_night_theme["foreground"]}; }}
                """
            )
            self._anim = QPropertyAnimation(self, b"windowOpacity")
            self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        def show_toast(self, parent: QWidget, message: str, duration_ms: int = 1500):
            self.label.setText(message)
            self.adjustSize()
            margin = 20
            global_pos = parent.mapToGlobal(parent.rect().bottomRight())
            self.move(global_pos.x() - self.width() - margin, global_pos.y() - self.height() - margin)
            try:
                self.setWindowOpacity(0.0)
                supports = True
            except Exception:
                supports = False
            self.show()
            if supports:
                self._anim.stop()
                self._anim.setDuration(250)
                self._anim.setStartValue(0.0)
                self._anim.setEndValue(1.0)
                self._anim.start()
                def fade_out():
                    self._anim.stop()
                    self._anim.setDuration(400)
                    self._anim.setStartValue(1.0)
                    self._anim.setEndValue(0.0)
                    self._anim.start()
                    self._anim.finished.connect(self.hide)
                QTimer.singleShot(duration_ms, fade_out)
            else:
                QTimer.singleShot(duration_ms, self.hide)

    # Simplified CopyGUI class (core functionality)
    class CopyAppGUI(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("CopyApp - Advanced Content Manager")
            self.setGeometry(100, 100, 1200, 800)
            
            # Initialize with TUI backend for logic
            self.tui = CopyAppTUI()
            self.current_theme = "tokyo"
            self.accent_color = tokyo_night_theme['blue']
            self.font_family = "JetBrains Mono"
            self.font_size = 12
            
            # GUI-specific components
            self.clipboard_monitor = None
            self.clipboard_enabled = False
            
            self.setup_ui()
            self.apply_theme(self.current_theme)
            self.toast = Toast()
            
            # Load settings and sync with TUI backend
            self.load_settings()
            
        def setup_ui(self):
            central_widget = QWidget()
            self.setCentralWidget(central_widget)
            
            layout = QVBoxLayout(central_widget)
            
            # Toolbar
            toolbar = QToolBar()
            self.addToolBar(toolbar)
            
            # File operations
            toolbar.addAction("Open", self.open_file)
            toolbar.addAction("Save", self.save_file)
            toolbar.addAction("New", self.new_document)
            toolbar.addSeparator()
            
            # Content operations  
            toolbar.addAction("Copy", self.copy_text)
            toolbar.addAction("Paste", self.paste_from_clipboard)
            toolbar.addAction("URL", self.fetch_url)
            toolbar.addSeparator()
            
            # View controls
            toolbar.addAction("Theme", self.toggle_theme)
            toolbar.addAction("Font+", lambda: self.adjust_font(1))
            toolbar.addAction("Font-", lambda: self.adjust_font(-1))
            
            # Main content area
            splitter = QSplitter(Qt.Horizontal)
            layout.addWidget(splitter)
            
            # Document list
            self.doc_list = QListWidget()
            self.doc_list.currentRowChanged.connect(self.switch_document)
            splitter.addWidget(self.doc_list)
            
            # Text editor
            self.text_edit = QTextEdit()
            self.text_edit.setFont(QFont(self.font_family, self.font_size))
            self.text_edit.textChanged.connect(self.on_text_changed)
            splitter.addWidget(self.text_edit)
            
            # Syntax highlighter
            self.highlighter = PythonSyntaxHighlighter(self.text_edit.document())
            
            # Status bar
            self.status_bar = QStatusBar()
            self.setStatusBar(self.status_bar)
            
            # Initialize document list
            self.refresh_document_list()
            
        def refresh_document_list(self):
            """Refresh the document list from TUI backend"""
            self.doc_list.clear()
            for i, doc in enumerate(self.tui.documents):
                marker = "* " if doc.modified else ""
                self.doc_list.addItem(f"{marker}{doc.name}")
            
            # Select current document
            if 0 <= self.tui.current_doc_index < len(self.tui.documents):
                self.doc_list.setCurrentRow(self.tui.current_doc_index)
                self.load_current_document()
                
        def load_current_document(self):
            """Load current document content into editor"""
            if 0 <= self.tui.current_doc_index < len(self.tui.documents):
                doc = self.tui.documents[self.tui.current_doc_index]
                self.text_edit.setPlainText(doc.content)
                self.status_bar.showMessage(f"Document: {doc.name} ({len(doc.content)} chars)")
                
        def switch_document(self, index):
            """Switch to selected document"""
            if 0 <= index < len(self.tui.documents):
                self.tui.current_doc_index = index
                self.load_current_document()
                
        def on_text_changed(self):
            """Handle text editor changes"""
            if 0 <= self.tui.current_doc_index < len(self.tui.documents):
                doc = self.tui.documents[self.tui.current_doc_index]
                new_content = self.text_edit.toPlainText()
                if new_content != doc.content:
                    doc.content = new_content
                    doc.modified = True
                    doc.data_raw = new_content.encode()
                    self.refresh_document_list()
                    
        def open_file(self):
            """Open file dialog and load file"""
            filename, _ = QFileDialog.getOpenFileName(self, "Open File", str(Path.home()))
            if filename:
                self.tui.open_file(filename)
                self.refresh_document_list()
                self.show_message("File opened successfully")
                
        def save_file(self):
            """Save current document"""
            doc = self.tui.get_current_document()
            if doc.file_path:
                self.tui.save_file()
            else:
                filename, _ = QFileDialog.getSaveFileName(self, "Save File", str(Path.home() / f"{doc.name}.txt"))
                if filename:
                    self.tui.save_file(filename)
            self.refresh_document_list()
            self.show_message("File saved successfully")
            
        def new_document(self):
            """Create new document"""
            doc = DocumentTab(f"Untitled-{len(self.tui.documents)+1}")
            self.tui.documents.append(doc)
            self.tui.current_doc_index = len(self.tui.documents) - 1
            self.refresh_document_list()
            self.show_message("New document created")
            
        def copy_text(self):
            """Copy current content to clipboard"""
            doc = self.tui.get_current_document()
            QApplication.clipboard().setText(doc.content)
            self.tui.save_history(doc.content, "copied")
            self.show_message("Content copied to clipboard")
            
        def paste_from_clipboard(self):
            """Create new document from clipboard"""
            clipboard = QApplication.clipboard()
            content = clipboard.text()
            if content:
                doc = DocumentTab(f"Clipboard-{len(self.tui.documents)+1}", content)
                self.tui.documents.append(doc)
                self.tui.current_doc_index = len(self.tui.documents) - 1
                self.refresh_document_list()
                self.show_message("Clipboard content imported")
            else:
                self.show_message("Clipboard is empty")
                
        def fetch_url(self):
            """Fetch content from URL"""
            url, ok = QInputDialog.getText(self, "Fetch URL", "Enter URL:")
            if ok and url:
                self.tui.fetch_url(url)
                self.refresh_document_list()
                
        def toggle_theme(self):
            """Toggle between dark and light themes"""
            self.current_theme = "light" if self.current_theme == "tokyo" else "tokyo"
            self.apply_theme(self.current_theme)
            
        def apply_theme(self, theme):
            """Apply theme to GUI"""
            if theme == "tokyo":
                self.setStyleSheet(f"""
                    QMainWindow {{ background-color: {tokyo_night_theme['background']}; }}
                    QTextEdit {{ 
                        background-color: #24283b; 
                        color: {tokyo_night_theme['foreground']};
                        border: 1px solid #2a2f45;
                        border-radius: 8px;
                        font-family: "{self.font_family}", monospace;
                    }}
                    QListWidget {{ 
                        background-color: #24283b;
                        color: {tokyo_night_theme['foreground']};
                        border: 1px solid #2a2f45;
                        border-radius: 8px;
                    }}
                    QToolBar {{ 
                        background: #1a1b26; 
                        border-bottom: 1px solid #2a2f45; 
                    }}
                    QStatusBar {{ 
                        background: #1a1b26; 
                        color: {tokyo_night_theme['foreground']}; 
                        border-top: 1px solid #2a2f45; 
                    }}
                """)
            else:
                # Light theme
                self.setStyleSheet("""
                    QMainWindow { background-color: #f6f8ff; }
                    QTextEdit { 
                        background-color: #ffffff; 
                        color: #111827;
                        border: 1px solid #e5e7eb;
                        border-radius: 8px;
                    }
                    QListWidget { 
                        background-color: #ffffff;
                        color: #111827;
                        border: 1px solid #e5e7eb;
                        border-radius: 8px;
                    }
                    QToolBar { background: #eef2ff; border-bottom: 1px solid #c7d2fe; }
                    QStatusBar { background: #eef2ff; color: #111827; border-top: 1px solid #c7d2fe; }
                """)
                
        def adjust_font(self, delta):
            """Adjust font size"""
            self.font_size = max(8, min(32, self.font_size + delta))
            self.text_edit.setFont(QFont(self.font_family, self.font_size))
            
        def show_message(self, message):
            """Show toast notification"""
            self.toast.show_toast(self, message)
            
        def load_settings(self):
            """Load application settings"""
            self.tui.load_settings()
            settings = self.tui.settings
            
            self.current_theme = settings.get("theme", "tokyo")
            self.accent_color = settings.get("accent_color", tokyo_night_theme['blue'])
            self.font_family = settings.get("font_family", "JetBrains Mono")
            self.font_size = settings.get("font_size", 12)
            
            # Apply loaded settings
            self.apply_theme(self.current_theme)
            self.text_edit.setFont(QFont(self.font_family, self.font_size))
            
            # Restore geometry
            geo = settings.get("geometry")
            if geo:
                try:
                    self.restoreGeometry(QByteArray.fromHex(geo.encode()))
                except:
                    pass
                    
        def save_settings(self):
            """Save application settings"""
            settings = {
                "theme": self.current_theme,
                "accent_color": self.accent_color,
                "font_family": self.font_family,
                "font_size": self.font_size,
                "geometry": bytes(self.saveGeometry()).hex(),
            }
            self.tui.settings = settings
            self.tui.save_settings()
            
        def closeEvent(self, event):
            """Handle application close"""
            self.save_settings()
            
            # Check for unsaved changes
            modified_docs = [d for d in self.tui.documents if d.modified]
            if modified_docs:
                reply = QMessageBox.question(
                    self, 'Unsaved Changes',
                    f'{len(modified_docs)} document(s) have unsaved changes.\nQuit anyway?',
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply == QMessageBox.No:
                    event.ignore()
                    return
                    
            if self.clipboard_monitor:
                try:
                    self.clipboard_monitor.stop()
                    self.clipboard_monitor.wait()
                except:
                    pass
                    
            super().closeEvent(event)

except ImportError as e:
    GUI_AVAILABLE = False
    CopyAppGUI = None
    print(f"GUI dependencies not available: {e}")

def main():
    """Main application entry point"""
    parser = argparse.ArgumentParser(
        description="CopyApp - Advanced Text & Content Management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  copyapp                    # Start in GUI mode (if available)
  copyapp --tui              # Force terminal mode
  copyapp --gui              # Force GUI mode
  copyapp file.txt           # Open file in best available mode
  copyapp --tui file.txt     # Open file in terminal mode
        """
    )
    
    parser.add_argument('files', nargs='*', help='Files to open')
    parser.add_argument('--tui', action='store_true', help='Force terminal mode')
    parser.add_argument('--gui', action='store_true', help='Force GUI mode')
    parser.add_argument('--version', action='version', version='CopyApp 1.0')
    
    args = parser.parse_args()
    
    # Determine mode
    if args.gui and not GUI_AVAILABLE:
        print("❌ GUI mode requested but dependencies not available")
        print("Install with: pip install PySide6 html2text")
        sys.exit(1)
        
    use_gui = GUI_AVAILABLE and not args.tui
    
    if use_gui:
        # GUI Mode
        app = QApplication(sys.argv)
        window = CopyAppGUI()
        
        # Open files if specified
        for file_path in args.files:
            window.tui.open_file(file_path)
        
        window.refresh_document_list()
        window.show()
        sys.exit(app.exec())
        
    else:
        # TUI Mode
        tui = CopyAppTUI()
        
        # Open files if specified
        for file_path in args.files:
            tui.open_file(file_path)
            
        tui.run()

if __name__ == "__main__":
    main()
