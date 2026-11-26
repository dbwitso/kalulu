// Kalulu Windows — window switching and keyboard tiling for the Kalulu launcher.
//
// On Wayland only the Shell knows which windows exist and may move them, so this extension:
//   * publishes dev.kalulu.Windows on the session bus (/dev/kalulu/Windows) for Kalulu to list,
//     focus, close and tile windows;
//   * remembers the last window you used before opening Kalulu, so "Left Half" in Kalulu acts on
//     that window and not on Kalulu itself;
//   * adds tiling hotkeys (Ctrl+Super+… by default) that work even when Kalulu isn't running.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const KALULU_APP_ID = 'dev.kalulu.Launcher';
const BUS_NAME = 'dev.kalulu.Windows';
const OBJECT_PATH = '/dev/kalulu/Windows';
const VERSION = '1';

const IFACE = `
<node>
  <interface name="dev.kalulu.Windows">
    <method name="Version"><arg type="s" direction="out" name="version"/></method>
    <method name="ListWindows"><arg type="s" direction="out" name="json"/></method>
    <method name="ActivateWindow">
      <arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/>
    </method>
    <method name="CloseWindow">
      <arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/>
    </method>
    <method name="MinimizeWindow">
      <arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/>
    </method>
    <method name="QuitApp">
      <arg type="t" direction="in" name="id"/><arg type="b" direction="out" name="ok"/>
    </method>
    <method name="Tile">
      <arg type="s" direction="in" name="layout"/>
      <arg type="t" direction="in" name="id"/>
      <arg type="s" direction="out" name="error"/>
    </method>
  </interface>
</node>`;

// Fractions of the work area: [x, y, w, h]
const CELLS = {
    'left-half': [0, 0, 1 / 2, 1], 'right-half': [1 / 2, 0, 1 / 2, 1],
    'top-half': [0, 0, 1, 1 / 2], 'bottom-half': [0, 1 / 2, 1, 1 / 2],
    'top-left': [0, 0, 1 / 2, 1 / 2], 'top-right': [1 / 2, 0, 1 / 2, 1 / 2],
    'bottom-left': [0, 1 / 2, 1 / 2, 1 / 2], 'bottom-right': [1 / 2, 1 / 2, 1 / 2, 1 / 2],
    'first-third': [0, 0, 1 / 3, 1], 'center-third': [1 / 3, 0, 1 / 3, 1], 'last-third': [2 / 3, 0, 1 / 3, 1],
    'first-two-thirds': [0, 0, 2 / 3, 1], 'last-two-thirds': [1 / 3, 0, 2 / 3, 1],
};
// Pressing the same half again cycles its size, like Rectangle on macOS.
const CYCLE = [1 / 2, 2 / 3, 1 / 3];
const CYCLING = new Set(['left-half', 'right-half', 'top-half', 'bottom-half']);
export const LAYOUTS = [...Object.keys(CELLS), 'maximize', 'almost-maximize', 'center', 'restore',
    'larger', 'smaller', 'next-display'];

function isKalulu(win) {
    if (!win)
        return false;
    const gtkId = win.get_gtk_application_id?.();
    return gtkId === KALULU_APP_ID || win.get_wm_class() === KALULU_APP_ID;
}

function isNormal(win) {
    return win && !win.skip_taskbar && win.get_window_type() === Meta.WindowType.NORMAL && !isKalulu(win);
}

function isMaximized(win) {
    if (typeof win.is_maximized === 'function')
        return win.is_maximized();
    return Boolean(win.maximized_horizontally || win.maximized_vertically);
}

// Mutter 49 dropped the MaximizeFlags argument; support both signatures.
function maximize(win) {
    try {
        win.maximize(Meta.MaximizeFlags.BOTH);
    } catch (_e) {
        win.maximize();
    }
}

function unmaximize(win) {
    try {
        win.unmaximize(Meta.MaximizeFlags.BOTH);
    } catch (_e) {
        win.unmaximize();
    }
}

function sameRect(a, b) {
    return a && b && Math.abs(a.x - b.x) <= 2 && Math.abs(a.y - b.y) <= 2 &&
        Math.abs(a.width - b.width) <= 2 && Math.abs(a.height - b.height) <= 2;
}

export default class KaluluWindowsExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._tracker = Shell.WindowTracker.get_default();
        this._lastWin = null;
        this._saved = new Map();          // window id -> rect before we first tiled it
        this._lastTile = null;            // {id, layout, step, rect}

        this._focusId = global.display.connect('notify::focus-window', () => {
            const w = global.display.focus_window;
            if (isNormal(w))
                this._lastWin = w;
        });
        if (isNormal(global.display.focus_window))
            this._lastWin = global.display.focus_window;

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE, null, null);

        this._bound = [];
        this._settingsIds = [
            this._settings.connect('changed::hotkeys-enabled', () => this._rebind()),
        ];
        this._rebind();
    }

    disable() {
        this._unbind();
        for (const id of this._settingsIds ?? [])
            this._settings.disconnect(id);
        this._settingsIds = [];
        if (this._focusId)
            global.display.disconnect(this._focusId);
        this._focusId = 0;
        if (this._nameId)
            Gio.bus_unown_name(this._nameId);
        this._nameId = 0;
        this._dbus?.unexport();
        this._dbus = null;
        this._saved = null;
        this._lastWin = null;
        this._lastTile = null;
        this._tracker = null;
        this._settings = null;
    }

    // ------------------------------------------------------------------ hotkeys
    _rebind() {
        this._unbind();
        if (!this._settings.get_boolean('hotkeys-enabled'))
            return;
        for (const layout of LAYOUTS) {
            const key = `tile-${layout}`;
            if (!this._settings.settings_schema.has_key(key))
                continue;
            const ok = Main.wm.addKeybinding(key, this._settings, Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL, () => this._tile(layout, null));
            if (ok !== Meta.KeyBindingAction.NONE)
                this._bound.push(key);
        }
    }

    _unbind() {
        for (const key of this._bound ?? [])
            Main.wm.removeKeybinding(key);
        this._bound = [];
    }

    // ------------------------------------------------------------------ helpers
    _windows() {
        // Most-recently-used first, across all workspaces.
        return global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null).filter(isNormal);
    }

    _byId(id) {
        if (!id)
            return null;
        return global.get_window_actors().map(a => a.meta_window).find(w => w && w.get_id() === id) ?? null;
    }

    _target(id) {
        if (id)
            return this._byId(id);
        const focus = global.display.focus_window;
        if (isNormal(focus))
            return focus;
        if (this._lastWin && this._windows().includes(this._lastWin))
            return this._lastWin;
        return this._windows()[0] ?? null;
    }

    _appInfo(win) {
        const app = this._tracker.get_window_app(win);
        if (!app)
            return {app_id: '', app_name: win.get_wm_class() ?? '', icon: ''};
        let icon = '';
        try {
            icon = app.get_icon()?.to_string() ?? '';
        } catch (_e) {}
        const appId = app.get_id() ?? '';
        return {app_id: appId.startsWith('window:') ? '' : appId, app_name: app.get_name() ?? '', icon};
    }

    // ------------------------------------------------------------------ D-Bus methods
    Version() {
        return VERSION;
    }

    ListWindows() {
        const last = this._target(0);
        const out = this._windows().map(w => ({
            id: w.get_id(),
            rect: (r => [r.x, r.y, r.width, r.height])(w.get_frame_rect()),
            title: w.get_title() ?? '',
            wm_class: w.get_wm_class() ?? '',
            workspace: w.get_workspace()?.index() ?? -1,
            monitor: w.get_monitor(),
            minimized: Boolean(w.minimized),
            last: w === last,
            ...this._appInfo(w),
        }));
        return JSON.stringify({
            windows: out,
            workspace: global.workspace_manager.get_active_workspace_index(),
            monitors: global.display.get_n_monitors(),
        });
    }

    ActivateWindow(id) {
        const w = this._byId(id);
        if (!w)
            return false;
        Main.activateWindow(w);
        return true;
    }

    CloseWindow(id) {
        const w = this._byId(id);
        if (!w)
            return false;
        w.delete(global.get_current_time());
        return true;
    }

    MinimizeWindow(id) {
        const w = this._byId(id);
        if (!w || !w.can_minimize())
            return false;
        w.minimize();
        return true;
    }

    QuitApp(id) {
        const w = this._byId(id);
        const app = w && this._tracker.get_window_app(w);
        if (!app)
            return false;
        app.request_quit();
        return true;
    }

    Tile(layout, id) {
        try {
            return this._tile(layout, id || null);
        } catch (e) {
            logError(e, 'kalulu-windows: tile failed');
            return String(e.message ?? e);
        }
    }

    // ------------------------------------------------------------------ tiling
    _tile(layout, id) {
        const win = this._target(id);
        if (!win)
            return 'No window to arrange';
        if (!LAYOUTS.includes(layout))
            return `Unknown layout ${layout}`;
        if (win.is_fullscreen())
            win.unmake_fullscreen();

        const wid = win.get_id();
        if (layout === 'next-display') {
            const n = global.display.get_n_monitors();
            if (n < 2)
                return 'Only one display connected';
            win.move_to_monitor((win.get_monitor() + 1) % n);
            return '';
        }
        if (layout === 'maximize' && this._settings.get_int('gap') === 0) {
            this._remember(win);
            maximize(win);
            this._lastTile = null;
            return '';
        }
        if (layout === 'restore') {
            const r = this._saved.get(wid);
            if (isMaximized(win))
                unmaximize(win);
            if (r) {
                win.move_resize_frame(true, r.x, r.y, r.width, r.height);
                this._saved.delete(wid);
            }
            this._lastTile = null;
            return '';
        }

        if (isMaximized(win))
            unmaximize(win);
        this._remember(win);

        const wa = win.get_work_area_current_monitor();
        const cur = win.get_frame_rect();
        let step = 0;
        if (this._settings.get_boolean('cycle-sizes') && CYCLING.has(layout) && this._lastTile &&
            this._lastTile.id === wid && this._lastTile.layout === layout && sameRect(cur, this._lastTile.rect))
            step = (this._lastTile.step + 1) % CYCLE.length;

        const rect = this._rectFor(layout, wa, cur, step);
        win.move_resize_frame(true, rect.x, rect.y, rect.width, rect.height);
        this._lastTile = {id: wid, layout, step, rect};
        return '';
    }

    _remember(win) {
        const wid = win.get_id();
        if (!this._saved.has(wid) && !isMaximized(win)) {
            const r = win.get_frame_rect();
            this._saved.set(wid, {x: r.x, y: r.y, width: r.width, height: r.height});
            win.connectObject('unmanaged', () => this._saved?.delete(wid), this);
        }
    }

    _rectFor(layout, wa, cur, step) {
        const gap = this._settings.get_int('gap');
        const full = {x: wa.x + gap, y: wa.y + gap, width: wa.width - 2 * gap, height: wa.height - 2 * gap};
        const round = r => ({x: Math.round(r.x), y: Math.round(r.y),
            width: Math.round(r.width), height: Math.round(r.height)});

        if (layout === 'maximize')
            return full;
        if (layout === 'almost-maximize') {
            const w = full.width * 0.92, h = full.height * 0.92;
            return round({x: full.x + (full.width - w) / 2, y: full.y + (full.height - h) / 2, width: w, height: h});
        }
        if (layout === 'center' || layout === 'larger' || layout === 'smaller') {
            let w = Math.min(cur.width, full.width), h = Math.min(cur.height, full.height);
            if (layout !== 'center') {
                const k = layout === 'larger' ? 1.1 : 1 / 1.1;
                w = Math.max(320, Math.min(full.width, w * k));
                h = Math.max(240, Math.min(full.height, h * k));
            }
            const cx = layout === 'center' ? full.x + full.width / 2 : cur.x + cur.width / 2;
            const cy = layout === 'center' ? full.y + full.height / 2 : cur.y + cur.height / 2;
            const x = Math.min(Math.max(cx - w / 2, full.x), full.x + full.width - w);
            const y = Math.min(Math.max(cy - h / 2, full.y), full.y + full.height - h);
            return round({x, y, width: w, height: h});
        }

        let [fx, fy, fw, fh] = CELLS[layout];
        if (CYCLING.has(layout) && step) {
            const size = CYCLE[step];
            if (layout === 'left-half' || layout === 'right-half') {
                fw = size;
                fx = layout === 'left-half' ? 0 : 1 - size;
            } else {
                fh = size;
                fy = layout === 'top-half' ? 0 : 1 - size;
            }
        }
        const half = gap / 2;
        const x = full.x + fx * full.width + (fx > 0 ? half : 0);
        const y = full.y + fy * full.height + (fy > 0 ? half : 0);
        const right = full.x + (fx + fw) * full.width - (fx + fw < 0.999 ? half : 0);
        const bottom = full.y + (fy + fh) * full.height - (fy + fh < 0.999 ? half : 0);
        return round({x, y, width: right - x, height: bottom - y});
    }
}
