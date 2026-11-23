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


def make_icon(icon: str, size: int = 28) -> Gtk.Image:
    if icon and os.path.isabs(icon) and os.path.exists(icon):
        gicon = Gio.FileIcon.new(Gio.File.new_for_path(icon))
    else:
        names = [icon] if icon else []
        if icon and not icon.endswith("-symbolic"):
            names.append(icon + "-symbolic")
        names.append("system-run-symbolic" if icon.endswith("-symbolic") else "application-x-executable")
        gicon = Gio.ThemedIcon.new_from_names(names)
    img = Gtk.Image.new_from_gicon(gicon)
    img.set_pixel_size(20 if icon.endswith("-symbolic") else size)
    img.set_size_request(size, size)
    return img


def keycap(text: str) -> Gtk.Label:
    l = Gtk.Label(label=text)
    l.add_css_class("keycap")
    return l


class ResultRow(Gtk.ListBoxRow):
    def __init__(self, result: Result, index: int, is_action: bool = False):
        super().__init__()
        self.result = result
        self.set_focusable(False)
        box = Gtk.Box(spacing=12)
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

        right = Gtk.Box(spacing=8)
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
        self.slow_timer = 0
        self.mode = "search"              # search | actions | detail
        self.action_owner: Result | None = None
        self.pending_confirm: tuple | None = None
        self.toast_timer = 0
        self._suppress_hide = False

        self._build()
        self._keys()
        self.connect("notify::is-active", self._on_active_changed)

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
        if self.slow_timer:
            GLib.source_remove(self.slow_timer)
            self.slow_timer = 0
        if text.strip() and any(p.slow for p in self.engine._active(text)):
            self.spinner.start()
            gen = self.generation
            self.slow_timer = GLib.timeout_add(SLOW_DEBOUNCE_MS, self._start_slow, text, gen)
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
        self.mode_icon.set_from_icon_name(name)

    def _start_slow(self, text, gen):
        self.slow_timer = 0

        def work():
            try:
                res = self.engine.search(text, slow=True)
            except Exception:
                log.exception("slow search failed")
                res = []
            GLib.idle_add(self._slow_done, res, gen)

        threading.Thread(target=work, daemon=True).start()
        return False

    def _slow_done(self, res, gen):
        if gen != self.generation or self.mode != "search":
            return False
        self.spinner.stop()
        if res:
            self._set_results(dedupe_sort(self.fast_results + res), keep_selection=True)
        return False

    def _set_results(self, results: list[Result], keep_selection: bool, is_action: bool = False):
        prev = self.selected_index() if keep_selection else 0
        limit = 60 if is_action or self.entry.get_text().startswith(("/", "~", ":", "f ")) else 40
        self.results = results[:limit]
        self.listbox.remove_all()
        for i, r in enumerate(self.results):
            self.listbox.append(ResultRow(r, i, is_action))
        if self.mode == "actions":
            self.section.set_label(f"Actions · {self.action_owner.title}" if self.action_owner else "Actions")
        elif not self.entry.get_text().strip():
            self.section.set_label("Recent & Suggestions")
        else:
            self.section.set_label("Results")
        if self.results:
            self.stack.set_visible_child_name("list")
            self.select(min(prev, len(self.results) - 1))
        elif self.entry.get_text().strip():
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

    def show_detail(self, text: str):
        self.mode = "detail"
        self.detail.get_buffer().set_text(text)
        self.stack.set_visible_child_name("detail")
        self.primary_label.set_label("Back: Esc ·  Copy")
        self._detail_text = text

    def copy(self, text: str):
        clip = Gdk.Display.get_default().get_clipboard()
        clip.set(text)
        notify("Copied to clipboard", text[:120]) if not self.get_visible() else None
        self.set_toast("Copied to clipboard")

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
