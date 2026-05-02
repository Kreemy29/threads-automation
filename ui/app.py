"""
ThreadsBotV2 — Main GUI
Built with customtkinter (dark theme).

Tabs:
  1. Accounts   — manage account list
  2. Settings   — all bot parameters + preset save/load
  3. Bio Gen    — LLM bio generation via DeepSeek
  4. Logs       — live per-account log viewer
"""
import os
import json
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ui.bio_gen import generate_bios

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACCOUNTS_FILE = os.path.join(ROOT, "accounts.txt")
BIOS_FILE     = os.path.join(ROOT, "content", "bios.txt")
PRESETS_DIR   = os.path.join(ROOT, "presets")
LOGS_DIR      = os.path.join(ROOT, "logs")

os.makedirs(PRESETS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,    exist_ok=True)

# ── appearance ─────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

ACCENT   = "#1DA1F2"
BG_DARK  = "#0F0F0F"
BG_PANEL = "#1A1A1A"
BG_CARD  = "#222222"


# ══════════════════════════════════════════════════════════════════════════════
#  Helper widgets
# ══════════════════════════════════════════════════════════════════════════════

class LabeledEntry(ctk.CTkFrame):
    """Label + Entry stacked vertically."""
    def __init__(self, parent, label, default="", width=200, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        ctk.CTkLabel(self, text=label, text_color="#AAAAAA", font=("Segoe UI", 11)).pack(anchor="w")
        self.entry = ctk.CTkEntry(self, width=width, height=32)
        self.entry.insert(0, default)
        self.entry.pack(fill="x")

    def get(self):  return self.entry.get()
    def set(self, v):
        self.entry.delete(0, "end")
        self.entry.insert(0, str(v))


class IntEntry(LabeledEntry):
    """LabeledEntry that only accepts integers."""
    def get(self):
        try: return int(self.entry.get())
        except ValueError: return 0


class SectionLabel(ctk.CTkLabel):
    def __init__(self, parent, text, **kw):
        super().__init__(parent, text=text, font=("Segoe UI", 13, "bold"),
                         text_color=ACCENT, **kw)


# ══════════════════════════════════════════════════════════════════════════════
#  Accounts Tab
# ══════════════════════════════════════════════════════════════════════════════

class AccountsTab(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG_PANEL)
        self._rows = []
        self._build()
        self._load_from_file()

    def _build(self):
        # ── header ──
        header = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        header.pack(fill="x", padx=12, pady=(12, 6))
        SectionLabel(header, "  Accounts").pack(side="left", padx=10, pady=8)
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right", padx=10, pady=6)
        ctk.CTkButton(btn_frame, text="Import .txt", width=100, command=self._import).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Export .txt", width=100, command=self._export).pack(side="left", padx=4)
        ctk.CTkButton(btn_frame, text="Clear All", width=90, fg_color="#882222",
                      hover_color="#AA3333", command=self._clear_all).pack(side="left", padx=4)

        # ── add-account form ──
        form = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        form.pack(fill="x", padx=12, pady=6)
        ctk.CTkLabel(form, text="Add account", font=("Segoe UI", 11, "bold"),
                     text_color="#CCCCCC").grid(row=0, column=0, columnspan=8, sticky="w", padx=12, pady=(8,4))

        labels = ["Username", "AdsPower ID", "Media Folder", "Follow List"]
        placeholders = ["myaccount1", "abc123def", "C:/media/acc1", "US Models"]
        self._form_entries = []
        for i, (lbl, ph) in enumerate(zip(labels, placeholders)):
            ctk.CTkLabel(form, text=lbl, text_color="#AAAAAA",
                         font=("Segoe UI", 11)).grid(row=1, column=i*2, padx=(12,4), pady=4, sticky="w")
            e = ctk.CTkEntry(form, width=150, placeholder_text=ph)
            e.grid(row=1, column=i*2+1, padx=4, pady=4)
            self._form_entries.append(e)

        # Browse button for Media Folder field
        ctk.CTkButton(form, text="📁", width=32,
                      command=self._browse_add_form_media).grid(row=1, column=5, padx=2, pady=4)

        ctk.CTkButton(form, text="+ Add", width=80, command=self._add_account).grid(
            row=1, column=8, padx=12, pady=4)
        form.grid_columnconfigure(9, weight=1)

        # ── account list ──
        list_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        cols = ctk.CTkFrame(list_frame, fg_color="#2A2A2A")
        cols.pack(fill="x", padx=8, pady=(8, 0))
        for col, w in [("#", 35), ("Username", 160), ("AdsPower ID", 130),
                       ("Media Folder", 200), ("Follow List", 120), ("Status", 90), ("", 60)]:
            ctk.CTkLabel(cols, text=col, font=("Segoe UI", 11, "bold"),
                         text_color="#AAAAAA", width=w).pack(side="left", padx=3, pady=4)

        self._scroll = ctk.CTkScrollableFrame(list_frame, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=8, pady=8)

        self._count_lbl = ctk.CTkLabel(list_frame, text="0 accounts",
                                       font=("Segoe UI", 10), text_color="#666666")
        self._count_lbl.pack(anchor="e", padx=12, pady=4)

    def _browse_add_form_media(self):
        folder = filedialog.askdirectory(title="Select media folder for this account")
        if folder:
            self._form_entries[2].delete(0, "end")
            self._form_entries[2].insert(0, folder)

    def _add_account(self, username="", adspower_id="", media_folder="", follow_list=""):
        usr  = username    or self._form_entries[0].get().strip()
        ads  = adspower_id or self._form_entries[1].get().strip()
        mf   = media_folder  or self._form_entries[2].get().strip()
        fl   = follow_list   or self._form_entries[3].get().strip()
        if not usr or not ads:
            messagebox.showwarning("Missing fields", "Username and AdsPower ID are required")
            return
        if any(r["username"] == usr for r in self._rows):
            messagebox.showwarning("Duplicate", f"@{usr} is already in the list")
            return
        self._append_row(usr, ads, media_folder=mf, follow_list=fl)
        for e in self._form_entries:
            e.delete(0, "end")
        self._save_to_file()

    def _append_row(self, username, adspower_id, status="pending",
                    media_folder="", follow_list=""):
        idx = len(self._rows) + 1
        row_data = {"username": username, "adspower_id": adspower_id,
                    "status": status, "media_folder": media_folder, "follow_list": follow_list}

        bg = BG_CARD if idx % 2 == 0 else "#1E1E1E"
        row_frame = ctk.CTkFrame(self._scroll, fg_color=bg, corner_radius=4)
        row_frame.pack(fill="x", pady=1)

        ctk.CTkLabel(row_frame, text=str(idx), width=35, font=("Segoe UI", 11)).pack(side="left", padx=3)
        ctk.CTkLabel(row_frame, text=f"@{username}", width=160, font=("Segoe UI", 11)).pack(side="left", padx=3)
        ctk.CTkLabel(row_frame, text=adspower_id, width=130, font=("Segoe UI", 11),
                     text_color="#AAAAAA").pack(side="left", padx=3)

        # Media folder label + browse button
        mf_lbl = ctk.CTkLabel(row_frame, text=os.path.basename(media_folder) or "—",
                               width=180, font=("Segoe UI", 10), text_color="#88BBFF",
                               cursor="hand2")
        mf_lbl.pack(side="left", padx=3)
        row_data["mf_label"] = mf_lbl
        ctk.CTkButton(row_frame, text="📁", width=28,
                      command=lambda u=username: self._browse_media(u)).pack(side="left", padx=2)

        fl_lbl = ctk.CTkLabel(row_frame, text=follow_list or "—",
                               width=120, font=("Segoe UI", 10), text_color="#FFBB44")
        fl_lbl.pack(side="left", padx=3)
        row_data["fl_label"] = fl_lbl

        status_colors = {"pending": "#AAAAAA", "active": "#44FF88",
                         "error": "#FF4444", "done": "#4488FF", "running": "#FFAA00",
                         "invalid": "#FF8800"}
        status_lbl = ctk.CTkLabel(row_frame, text=status, width=90,
                                  font=("Segoe UI", 11),
                                  text_color=status_colors.get(status, "#AAAAAA"))
        status_lbl.pack(side="left", padx=3)
        row_data["status_label"] = status_lbl
        row_data["frame"] = row_frame

        ctk.CTkButton(row_frame, text="✕", width=36, fg_color="#550000", hover_color="#880000",
                      command=lambda u=username: self._remove(u)).pack(side="right", padx=6, pady=3)
        ctk.CTkButton(row_frame, text="Reset", width=52, fg_color="#1a4a1a", hover_color="#2a6a2a",
                      command=lambda u=username: self._reset_account(u)).pack(side="right", padx=2, pady=3)

        self._rows.append(row_data)
        self._count_lbl.configure(text=f"{len(self._rows)} accounts")

    def _browse_media(self, username):
        folder = filedialog.askdirectory(title=f"Select media folder for @{username}")
        if not folder:
            return
        for r in self._rows:
            if r["username"] == username:
                r["media_folder"] = folder
                r["mf_label"].configure(text=os.path.basename(folder))
                break
        self._save_to_file()

    def _reset_account(self, username):
        import data.db as db
        db.init_db()
        db.update_account(username, state="pending", notes="", retry_count=0)
        self.update_status(username, "pending")

    def _remove(self, username):
        self._rows = [r for r in self._rows if r["username"] != username]
        for w in self._scroll.winfo_children():
            w.destroy()
        rows_copy, self._rows = self._rows[:], []
        for r in rows_copy:
            self._append_row(r["username"], r["adspower_id"], r.get("status", "pending"),
                             r.get("media_folder", ""), r.get("follow_list", ""))
        self._save_to_file()

    def _clear_all(self):
        if messagebox.askyesno("Clear all", "Remove all accounts from the list?"):
            for w in self._scroll.winfo_children():
                w.destroy()
            self._rows = []
            self._count_lbl.configure(text="0 accounts")
            self._save_to_file()

    def _import(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All", "*.*")])
        if not path:
            return
        imported = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    self._add_account(
                        parts[0], parts[1],
                        parts[2] if len(parts) > 2 else "",
                        parts[3] if len(parts) > 3 else "",
                    )
                    imported += 1
        messagebox.showinfo("Imported", f"Added {imported} accounts")

    def _export(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text files", "*.txt")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            for r in self._rows:
                f.write(f"{r['username']},{r['adspower_id']},"
                        f"{r.get('media_folder','')},"
                        f"{r.get('follow_list','')}\n")
        messagebox.showinfo("Exported", f"Saved {len(self._rows)} accounts")

    def _save_to_file(self):
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            f.write("# username,adspower_id,media_folder,follow_list\n")
            for r in self._rows:
                f.write(f"{r['username']},{r['adspower_id']},"
                        f"{r.get('media_folder','')},"
                        f"{r.get('follow_list','')}\n")

    def _load_from_file(self):
        if not os.path.exists(ACCOUNTS_FILE):
            return
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    usr = parts[0]
                    if not any(r["username"] == usr for r in self._rows):
                        self._append_row(
                            usr, parts[1],
                            media_folder=parts[2] if len(parts) > 2 else "",
                            follow_list =parts[3] if len(parts) > 3 else "",
                        )

    def update_status(self, username, status):
        colors = {"pending": "#AAAAAA", "active": "#44FF88",
                  "error": "#FF4444", "done": "#4488FF", "running": "#FFAA00"}
        for r in self._rows:
            if r["username"] == username and "status_label" in r:
                r["status_label"].configure(text=status,
                                            text_color=colors.get(status, "#AAAAAA"))
                r["status"] = status
                break

    def get_accounts(self) -> list:
        return [{"username": r["username"], "adspower_id": r["adspower_id"],
                 "media_folder": r.get("media_folder", ""),
                 "follow_list": r.get("follow_list", "")} for r in self._rows]


# ══════════════════════════════════════════════════════════════════════════════
#  Follow Lists Tab
# ══════════════════════════════════════════════════════════════════════════════

class FollowListsTab(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG_PANEL)
        self._build()
        self._refresh_lists()

    def _build(self):
        # ── left: editor ──
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)

        SectionLabel(left, "Follow List Editor").pack(anchor="w", pady=(0, 6))

        name_row = ctk.CTkFrame(left, fg_color=BG_CARD, corner_radius=8)
        name_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(name_row, text="List name:", text_color="#AAAAAA",
                     font=("Segoe UI", 11)).pack(side="left", padx=12, pady=8)
        self._name_entry = ctk.CTkEntry(name_row, width=200, placeholder_text="e.g. US Models")
        self._name_entry.pack(side="left", padx=4, pady=8)
        ctk.CTkButton(name_row, text="Save", width=80, command=self._save_list).pack(side="left", padx=8)
        ctk.CTkButton(name_row, text="Delete", width=80, fg_color="#552222",
                      hover_color="#773333", command=self._delete_list).pack(side="left", padx=4)

        ctk.CTkLabel(left, text="Handles (one per line — @handle or full URL):",
                     text_color="#AAAAAA", font=("Segoe UI", 11)).pack(anchor="w", pady=(6, 2))
        self._handles_box = ctk.CTkTextbox(left, font=("Segoe UI", 12),
                                           border_width=1, border_color="#444444")
        self._handles_box.pack(fill="both", expand=True)
        self._handles_box.insert("1.0", "Type handles here, one per line\n@handle or www.threads.net/@handle")
        self._handles_box.bind("<FocusIn>", self._clear_placeholder)
        self._handles_box.bind("<FocusOut>", self._restore_placeholder)
        self._placeholder_active = True

        self._status_lbl = ctk.CTkLabel(left, text="", font=("Segoe UI", 11), text_color="#44FF88")
        self._status_lbl.pack(anchor="w", pady=4)

        # ── right: saved lists ──
        right = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        right.pack(side="right", fill="y", padx=(6, 12), pady=12, ipadx=8)

        SectionLabel(right, "Saved Lists").pack(anchor="w", padx=12, pady=(10, 6))

        self._list_scroll = ctk.CTkScrollableFrame(right, fg_color="transparent", width=200)
        self._list_scroll.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkButton(right, text="↻ Refresh", width=160,
                      command=self._refresh_lists).pack(padx=8, pady=8)

    def _clear_placeholder(self, _event=None):
        if self._placeholder_active:
            self._handles_box.delete("1.0", "end")
            self._placeholder_active = False

    def _restore_placeholder(self, _event=None):
        if not self._handles_box.get("1.0", "end").strip():
            self._handles_box.insert("1.0", "Type handles here, one per line\n@handle or www.threads.net/@handle")
            self._placeholder_active = True

    def _refresh_lists(self):
        for w in self._list_scroll.winfo_children():
            w.destroy()
        try:
            import data.db as db
            db.init_db()
            names = db.list_follow_lists()
        except Exception:
            names = []
        for name in names:
            ctk.CTkButton(
                self._list_scroll, text=name, anchor="w",
                fg_color="transparent", hover_color=BG_CARD,
                command=lambda n=name: self._load_list(n),
            ).pack(fill="x", pady=2)

    def _load_list(self, name: str):
        import data.db as db
        db.init_db()
        handles = db.get_follow_list(name)
        self._name_entry.delete(0, "end")
        self._name_entry.insert(0, name)
        self._handles_box.delete("1.0", "end")
        self._handles_box.insert("1.0", "\n".join(handles))
        self._placeholder_active = False

    def _save_list(self):
        name = self._name_entry.get().strip()
        if not name:
            messagebox.showwarning("Missing name", "Enter a list name")
            return

        # If name looks like a handle/URL, move it into the handles box automatically
        is_handle_in_name = name.startswith("@") or "threads.net" in name or "threads.com" in name
        if is_handle_in_name:
            if self._placeholder_active:
                self._handles_box.delete("1.0", "end")
                self._placeholder_active = False
            self._handles_box.insert("end", name + "\n")
            name = name.lstrip("@").split("/")[-1]  # derive a clean list name
            self._name_entry.delete(0, "end")
            self._name_entry.insert(0, name)

        raw = "" if self._placeholder_active else self._handles_box.get("1.0", "end").strip()
        handles = [h.strip() for h in raw.splitlines() if h.strip()]
        if not handles:
            messagebox.showwarning("Empty list", "Add at least one handle")
            return
        import data.db as db
        db.init_db()
        db.save_follow_list(name, handles)
        self._status_lbl.configure(text=f"✓ Saved '{name}' ({len(handles)} handles)")
        self.after(3000, lambda: self._status_lbl.configure(text=""))
        self._refresh_lists()

    def _delete_list(self):
        name = self._name_entry.get().strip()
        if not name:
            return
        if messagebox.askyesno("Delete", f"Delete list '{name}'?"):
            import data.db as db
            db.init_db()
            db.delete_follow_list(name)
            self._handles_box.delete("1.0", "end")
            self._handles_box.insert("1.0", "Type handles here, one per line\n@handle or www.threads.net/@handle")
            self._placeholder_active = True
            self._name_entry.delete(0, "end")
            self._refresh_lists()


# ══════════════════════════════════════════════════════════════════════════════
#  Settings Tab
# ══════════════════════════════════════════════════════════════════════════════

class SettingsTab(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG_PANEL)
        self._build()
        self._load_preset("default")

    def _build(self):
        # ── preset bar ──
        pbar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        pbar.pack(fill="x", padx=12, pady=(12, 6))

        SectionLabel(pbar, "  Presets").pack(side="left", padx=10, pady=8)

        self._preset_name = ctk.CTkEntry(pbar, width=160, placeholder_text="Preset name")
        self._preset_name.pack(side="left", padx=6, pady=8)
        self._preset_name.insert(0, "default")

        ctk.CTkButton(pbar, text="Save", width=70, command=self._save_preset).pack(side="left", padx=4)
        ctk.CTkButton(pbar, text="Load", width=70, command=self._pick_and_load).pack(side="left", padx=4)

        self._saved_lbl = ctk.CTkLabel(pbar, text="", text_color="#44FF88",
                                       font=("Segoe UI", 11))
        self._saved_lbl.pack(side="left", padx=10)

        # ── scrollable settings ──
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=12, pady=6)

        # two-column layout
        left  = ctk.CTkFrame(scroll, fg_color="transparent")
        right = ctk.CTkFrame(scroll, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        scroll.grid_columnconfigure(0, weight=1)
        scroll.grid_columnconfigure(1, weight=1)

        # ── left: warmup ──
        self._card(left, "Warmup").pack(fill="x", pady=(0, 0))
        warm_card = self._card(left, "")
        warm_card.pack(fill="x", pady=(0, 8))

        self.warmup_duration  = IntEntry(warm_card, "Warmup duration (minutes)", "10")
        self.warmup_duration.pack(fill="x", padx=12, pady=4)

        self.warmup_targets   = LabeledEntry(warm_card, "Warmup targets (comma-separated @handles)", "", width=300)
        self.warmup_targets.pack(fill="x", padx=12, pady=4)

        likes_row_w = ctk.CTkFrame(warm_card, fg_color="transparent")
        likes_row_w.pack(fill="x", padx=12, pady=4)
        self.warmup_likes_min = IntEntry(likes_row_w, "Likes per visit MIN", "2", width=90)
        self.warmup_likes_min.pack(side="left", padx=(0, 8))
        self.warmup_likes_max = IntEntry(likes_row_w, "Likes per visit MAX", "6", width=90)
        self.warmup_likes_max.pack(side="left")

        self.warmup_max_follows = IntEntry(warm_card, "Max follows per warmup session", "8")
        self.warmup_max_follows.pack(fill="x", padx=12, pady=4)

        # ── left: active process ──
        self._card(left, "Active Process").pack(fill="x", pady=(8, 0))
        post_card = self._card(left, "")
        post_card.pack(fill="x", pady=(0, 8))

        self.text_posts       = IntEntry(post_card, "Text posts per cycle", "4")
        self.text_posts.pack(fill="x", padx=12, pady=4)

        self.session_duration = IntEntry(post_card, "Session duration (minutes)", "30")
        self.session_duration.pack(fill="x", padx=12, pady=4)

        self.image_post_var   = ctk.BooleanVar(value=True)
        img_row = ctk.CTkFrame(post_card, fg_color="transparent")
        img_row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(img_row, text="Image post per cycle", text_color="#AAAAAA",
                     font=("Segoe UI", 11)).pack(side="left")
        ctk.CTkSwitch(img_row, text="", variable=self.image_post_var).pack(side="right")

        likes_row_a = ctk.CTkFrame(post_card, fg_color="transparent")
        likes_row_a.pack(fill="x", padx=12, pady=4)
        self.active_likes_min = IntEntry(likes_row_a, "Likes per scroll MIN", "1", width=90)
        self.active_likes_min.pack(side="left", padx=(0, 8))
        self.active_likes_max = IntEntry(likes_row_a, "Likes per scroll MAX", "5", width=90)
        self.active_likes_max.pack(side="left")

        # ── right: engagement ──
        self._card(right, "Engagement").pack(fill="x", pady=(0, 8))
        eng_card = self._card(right, "")
        eng_card.pack(fill="x", pady=(0, 8))

        comments_row = ctk.CTkFrame(eng_card, fg_color="transparent")
        comments_row.pack(fill="x", padx=12, pady=4)
        self.comments_min = IntEntry(comments_row, "Comments/day MIN", "4", width=90)
        self.comments_min.pack(side="left", padx=(0, 8))
        self.comments_max = IntEntry(comments_row, "Comments/day MAX", "6", width=90)
        self.comments_max.pack(side="left")

        self.pic_comment_ratio = IntEntry(eng_card, "Pic comment ratio % (0–100)", "20")
        self.pic_comment_ratio.pack(fill="x", padx=12, pady=4)

        self.follow_batch     = IntEntry(eng_card, "Follow batch size", "20")
        self.follow_batch.pack(fill="x", padx=12, pady=4)

        self.unfollow_hours   = IntEntry(eng_card, "Unfollow after (hours)", "2")
        self.unfollow_hours.pack(fill="x", padx=12, pady=4)

        self.outreach_targets = LabeledEntry(eng_card, "Outreach targets (comma-separated @handles)", "", width=300)
        self.outreach_targets.pack(fill="x", padx=12, pady=4)

        # ── right: general ──
        self._card(right, "General").pack(fill="x", pady=(8, 0))
        gen_card = self._card(right, "")
        gen_card.pack(fill="x", pady=(0, 8))

        self.batch_size       = IntEntry(gen_card, "Concurrent accounts (batch size)", "15")
        self.batch_size.pack(fill="x", padx=12, pady=4)

        self.mother_post_url  = LabeledEntry(gen_card, "Mother post URL (Threads post to repost)", "", width=300)
        self.mother_post_url.pack(fill="x", padx=12, pady=4)

        self.adspower_api_key = LabeledEntry(gen_card, "AdsPower API Key", "", width=300)
        self.adspower_api_key.pack(fill="x", padx=12, pady=4)

        self.gemini_key       = LabeledEntry(gen_card, "Gemini API Key", "", width=300)
        self.gemini_key.pack(fill="x", padx=12, pady=(4, 12))

    def _card(self, parent, title):
        f = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=8)
        if title:
            ctk.CTkLabel(f, text=title, font=("Segoe UI", 12, "bold"),
                         text_color=ACCENT).pack(anchor="w", padx=12, pady=(8, 2))
        return f

    # ── preset I/O ──────────────────────────────────────────────────────────

    def _to_dict(self) -> dict:
        return {
            "text_posts":           self.text_posts.get(),
            "session_duration":     self.session_duration.get(),
            "image_post":           self.image_post_var.get(),
            "active_likes_min":     self.active_likes_min.get(),
            "active_likes_max":     self.active_likes_max.get(),
            "warmup_duration":      self.warmup_duration.get(),
            "warmup_targets":       self.warmup_targets.get(),
            "warmup_likes_min":     self.warmup_likes_min.get(),
            "warmup_likes_max":     self.warmup_likes_max.get(),
            "warmup_max_follows":   self.warmup_max_follows.get(),
            "comments_min":         self.comments_min.get(),
            "comments_max":         self.comments_max.get(),
            "pic_comment_ratio":    self.pic_comment_ratio.get(),
            "follow_batch":         self.follow_batch.get(),
            "unfollow_hours":       self.unfollow_hours.get(),
            "outreach_targets":     self.outreach_targets.get(),
            "batch_size":           self.batch_size.get(),
            "mother_post_url":      self.mother_post_url.get(),
            "adspower_api_key":     self.adspower_api_key.get(),
            "gemini_key":           self.gemini_key.get(),
        }

    def _from_dict(self, d: dict):
        self.text_posts.set(d.get("text_posts", 4))
        self.session_duration.set(d.get("session_duration", 30))
        self.image_post_var.set(d.get("image_post", True))
        self.active_likes_min.set(d.get("active_likes_min", 1))
        self.active_likes_max.set(d.get("active_likes_max", 5))
        self.warmup_duration.set(d.get("warmup_duration", 60))
        self.warmup_targets.set(d.get("warmup_targets", ""))
        self.warmup_likes_min.set(d.get("warmup_likes_min", 2))
        self.warmup_likes_max.set(d.get("warmup_likes_max", 6))
        self.warmup_max_follows.set(d.get("warmup_max_follows", 8))
        self.comments_min.set(d.get("comments_min", 4))
        self.comments_max.set(d.get("comments_max", 6))
        self.pic_comment_ratio.set(d.get("pic_comment_ratio", 20))
        self.follow_batch.set(d.get("follow_batch", 20))
        self.unfollow_hours.set(d.get("unfollow_hours", 2))
        self.outreach_targets.set(d.get("outreach_targets", ""))
        self.batch_size.set(d.get("batch_size", 15))
        self.mother_post_url.set(d.get("mother_post_url", ""))
        self.adspower_api_key.set(d.get("adspower_api_key", ""))
        self.gemini_key.set(d.get("gemini_key", d.get("deepseek_key", "")))

    def _save_preset(self):
        name = self._preset_name.get().strip() or "default"
        data = self._to_dict()
        path = os.path.join(PRESETS_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # Also write as active so workers always use the latest saved settings
        active_path = os.path.join(PRESETS_DIR, "active.json")
        with open(active_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self._saved_lbl.configure(text=f"✓ Saved '{name}'")
        self.after(3000, lambda: self._saved_lbl.configure(text=""))

    def _pick_and_load(self):
        path = filedialog.askopenfilename(initialdir=PRESETS_DIR,
                                          filetypes=[("JSON", "*.json")])
        if path:
            name = os.path.splitext(os.path.basename(path))[0]
            self._preset_name.delete(0, "end")
            self._preset_name.insert(0, name)
            self._load_preset(name)

    def _load_preset(self, name):
        path = os.path.join(PRESETS_DIR, f"{name}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._from_dict(json.load(f))

    def get_settings(self) -> dict:
        return self._to_dict()


# ══════════════════════════════════════════════════════════════════════════════
#  Bio Generator Tab
# ══════════════════════════════════════════════════════════════════════════════

class BioGenTab(ctk.CTkFrame):
    def __init__(self, parent, settings_tab: SettingsTab):
        super().__init__(parent, fg_color=BG_PANEL)
        self._settings = settings_tab
        self._build()

    def _build(self):
        # ── controls ──
        ctrl = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        ctrl.pack(fill="x", padx=12, pady=(12, 6))

        SectionLabel(ctrl, "  Bio Generator").pack(side="left", padx=10, pady=10)

        right_ctrl = ctk.CTkFrame(ctrl, fg_color="transparent")
        right_ctrl.pack(side="right", padx=10, pady=6)

        ctk.CTkLabel(right_ctrl, text="Niche / theme:", text_color="#AAAAAA",
                     font=("Segoe UI", 11)).pack(side="left", padx=(0, 6))
        self._niche = ctk.CTkEntry(right_ctrl, width=200, placeholder_text="e.g. fitness model, lifestyle")
        self._niche.pack(side="left", padx=4)

        ctk.CTkLabel(right_ctrl, text="Count:", text_color="#AAAAAA",
                     font=("Segoe UI", 11)).pack(side="left", padx=(12, 4))
        self._count = ctk.CTkEntry(right_ctrl, width=60)
        self._count.insert(0, "15")
        self._count.pack(side="left", padx=4)

        self._gen_btn = ctk.CTkButton(right_ctrl, text="Generate", width=100,
                                      command=self._generate)
        self._gen_btn.pack(side="left", padx=12)

        # ── preview area ──
        preview_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        preview_frame.pack(fill="both", expand=True, padx=12, pady=6)

        ctk.CTkLabel(preview_frame, text="Generated bios (edit freely before saving):",
                     text_color="#AAAAAA", font=("Segoe UI", 11)).pack(anchor="w", padx=12, pady=(10, 4))

        self._preview = ctk.CTkTextbox(preview_frame, font=("Segoe UI", 12), wrap="word")
        self._preview.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        # ── action bar ──
        action = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        action.pack(fill="x", padx=12, pady=(0, 12))

        self._status_lbl = ctk.CTkLabel(action, text="", text_color="#AAAAAA",
                                        font=("Segoe UI", 11))
        self._status_lbl.pack(side="left", padx=12, pady=8)

        ctk.CTkButton(action, text="Append to bios.txt", width=160, command=self._append_bios).pack(
            side="right", padx=4, pady=6)
        ctk.CTkButton(action, text="Overwrite bios.txt", width=160, fg_color="#553300",
                      hover_color="#774400", command=self._overwrite_bios).pack(
            side="right", padx=4, pady=6)

    def _generate(self):
        api_key = self._settings.get_settings().get("gemini_key", "").strip()
        if not api_key:
            messagebox.showwarning("API Key missing",
                                   "Enter your Gemini API key in the Settings tab first.")
            return

        niche = self._niche.get().strip()
        if not niche:
            messagebox.showwarning("Niche missing", "Enter a niche or theme to generate bios.")
            return

        try:
            count = int(self._count.get())
        except ValueError:
            count = 15

        self._gen_btn.configure(state="disabled", text="Generating…")
        self._status_lbl.configure(text="Calling DeepSeek API…")

        def _run():
            try:
                bios = generate_bios(api_key, niche, count)
                result = "\n".join(bios)
                self.after(0, lambda: self._show_result(result, len(bios)))
            except Exception as e:
                self.after(0, lambda: self._show_error(str(e)))

        threading.Thread(target=_run, daemon=True).start()

    def _show_result(self, text, count):
        self._preview.delete("1.0", "end")
        self._preview.insert("1.0", text)
        self._status_lbl.configure(text=f"✓ Generated {count} bios", text_color="#44FF88")
        self._gen_btn.configure(state="normal", text="Generate")

    def _show_error(self, msg):
        self._status_lbl.configure(text=f"Error: {msg[:80]}", text_color="#FF4444")
        self._gen_btn.configure(state="normal", text="Generate")

    def _get_bios_text(self) -> str:
        return self._preview.get("1.0", "end").strip()

    def _append_bios(self):
        text = self._get_bios_text()
        if not text:
            return
        with open(BIOS_FILE, "a", encoding="utf-8") as f:
            f.write("\n" + text)
        messagebox.showinfo("Saved", f"Bios appended to {BIOS_FILE}")

    def _overwrite_bios(self):
        text = self._get_bios_text()
        if not text:
            return
        if messagebox.askyesno("Overwrite", "This will replace all existing bios. Continue?"):
            with open(BIOS_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            messagebox.showinfo("Saved", f"bios.txt overwritten with {len(text.splitlines())} bios")


# ══════════════════════════════════════════════════════════════════════════════
#  Logs Tab
# ══════════════════════════════════════════════════════════════════════════════

class LogsTab(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=BG_PANEL)
        self._current_file = None
        self._last_size = 0
        self._polling = False
        self._build()
        self._start_poll()

    def _build(self):
        # ── toolbar ──
        bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        bar.pack(fill="x", padx=12, pady=(12, 6))

        SectionLabel(bar, "  Logs").pack(side="left", padx=10, pady=8)

        ctk.CTkLabel(bar, text="Account:", text_color="#AAAAAA",
                     font=("Segoe UI", 11)).pack(side="left", padx=(20, 4))
        self._selector = ctk.CTkOptionMenu(bar, values=["(none)"], width=200,
                                           command=self._switch_log)
        self._selector.pack(side="left", padx=4)

        ctk.CTkButton(bar, text="Refresh list", width=100,
                      command=self._refresh_accounts).pack(side="left", padx=8)
        ctk.CTkButton(bar, text="Clear view", width=90,
                      command=lambda: self._log_box.delete("1.0", "end")).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Open folder", width=100,
                      command=lambda: os.startfile(LOGS_DIR)).pack(side="right", padx=12)

        # ── log box ──
        log_frame = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=8)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._log_box = ctk.CTkTextbox(log_frame, font=("Consolas", 11), wrap="none",
                                       text_color="#CCCCCC")
        self._log_box.pack(fill="both", expand=True, padx=8, pady=8)

    def _refresh_accounts(self):
        if not os.path.exists(LOGS_DIR):
            return
        files = [f[:-4] for f in os.listdir(LOGS_DIR) if f.endswith(".log")]
        if files:
            self._selector.configure(values=files)
        else:
            self._selector.configure(values=["(none)"])

    def _switch_log(self, username):
        path = os.path.join(LOGS_DIR, f"{username}.log")
        if os.path.exists(path):
            self._current_file = path
            self._last_size = 0
            self._log_box.delete("1.0", "end")
            self._read_log()

    def _read_log(self):
        if not self._current_file or not os.path.exists(self._current_file):
            return
        size = os.path.getsize(self._current_file)
        if size == self._last_size:
            return
        with open(self._current_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._last_size)
            new_text = f.read()
        if new_text:
            self._log_box.insert("end", new_text)
            self._log_box.see("end")
        self._last_size = size

    def _start_poll(self):
        self._polling = True
        self._poll()

    def _poll(self):
        if self._polling:
            self._read_log()
            self.after(2000, self._poll)

    def stop_poll(self):
        self._polling = False


# ══════════════════════════════════════════════════════════════════════════════
#  Main App Window
# ══════════════════════════════════════════════════════════════════════════════

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ThreadsBotV2")
        self.geometry("1200x800")
        self.minsize(900, 600)
        self.configure(fg_color=BG_DARK)

        self._coordinator_thread = None
        self._stop_event = threading.Event()

        self._build()

    def _build(self):
        # ── top header ──
        header = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(header, text="ThreadsBotV2",
                     font=("Segoe UI", 18, "bold"), text_color=ACCENT).pack(side="left", padx=20)

        # right controls
        ctrl = ctk.CTkFrame(header, fg_color="transparent")
        ctrl.pack(side="right", padx=16)

        self._active_lbl = ctk.CTkLabel(ctrl, text="● 0 running",
                                        font=("Segoe UI", 12), text_color="#AAAAAA")
        self._active_lbl.pack(side="left", padx=12)

        self._stop_btn = ctk.CTkButton(ctrl, text="⏹ Stop", width=90,
                                       fg_color="#882222", hover_color="#AA3333",
                                       state="disabled", command=self._stop_all)
        self._stop_btn.pack(side="left", padx=4)

        self._start_btn = ctk.CTkButton(ctrl, text="▶ Start", width=90,
                                        fg_color="#115511", hover_color="#1A7A1A",
                                        command=self._start_all)
        self._start_btn.pack(side="left", padx=4)

        # ── tabs ──
        self._tabs = ctk.CTkTabview(self, fg_color=BG_PANEL,
                                    segmented_button_fg_color=BG_CARD,
                                    segmented_button_selected_color=ACCENT,
                                    segmented_button_selected_hover_color="#1A8CD8")
        self._tabs.pack(fill="both", expand=True, padx=0, pady=0)

        for name in ("Accounts", "Follow Lists", "Settings", "Bio Generator", "Logs"):
            self._tabs.add(name)

        self.accounts_tab    = AccountsTab(self._tabs.tab("Accounts"))
        self.follow_lists_tab = FollowListsTab(self._tabs.tab("Follow Lists"))
        self.settings_tab    = SettingsTab(self._tabs.tab("Settings"))
        self.bio_tab         = BioGenTab(self._tabs.tab("Bio Generator"), self.settings_tab)
        self.logs_tab        = LogsTab(self._tabs.tab("Logs"))

        self.accounts_tab.pack(fill="both", expand=True)
        self.follow_lists_tab.pack(fill="both", expand=True)
        self.settings_tab.pack(fill="both", expand=True)
        self.bio_tab.pack(fill="both", expand=True)
        self.logs_tab.pack(fill="both", expand=True)

        # status bar
        self._status_bar = ctk.CTkLabel(self, text="Ready",
                                        font=("Segoe UI", 10), text_color="#555555",
                                        fg_color=BG_CARD, anchor="w")
        self._status_bar.pack(fill="x", side="bottom", padx=0)

        self._poll_status()

    # ── start / stop ────────────────────────────────────────────────────────

    def _start_all(self):
        accounts = self.accounts_tab.get_accounts()
        if not accounts:
            messagebox.showwarning("No accounts", "Add at least one account before starting.")
            return

        settings = self.settings_tab.get_settings()
        self._apply_settings(settings)

        self._stop_event.clear()
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._set_status("Bot running…")

        self._coordinator_thread = threading.Thread(
            target=self._run_coordinator, daemon=True
        )
        self._coordinator_thread.start()

    def _stop_all(self):
        self._stop_event.set()
        self._set_status("Stopping…")
        self._stop_btn.configure(state="disabled")
        self.after(3000, lambda: self._start_btn.configure(state="normal"))

    def _run_coordinator(self):
        import data.db as db
        from coordinator import Coordinator

        db.init_db()
        db.load_accounts_from_file(ACCOUNTS_FILE)

        c = Coordinator(
            accounts_file=ACCOUNTS_FILE,
            batch_size=int(self.settings_tab.batch_size.get()),
            stop_event=self._stop_event,
        )
        c.run()
        self.after(0, self._on_coordinator_done)

    def _on_coordinator_done(self):
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._set_status("Finished")

    # ── settings → config ───────────────────────────────────────────────────

    def _apply_settings(self, s: dict):
        import config
        config.SESSION_DURATION        = s["session_duration"] * 60
        config.OUTREACH_COMMENTS_MIN   = s["comments_min"]
        config.OUTREACH_COMMENTS_MAX   = s["comments_max"]
        config.PIC_COMMENT_RATIO       = s.get("pic_comment_ratio", 20)
        config.FOLLOW_BATCH_SIZE       = s["follow_batch"]
        config.UNFOLLOW_AFTER_SECONDS  = s["unfollow_hours"] * 3600
        config.WARMUP_DURATION         = s["warmup_duration"] * 60
        config.WARMUP_LIKES_MIN        = s.get("warmup_likes_min", 2)
        config.WARMUP_LIKES_MAX        = s.get("warmup_likes_max", 6)
        config.WARMUP_MAX_FOLLOWS      = s.get("warmup_max_follows", 8)
        config.ACTIVE_LIKES_MIN        = s.get("active_likes_min", 1)
        config.ACTIVE_LIKES_MAX        = s.get("active_likes_max", 5)
        config.TEXT_POSTS_PER_CYCLE    = s.get("text_posts", 4)
        config.MOTHER_POST_URL         = s["mother_post_url"]
        config.BATCH_SIZE              = s["batch_size"]
        config.ADSPOWER_API_KEY        = s.get("adspower_api_key", "")
        config.GEMINI_API_KEY          = s.get("gemini_key", "")

        raw_warmup = [h.strip().lstrip("@") for h in s.get("warmup_targets", "").split(",") if h.strip()]
        if raw_warmup:
            config.WARMUP_TARGETS = [f"https://www.threads.net/@{h}" for h in raw_warmup]

        raw_outreach = [h.strip().lstrip("@") for h in s.get("outreach_targets", "").split(",") if h.strip()]
        if raw_outreach:
            config.OUTREACH_TARGETS = [f"https://www.threads.net/@{h}" for h in raw_outreach]

        # Write to disk so child worker processes pick up the same values
        active_path = os.path.join(PRESETS_DIR, "active.json")
        with open(active_path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)

        raw_outreach = [h.strip().lstrip("@") for h in s["outreach_targets"].split(",") if h.strip()]
        if raw_outreach:
            config.OUTREACH_TARGETS = [f"https://www.threads.net/@{h}" for h in raw_outreach]

    # ── status polling ───────────────────────────────────────────────────────

    def _poll_status(self):
        try:
            import data.db as db
            db.init_db()
            rows = db.get_pending_accounts(9999)
            running = sum(1 for r in rows if r.get("state") == "running")
            self._active_lbl.configure(
                text=f"● {running} running",
                text_color="#44FF88" if running > 0 else "#AAAAAA"
            )
            # refresh log account list
            self.logs_tab._refresh_accounts()
        except Exception:
            pass
        self.after(5000, self._poll_status)

    def _set_status(self, msg: str):
        self._status_bar.configure(text=f"  {msg}")


def launch():
    app = App()
    app.mainloop()
