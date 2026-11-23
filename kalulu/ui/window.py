"""The launcher window."""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core.engine import Engine, dedupe_sort  # noqa: E402
from ..core.models import Action, Result  # noqa: E402
from ..core.system import exec_to_argv, notify, spawn, terminal_argv  # noqa: E402

log = logging.getLogger("kalulu.ui")

WIDTH, HEIGHT = 760, 476
SLOW_DEBOUNCE_MS = 140
ICON_SLOT = 28


def make_icon(icon: str, size: int = ICON_SLOT) -> Gtk.Widget:
    """Full-colour icons (apps, file types) fill the slot. Monochrome symbolic icons sit centred on a
    soft rounded tile of the same size, so every row's icon has the same footprint and baseline."""
    from .icons import resolver
    gicon, _how = resolver().resolve(icon)
    img = Gtk.Image.new_from_gicon(gicon)
    symbolic = isinstance(gicon, Gio.ThemedIcon) and gicon.get_names()[0].endswith("-symbolic")
    if not symbolic:
        img.set_pixel_size(size)
        img.set_size_request(size, size)
        img.set_valign(Gtk.Align.CENTER)
        return img
    img.set_pixel_size(16)
    img.set_hexpand(True)
    img.set_vexpand(True)
    tile = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    tile.set_hexpand(False)                   # stop the image's hexpand leaking into the row
    tile.set_vexpand(False)
    tile.add_css_class("kalulu-icon-tile")
    tile.set_size_request(size, size)
    tile.append(img)
    return tile


def keycap(text: str) -> Gtk.Label:
    l = Gtk.Label(label=text)
    l.add_css_class("keycap")
    return l


class ResultRow(Gtk.ListBoxRow):
    def __init__(self, result: Result, index: int, is_action: bool = False):
        super().__init__()
        self.result = result
        self.set_focusable(False)
        box = Gtk.Box(spacing=14)
        box.append(make_icon(result.icon))

        texts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, hexpand=True)
        title = Gtk.Label(label=result.title, xalign=0, ellipsize=3)
        title.add_css_class("kalulu-calc" if result.category == "Calculator" else "kalulu-title")
        texts.append(title)
        if result.subtitle:
            sub = Gtk.Label(label=result.subtitle, xalign=0, ellipsize=3, hexpand=True)
            sub.add_css_class("kalulu-subtitle")
            texts.append(sub)
        else:
            title.set_hexpand(True)
        box.append(texts)

        right = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        if is_action and result.actions and result.actions[0].shortcut:
            right.append(keycap(result.actions[0].shortcut))
        elif result.category:
            acc = Gtk.Label(label=result.category)
            acc.add_css_class("kalulu-accessory")
            right.append(acc)
        if index < 9 and not is_action:
            right.append(keycap(f"Alt {index + 1}"))
        box.append(right)
        self.set_child(box)


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app, engine: Engine):
        super().__init__(application=app, title="Kalulu")
        self.engine = engine
        self.set_default_size(WIDTH, HEIGHT)
        self.set_resizable(False)
        self.add_css_class("kalulu")
        self.set_hide_on_close(True)

        self.results: list[Result] = []
        self.fast_results: list[Result] = []
        self.generation = 0
        self.slow_timers: list[int] = []
        self.slow_acc: list[Result] = []
        self.slow_pending = 0
        self.mode = "search"              # search | actions | detail
        self.action_owner: Result | None = None
        self.pending_confirm: tuple | None = None
        self.toast_timer = 0
        self._suppress_hide = False
        self._last_clip_text: str | None = None

        self._build()
        self._keys()
        self.connect("notify::is-active", self._on_active_changed)
        # Clipboard history. X11: fires for every copy. Wayland: only while Kalulu has focus, so the
        # GNOME Shell extension (data/gnome-shell) reports the rest through the app's clip-add action.
        self._clipboard = Gdk.Display.get_default().get_clipboard()
        self._clipboard.connect("changed", lambda *_: self._capture_clipboard())
        GLib.idle_add(self._capture_clipboard)

    # ------------------------------------------------------------------ build
    def _build(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Gtk.Box(spacing=10)
        header.add_css_class("kalulu-header")
        self.mode_icon = Gtk.Image.new_from_icon_name("system-search-symbolic")
        self.mode_icon.set_pixel_size(20)
        self.mode_icon.set_opacity(0.6)
        header.append(self.mode_icon)
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Search apps, files and commands…")
        self.entry.add_css_class("kalulu-entry")
        self.entry.add_css_class("flat")
        self.entry.connect("changed", self._on_changed)
        self.entry.connect("activate", lambda *_: self.activate_selected())
        header.append(self.entry)
        self.spinner = Gtk.Spinner()
        header.append(self.spinner)
        root.append(header)
        root.append(Gtk.Separator())

        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=80)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("kalulu-list")
        self.listbox.set_can_focus(False)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.connect("row-activated", lambda _lb, row: self.activate_selected(row))
        self.section = Gtk.Label(xalign=0)
        self.section.add_css_class("kalulu-section")
        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.append(self.section)
        list_box.append(self.listbox)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=list_box)
        self.stack.add_named(self.scroller, "list")

        self.detail = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                   cursor_visible=False)
        self.detail.add_css_class("kalulu-detail")
        self.stack.add_named(Gtk.ScrolledWindow(child=self.detail), "detail")

        empty = Adw.StatusPage(icon_name="edit-find-symbolic", title="No results",
                               description="Try another search, or prefix with  >  : f  ~/")
        self.stack.add_named(empty, "empty")
        root.append(self.stack)
        root.append(Gtk.Separator())

        footer = Gtk.Box(spacing=8)
        footer.add_css_class("kalulu-footer")
        logo = Gtk.Image.new_from_icon_name("dev.kalulu.Launcher")
        logo.set_pixel_size(16)
        footer.append(logo)
        self.toast = Gtk.Label(label="Kalulu", xalign=0, hexpand=True, ellipsize=3)
        self.toast.add_css_class("kalulu-toast")
        footer.append(self.toast)
        self.primary_label = Gtk.Label()
        footer.append(self.primary_label)
        footer.append(keycap("↵"))
        footer.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        footer.append(Gtk.Label(label="Actions"))
        footer.append(keycap("Ctrl K"))
        root.append(footer)

        self.set_content(root)

    def _keys(self):
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("key-pressed", self._on_key)
        self.add_controller(ctrl)

    # ------------------------------------------------------------------ show / hide
    def show_launcher(self, query: str | None = None):
        if self.get_visible() and not self.is_active():
            # re-map instead of re-focusing: Wayland compositors give focus to newly mapped
            # windows, but may refuse focus requests from an already-mapped one.
            self.set_visible(False)
        self.reset(query or "")
        self.present()
        self.entry.grab_focus_without_selecting()
        self.entry.set_position(-1)
        GLib.idle_add(self._idle_refresh)

    def _idle_refresh(self):
        self.engine.refresh()
        return False

    def hide_launcher(self):
        self.set_visible(False)

    def toggle(self):
        if self.get_visible() and self.is_active():
            self.hide_launcher()
        else:
            self.show_launcher()

    def reset(self, query: str = ""):
        self.mode = "search"
        self.action_owner = None
        self.pending_confirm = None
        self.stack.set_visible_child_name("list")
        if self.entry.get_text() == query:
            self._on_changed(self.entry)
        else:
            self.entry.set_text(query)
        self.entry.set_position(-1)
        self.set_toast("Kalulu")

    def _on_active_changed(self, *_):
        if self.is_active():
            self._capture_clipboard()
        if self.is_active() or not self.get_visible() or self._suppress_hide:
            return
        if not self.engine.ctx.config.get("general", {}).get("hide_on_focus_loss", True):
            return
        GLib.timeout_add(90, self._hide_if_still_inactive)

    def _hide_if_still_inactive(self):
        if not self.is_active() and self.get_visible():
            self.hide_launcher()
        return False

    # ------------------------------------------------------------------ search
    def _on_changed(self, entry):
        if self.mode == "actions":
            self._filter_actions(entry.get_text())
            return
        if self.mode == "detail":
            self.mode = "search"
            self.stack.set_visible_child_name("list")
        text = entry.get_text()
        self.pending_confirm = None
        self.generation += 1
        self.fast_results = self.engine.search(text)
        self._set_results(self.fast_results, keep_selection=False)
        self._update_mode_icon(text)
        for t in self.slow_timers:
            if not t[1]:
                GLib.source_remove(t[0])
        self.slow_timers = []
        self.slow_acc = []
        providers = self.engine.slow_providers(text)
        self.slow_pending = len(providers)
        if providers:
            self.spinner.start()
            gen = self.generation
            for p in providers:
                slot = [0, False]
                slot[0] = GLib.timeout_add(p.delay_ms, self._start_slow, p, text, gen, slot)
                self.slow_timers.append(slot)
        else:
            self.spinner.stop()

    def _update_mode_icon(self, text: str):
        name = "system-search-symbolic"
        if text.startswith(">"):
            name = "utilities-terminal-symbolic"
        elif text.startswith(":"):
            name = "face-smile-symbolic"
        elif text.startswith(("/", "~", "f ")):
            name = "folder-symbolic"
        elif text.startswith("?") or text.startswith("define "):
            name = "dialog-question-symbolic"
        elif text.startswith(("install ", "uninstall ", "remove ")):
            name = "system-software-install-symbolic"
        elif text.startswith("clip "):
            name = "edit-paste-symbolic"
        self.mode_icon.set_from_icon_name(name)

    def _start_slow(self, provider, text, gen, slot):
        slot[1] = True
        def work():
            try:
                res = self.engine.search_one(provider, text)
            except Exception:
                log.exception("slow provider %s failed", provider.name)
                res = []
            GLib.idle_add(self._slow_done, res, gen)

        if gen == self.generation:
            threading.Thread(target=work, daemon=True).start()
        return False

    def _slow_done(self, res, gen):
        if gen != self.generation or self.mode != "search":
            return False
        self.slow_pending -= 1
        if self.slow_pending <= 0:
            self.spinner.stop()
        if res:
            self.slow_acc += res
            self._set_results(dedupe_sort(self.fast_results + self.slow_acc), keep_selection=True)
        elif self.slow_pending <= 0 and not self.results and self.entry.get_text().strip():
            self.stack.set_visible_child_name("empty")
        return False

    def _set_results(self, results: list[Result], keep_selection: bool, is_action: bool = False):
        prev = 0
        if keep_selection and self.results:
            cur = self.selected_result()
            ids = [r.id for r in results]
            prev = ids.index(cur.id) if cur and cur.id in ids and self.selected_index() > 0 else 0
        limit = 60 if is_action or self.entry.get_text().startswith(("/", "~", ":", "f ", "install ")) else 40
        if self.entry.get_text().startswith("clip ") and not is_action:
            limit = 200
        self.results = results[:limit]
        self.listbox.remove_all()
        for i, r in enumerate(self.results):
            self.listbox.append(ResultRow(r, i, is_action))
        if self.mode == "actions":
            self.section.set_label(f"Actions · {self.action_owner.title}" if self.action_owner else "Actions")
        elif not self.entry.get_text().strip():
            self.section.set_label("Recent & Suggestions")
        elif self.entry.get_text().startswith("clip "):
            n = len(self.engine.ctx.clips) if self.engine.ctx.clips is not None else 0
            self.section.set_label(f"Clipboard History · {n} saved")
        else:
            self.section.set_label("Results")
        if self.results:
            self.stack.set_visible_child_name("list")
            self.select(min(prev, len(self.results) - 1))
        elif self.entry.get_text().strip() and not self.slow_pending:
            self.stack.set_visible_child_name("empty")
            self.primary_label.set_label("")

    # ------------------------------------------------------------------ selection
    def selected_index(self) -> int:
        row = self.listbox.get_selected_row()
        return row.get_index() if row else 0

    def selected_result(self) -> Result | None:
        row = self.listbox.get_selected_row()
        return row.result if row else (self.results[0] if self.results else None)

    def select(self, i: int):
        if not self.results:
            return
        i = max(0, min(i, len(self.results) - 1))
        row = self.listbox.get_row_at_index(i)
        self.listbox.select_row(row)
        # keep the selected row visible
        adj = self.scroller.get_vadjustment()
        ok, rect = row.compute_bounds(self.listbox)
        if ok:
            y, h = rect.get_y(), rect.get_height()
            if y < adj.get_value():
                adj.set_value(max(0, y - 30))
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size() + 8)
        r = self.results[i]
        self.primary_label.set_label(r.primary.title if r.primary else "")
        self.pending_confirm = None

    def move(self, delta: int):
        self.select(self.selected_index() + delta)

    # ------------------------------------------------------------------ actions panel
    def open_actions(self):
        r = self.selected_result()
        if not r or not r.actions:
            return
        self.mode = "actions"
        self.action_owner = r
        self._saved_query = self.entry.get_text()
        self.entry.set_text("")
        self.entry.set_placeholder_text("Search actions…")
        self._filter_actions("")

    def _action_results(self) -> list[Result]:
        r = self.action_owner
        hints = ["↵", "Ctrl ↵"]
        out = []
        for i, a in enumerate(r.actions):
            a2 = Action(**{**a.__dict__})
            if not a2.shortcut and i < len(hints):
                a2.shortcut = hints[i]
            out.append(Result(id=f"action:{i}", title=a.title, icon=_action_icon(a), actions=[a2],
                              category="", learn=False))
        return out

    def _filter_actions(self, text: str):
        from ..core import fuzzy
        acts = self._action_results()
        if text.strip():
            acts = [a for a in acts if fuzzy.score(text, a.title) is not None]
        self._set_results(acts, keep_selection=False, is_action=True)

    def close_actions(self):
        self.mode = "search"
        owner = self.action_owner
        self.action_owner = None
        self.entry.set_placeholder_text("Search apps, files and commands…")
        self.entry.set_text(getattr(self, "_saved_query", ""))
        self.entry.set_position(-1)
        if owner:
            for i, r in enumerate(self.results):
                if r.id == owner.id:
                    self.select(i)

    # ------------------------------------------------------------------ run
    def activate_selected(self, row=None, alt: int = 0):
        if self.mode == "detail":
            return
        r = row.result if row else self.selected_result()
        if not r or not r.actions:
            return
        if self.mode == "actions":
            owner, action = self.action_owner, r.actions[0]
            self.run(owner, action)
            return
        idx = min(alt, len(r.actions) - 1)
        self.run(r, r.actions[idx])

    def run(self, result: Result, action: Action):
        query = getattr(self, "_saved_query", "") if self.mode == "actions" else self.entry.get_text()
        if action.confirm and self.pending_confirm != (result.id, action.title):
            self.pending_confirm = (result.id, action.title)
            self.set_toast(f"Press ↵ again to {action.title.lower()}", warn=True, timeout=4000)
            return
        self.pending_confirm = None
        if action.kind not in ("complete", "internal"):
            self.engine.record(result, query)
        k, pl = action.kind, action.payload
        try:
            if k == "complete":
                if self.mode == "actions":
                    self.mode = "search"
                    self.entry.set_placeholder_text("Search apps, files and commands…")
                self.entry.set_text(pl)
                self.entry.set_position(-1)
                return
            if k == "internal":
                if pl == "reload":
                    self.engine.reload()
                    self.reset("")
                    self.set_toast(self.engine.ctx.config_error or "Reloaded", warn=bool(self.engine.ctx.config_error))
                elif pl == "quit":
                    self.get_application().quit()
                return
            if k == "copy":
                self.copy(pl)
            elif k == "launch_app":
                self._launch_app(pl)
            elif k == "open_uri":
                self._open(pl)
            elif k == "reveal":
                self._reveal(pl)
            elif k == "detail":
                self.show_detail(pl)
                return
            elif k in ("script", "callback"):
                self._run_async(action)
                if not action.close:
                    return
            else:
                self.engine.execute(action)
        except Exception as e:
            log.exception("action failed")
            self.set_toast(f"Failed: {e}", warn=True)
            return
        if action.close:
            self.hide_launcher()

    def _run_async(self, action: Action):
        self.spinner.start()
        label = action.payload.get("title") if isinstance(action.payload, dict) else None
        self.set_toast(f"Running {label or action.title}…")
        close = action.close

        def work():
            try:
                outcome = self.engine.execute(action)
            except Exception as e:
                log.exception("async action failed")
                outcome = ("toast", f"Failed: {e}")
            GLib.idle_add(done, outcome)

        def done(outcome):
            self.spinner.stop()
            if not outcome:
                self.set_toast("Done")
                return False
            kind, text = outcome
            if kind == "refresh":                 # e.g. a clipboard entry was pinned or deleted
                if self.mode == "actions":
                    self.close_actions()
                else:
                    self._on_changed(self.entry)
                self.set_toast(text)
                return False
            if kind == "detail":
                if not self.get_visible():
                    self.show_launcher()
                self.show_detail(text)
                self.set_toast("Kalulu")
            elif close or not self.get_visible():
                notify(action.payload.get("title", "Kalulu") if isinstance(action.payload, dict) else "Kalulu", text)
            else:
                self.set_toast(text)
            return False

        threading.Thread(target=work, daemon=True).start()

    def show_detail(self, content):
        """content: plain text (script output, monospace) or a dict {title, subtitle, body, source} (prose)."""
        self.mode = "detail"
        buf = self.detail.get_buffer()
        buf.set_text("")
        if isinstance(content, dict):
            self.detail.set_monospace(False)
            self.detail.add_css_class("kalulu-prose")
            tags = self._detail_tags(buf)
            end = buf.get_end_iter
            buf.insert_with_tags(end(), content.get("title", "") + "\n", tags["title"])
            if content.get("subtitle"):
                buf.insert_with_tags(end(), content["subtitle"] + "\n", tags["dim"])
            buf.insert(end(), "\n" + content.get("body", "") + "\n")
            if content.get("source"):
                buf.insert_with_tags(end(), "\n" + content["source"], tags["small"])
            text = f"{content.get('title', '')}\n\n{content.get('body', '')}"
        else:
            self.detail.set_monospace(True)
            self.detail.remove_css_class("kalulu-prose")
            buf.set_text(content)
            text = content
        self.stack.set_visible_child_name("detail")
        self.primary_label.set_label("Back: Esc ·  Copy")
        self._detail_text = text

    def _detail_tags(self, buf):
        table = buf.get_tag_table()
        if not table.lookup("k-title"):
            buf.create_tag("k-title", weight=700, scale=1.5, pixels_below_lines=2)
            buf.create_tag("k-dim", style=2)
            buf.create_tag("k-small", scale=0.85, style=2)
        return {"title": table.lookup("k-title"), "dim": table.lookup("k-dim"), "small": table.lookup("k-small")}

    def copy(self, text: str):
        self._clipboard.set(text)
        self._remember_clip(text)
        notify("Copied to clipboard", text[:120]) if not self.get_visible() else None
        self.set_toast("Copied to clipboard")

    # ------------------------------------------------------------------ clipboard history
    def _capture_clipboard(self):
        if self.engine.ctx.clips is None or self._clipboard.is_local():
            return False
        formats = self._clipboard.get_formats()
        if formats and formats.contain_mime_type("x-kde-passwordManagerHint"):
            return False                      # password managers flag secrets this way; never store them
        self._clipboard.read_text_async(None, self._on_clip_text)
        return False

    def _on_clip_text(self, clip, res):
        try:
            text = clip.read_text_finish(res)
        except GLib.Error:
            return                            # not text (an image, a file) or the owner went away
        self._remember_clip(text)

    def _remember_clip(self, text: str | None):
        # Only record a change: reading the same clipboard again on focus must not resurrect an
        # entry the user just deleted, or bump it back to the top.
        if not text or text == self._last_clip_text:
            return
        self._last_clip_text = text
        store = self.engine.ctx.clips
        if store is not None and store.add(text):
            self.on_clip_added()

    def on_clip_added(self):
        if self.get_visible() and self.mode == "search" and self.entry.get_text().startswith("clip "):
            self._on_changed(self.entry)

    def _ctx(self):
        return Gdk.Display.get_default().get_app_launch_context()

    def _launch_app(self, pl: dict):
        info = None
        try:
            info = Gio.DesktopAppInfo.new_from_filename(pl["path"])
        except TypeError:
            info = None
        if info:
            if pl.get("action"):
                info.launch_action(pl["action"], self._ctx())
            else:
                info.launch([], self._ctx())
            return
        argv = exec_to_argv(pl["exec"])
        spawn(terminal_argv(" ".join(argv), self.engine.terminal(), hold=False) if pl.get("terminal") else argv)

    def _open(self, target: str):
        uri = target if "://" in target else Gio.File.new_for_path(os.path.expanduser(target)).get_uri()
        Gio.AppInfo.launch_default_for_uri(uri, self._ctx())

    def _reveal(self, path: str):
        uri = Gio.File.new_for_path(path).get_uri()
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                          "org.freedesktop.FileManager1", "ShowItems",
                          GLib.Variant("(ass)", ([uri], "")), None, Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            self._open(str(Path(path).parent))

    # ------------------------------------------------------------------ toast
    def set_toast(self, text: str, warn: bool = False, timeout: int = 2500):
        self.toast.set_label(text)
        if warn:
            self.toast.add_css_class("kalulu-warn")
        else:
            self.toast.remove_css_class("kalulu-warn")
        if self.toast_timer:
            GLib.source_remove(self.toast_timer)
            self.toast_timer = 0
        if text != "Kalulu":
            self.toast_timer = GLib.timeout_add(timeout, self._clear_toast)

    def _clear_toast(self):
        self.toast_timer = 0
        self.toast.set_label("Kalulu")
        self.toast.remove_css_class("kalulu-warn")
        return False

    # ------------------------------------------------------------------ keys
    def _on_key(self, _ctrl, keyval, _code, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        name = Gdk.keyval_name(keyval) or ""

        if name == "Escape":
            if self.mode == "actions":
                self.close_actions()
            elif self.mode == "detail":
                self.mode = "search"
                self.stack.set_visible_child_name("list")
                self.select(self.selected_index())
            elif self.entry.get_text():
                self.entry.set_text("")
            else:
                self.hide_launcher()
            return True
        if name in ("Down",) or (ctrl and name in ("n", "j")):
            self.move(1)
            return True
        if name in ("Up",) or (ctrl and name in ("p",)):
            self.move(-1)
            return True
        if name == "Page_Down":
            self.move(5)
            return True
        if name == "Page_Up":
            self.move(-5)
            return True
        if ctrl and name == "k":
            self.close_actions() if self.mode == "actions" else self.open_actions()
            return True
        if name in ("Return", "KP_Enter"):
            if self.mode == "detail":
                self.copy(getattr(self, "_detail_text", ""))
                return True
            self.activate_selected(alt=1 if ctrl else (2 if state & Gdk.ModifierType.SHIFT_MASK else 0))
            return True
        if name == "Tab":
            r = self.selected_result()
            if r and r.primary and r.primary.kind == "complete":
                self.run(r, r.primary)
            elif r and self.mode == "search" and r.category in ("Application", "Command", "Script"):
                self.entry.set_text(r.title)
                self.entry.set_position(-1)
            return True
        if alt and name.isdigit() and name != "0":
            i = int(name) - 1
            if i < len(self.results):
                self.select(i)
                self.activate_selected()
            return True
        if ctrl and name == "comma":
            self.run(Result(id="kalulu:config", title="Settings"), Action("Open", "open_uri",
                                                                          str(__import__("kalulu.core.paths", fromlist=["x"]).CONFIG_FILE)))
            return True
        if ctrl and name == "r":
            self.run(Result(id="kalulu:reload", title="Reload"), Action("Reload", "internal", "reload", close=False))
            return True
        return False


def _action_icon(a: Action) -> str:
    return {
        "copy": "edit-copy-symbolic", "launch_app": "media-playback-start-symbolic",
        "open_uri": "document-open-symbolic", "reveal": "folder-open-symbolic",
        "terminal": "utilities-terminal-symbolic", "terminal_here": "utilities-terminal-symbolic",
        "script": "system-run-symbolic", "run": "system-run-symbolic", "complete": "go-next-symbolic",
        "callback": "emblem-ok-symbolic", "internal": "emblem-system-symbolic",
    }.get(a.kind, "go-next-symbolic")
