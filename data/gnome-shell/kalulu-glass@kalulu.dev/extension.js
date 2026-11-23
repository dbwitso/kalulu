// Kalulu Glass — frosted "Liquid Glass" across the GNOME interface (GNOME Shell 46–50).
//
// Real background blur (Shell.BlurEffect) plus a translucent tint on:
//   top bar · Ubuntu Dock / Dash to Dock · every popup menu (quick settings, calendar, app menus…)
//   notification banners · volume/brightness pop-ups · Alt+Tab · the overview (blurred wallpaper)
//   and the Kalulu launcher window (blur drawn behind it).
//
// Settings come from Kalulu's config ([glass] in ~/.config/kalulu/config.toml), which Kalulu mirrors to
// ~/.local/state/kalulu/glass.json; changes apply live. Everything is undone when the extension is
// turned off: original styles are restored and every effect and actor added here is removed.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as OsdWindow from 'resource:///org/gnome/shell/ui/osdWindow.js';
import * as SwitcherPopup from 'resource:///org/gnome/shell/ui/switcherPopup.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const STATE_PATH = GLib.build_filenamev([GLib.get_user_state_dir(), 'kalulu', 'glass.json']);
const KALULU_APP_ID = 'dev.kalulu.Launcher';
const EFFECT = 'kalulu-glass-blur';
// Kalulu's window corners are rounded (14 px). Blur effects are rectangular, so the blur behind the
// window is inset just enough that its corners stay inside the rounded shape: r·(1 − 1/√2) ≈ 0.3 r.
const KALULU_INSET = 5;

const DEFAULTS = {
    enabled: true, panel: true, dock: true, menus: true, notifications: true, popups: true,
    overview: true, kalulu: true, blur: 36, opacity: 0.42, tint: 'auto',
};

function readState() {
    try {
        const [ok, bytes] = GLib.file_get_contents(STATE_PATH);
        if (ok)
            return {...DEFAULTS, ...JSON.parse(new TextDecoder().decode(bytes))};
    } catch (_e) {}
    return {...DEFAULTS};
}

function* descendants(actor) {
    for (const c of actor.get_children?.() ?? []) {
        yield c;
        yield* descendants(c);
    }
}

export default class KaluluGlassExtension extends Extension {
    enable() {
        this._glassed = new Map();          // actor -> {style, destroyId}
        this._handlers = [];
        this._local = [];
        this._kaluluBlurs = new Map();
        this._injections = new InjectionManager();
        this._iface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._on(this._iface, 'changed::color-scheme', () => this._reapply());

        this._monitor = Gio.File.new_for_path(STATE_PATH).monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._on(this._monitor, 'changed', (_m, _f, _o, type) => {
            if (type === Gio.FileMonitorEvent.CHANGES_DONE_HINT || type === Gio.FileMonitorEvent.CREATED)
                this._reapply();
        });

        this._installHooks();
        this._apply();
    }

    disable() {
        this._teardown();
        this._injections?.clear();
        this._injections = null;
        for (const [obj, id] of this._handlers ?? [])
            obj.disconnect(id);
        this._handlers = [];
        this._monitor?.cancel();
        this._monitor = null;
        this._iface = null;
        this._glassed = null;
    }

    _on(obj, signal, fn) {
        const id = obj.connect(signal, (...args) => {
            try {
                return fn(...args);
            } catch (e) {
                logError(e, 'Kalulu Glass');
            }
            return undefined;
        });
        this._handlers.push([obj, id]);
    }

    // ------------------------------------------------------------------ settings
    _dark() {
        if (this._s.tint === 'dark')
            return true;
        if (this._s.tint === 'light')
            return false;
        return this._iface.get_string('color-scheme') === 'prefer-dark';
    }

    // Dark glass under light text, light glass under dark text, so nothing becomes unreadable
    // whatever the Shell theme does in light or dark mode. The top bar and dock always get dark glass
    // (their icons are white in Ubuntu's theme).
    _isDarkGlass(actor, kind) {
        if (kind === 'bar' || this._s.tint === 'dark')
            return true;
        if (this._s.tint === 'light')
            return false;
        try {
            actor.ensure_style?.();
            const c = actor.get_theme_node().get_foreground_color();
            return (0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue) / 255 > 0.5;
        } catch (_e) {
            return this._dark();
        }
    }

    _tint(dark) {
        const a = Math.max(0.1, Math.min(0.9, Number(this._s.opacity) || DEFAULTS.opacity));
        return dark ? `rgba(24, 24, 28, ${a})` : `rgba(248, 248, 250, ${Math.min(0.9, a + 0.18)})`;
    }

    _reapply() {
        this._teardown();
        this._apply();
    }

    _apply() {
        this._s = readState();
        if (!this._s.enabled)
            return;
        try {
            if (this._s.panel)
                this._glass(Main.panel, 'bar');
            if (this._s.dock)
                this._watchDock();
            if (this._s.notifications && Main.messageTray?._bannerBin) {
                const bin = Main.messageTray._bannerBin;
                this._bannerId = bin.connect('child-added', (_b, child) => this._glass(child, 'card'));
                for (const c of bin.get_children())
                    this._glass(c, 'card');
            }
            if (this._s.overview)
                this._buildOverview();
            if (this._s.kalulu)
                this._watchKalulu();
        } catch (e) {
            logError(e, 'Kalulu Glass');
        }
    }

    _teardown() {
        if (this._bannerId) {
            Main.messageTray._bannerBin.disconnect(this._bannerId);
            this._bannerId = 0;
        }
        if (this._dockId) {
            Main.layoutManager.uiGroup.disconnect(this._dockId);
            this._dockId = 0;
        }
        for (const [obj, id] of this._local ?? [])
            obj.disconnect(id);
        this._local = [];
        for (const actor of [...(this._glassed?.keys() ?? [])])
            this._unglass(actor);
        this._destroyOverview();
        for (const b of this._kaluluBlurs?.values() ?? [])
            b.destroy();
        this._kaluluBlurs = new Map();
    }

    // ------------------------------------------------------------------ glass on an actor
    // Tint on the actor itself; blur either as an effect on the actor or, when GNOME renders the actor
    // (or an ancestor) off-screen — the top bar and all menus do — as a separate "backdrop" actor right
    // underneath it. An off-screen buffer has nothing behind it to blur, so a backdrop is the only way.
    _glass(actor, kind = 'card') {
        if (!actor || !this._glassed || this._glassed.has(actor))
            return;
        const style = actor.get_style?.() ?? null;
        const dark = this._isDarkGlass(actor, kind);
        const tint = this._tint(dark);
        actor.add_style_class_name(dark ? 'kalulu-glass-dark' : 'kalulu-glass-light');
        const rec = {style, backdrop: null, ids: [], styled: false};
        rec.destroyId = actor.connect('destroy', () => this._unglass(actor));
        this._glassed.set(actor, rec);

        const top = this._offscreenTop(actor);
        let radius = 0;
        try {
            radius = actor.get_theme_node().get_border_radius(St.Corner.TOPLEFT);
        } catch (_e) {}
        if (top && radius === 0) {
            // Square surfaces (the top bar): the backdrop carries the tint and the actor goes clear via a
            // style class. GNOME's overview rewrites the top bar's inline style, so we can't use that.
            actor.add_style_class_name('kalulu-glass-clear');
            this._addBackdrop(actor, top, rec, tint);
            return;
        }
        actor.set_style(`${style ?? ''} background-color: ${tint};`);
        rec.styled = true;
        if (top) {
            this._addBackdrop(actor, top, rec, null);
        } else {
            actor.remove_effect_by_name(EFFECT);
            actor.add_effect_with_name(EFFECT, this._blurEffect());
        }
    }

    _blurEffect(scale = 1) {
        return new Shell.BlurEffect({
            mode: Shell.BlurMode.BACKGROUND,
            radius: Math.max(4, Math.min(120, (Number(this._s.blur) || DEFAULTS.blur) * scale)),
            brightness: 1.0,
        });
    }

    // If `actor` or an ancestor is always rendered off-screen, return the ancestor that is a direct
    // child of uiGroup (where the backdrop goes, just below it); otherwise null.
    _offscreenTop(actor) {
        const ui = Main.layoutManager.uiGroup;
        let redirected = false;
        let a = actor;
        let topChild = null;
        while (a && a !== ui) {
            if (a.get_offscreen_redirect?.() === Clutter.OffscreenRedirect.ALWAYS)
                redirected = true;
            topChild = a;
            a = a.get_parent();
        }
        return redirected && a === ui ? topChild : null;
    }

    _addBackdrop(actor, top, rec, tint) {
        const ui = Main.layoutManager.uiGroup;
        const backdrop = new St.Widget({reactive: false, style_class: 'kalulu-glass-backdrop'});
        if (tint)
            backdrop.set_style(`background-color: ${tint};`);
        backdrop.add_effect_with_name(EFFECT, this._blurEffect());
        ui.insert_child_below(backdrop, top);
        rec.backdrop = backdrop;

        let inset = 0;
        const sync = () => {
            if (!rec.backdrop)
                return;
            const visible = actor.mapped && actor.visible;
            backdrop.visible = visible;
            if (!visible)
                return;
            // Corners: keep the rectangular blur inside the rounded shape (r·(1 − 1/√2) ≈ 0.3 r)
            try {
                const r = actor.get_theme_node().get_border_radius(St.Corner.TOPLEFT);
                inset = Math.ceil(r * 0.3);
            } catch (_e) {}
            const [x, y] = actor.get_transformed_position();
            const [w, h] = actor.get_transformed_size();
            backdrop.set_position(Math.round(x + inset), Math.round(y + inset));
            backdrop.set_size(Math.max(0, Math.round(w - 2 * inset)), Math.max(0, Math.round(h - 2 * inset)));
            backdrop.opacity = actor.get_paint_opacity();
        };
        // Follow the actor and every ancestor up to uiGroup (menus slide and fade in, banners slide down)
        for (let a = actor; a && a !== ui; a = a.get_parent()) {
            for (const sig of ['notify::allocation', 'notify::translation-x', 'notify::translation-y',
                'notify::scale-x', 'notify::scale-y', 'notify::opacity', 'notify::visible', 'notify::mapped'])
                rec.ids.push([a, a.connect(sig, sync)]);
        }
        sync();
    }

    _unglass(actor) {
        const rec = this._glassed?.get(actor);
        if (!rec)
            return;
        this._glassed.delete(actor);
        for (const [o, id] of rec.ids) {
            try {
                o.disconnect(id);
            } catch (_e) {}
        }
        rec.backdrop?.destroy();
        rec.backdrop = null;
        try {
            actor.disconnect(rec.destroyId);
            actor.remove_effect_by_name(EFFECT);
            if (rec.styled)
                actor.set_style(rec.style);
            actor.remove_style_class_name('kalulu-glass-clear');
            actor.remove_style_class_name('kalulu-glass-dark');
            actor.remove_style_class_name('kalulu-glass-light');
        } catch (_e) {}         // already destroyed
    }

    // ------------------------------------------------------------------ hooks for things created later
    _installHooks() {
        const ext = this;
        // Every popup menu: quick settings, calendar, app/window menus, the dock's menus…
        this._injections.overrideMethod(PopupMenu.PopupMenu.prototype, 'open', original => function (...args) {
            original.call(this, ...args);
            if (ext._s?.enabled && ext._s.menus)
                ext._glass(this.box, 'card');
        });
        // Volume / brightness / keyboard-layout pop-ups
        this._injections.overrideMethod(OsdWindow.OsdWindowManager.prototype, 'show', original => function (...args) {
            const r = original.call(this, ...args);
            if (ext._s?.enabled && ext._s.popups) {
                for (const w of this._osdWindows ?? []) {
                    for (const a of [w, ...descendants(w)]) {
                        if (a.has_style_class_name?.('osd-window'))
                            ext._glass(a, 'card');
                    }
                }
            }
            return r;
        });
        // Alt+Tab and friends
        this._injections.overrideMethod(SwitcherPopup.SwitcherPopup.prototype, 'show', original => function (...args) {
            const r = original.call(this, ...args);
            if (ext._s?.enabled && ext._s.popups && this._switcherList)
                ext._glass(this._switcherList, 'card');
            return r;
        });
    }

    // ------------------------------------------------------------------ dock
    _watchDock() {
        const scan = () => {
            for (const top of Main.layoutManager.uiGroup.get_children()) {
                if (top.name !== 'dashtodockContainer')
                    continue;
                for (const a of descendants(top)) {
                    if (a.has_style_class_name?.('dash-background'))
                        this._glass(a, 'bar');
                }
            }
            return GLib.SOURCE_REMOVE;
        };
        scan();
        // Ubuntu Dock may load after us, and rebuilds itself on monitor/settings changes
        this._dockId = Main.layoutManager.uiGroup.connect('child-added', (_g, child) => {
            if (child.name === 'dashtodockContainer')
                GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, scan);
        });
    }

    // ------------------------------------------------------------------ overview
    _buildOverview() {
        this._overviewBgs = [];
        this._overviewManagers = [];
        const group = Main.layoutManager.overviewGroup;
        for (let i = 0; i < Main.layoutManager.monitors.length; i++) {
            const m = Main.layoutManager.monitors[i];
            const widget = new St.Widget({
                x: m.x, y: m.y, width: m.width, height: m.height,
                reactive: false, style_class: 'kalulu-glass-overview-bg',
            });
            this._overviewManagers.push(new Background.BackgroundManager({
                container: widget, monitorIndex: i, controlPosition: false,
            }));
            widget.add_effect(new Shell.BlurEffect({
                mode: Shell.BlurMode.ACTOR,
                radius: Math.max(20, (Number(this._s.blur) || DEFAULTS.blur) * 2),
                brightness: this._dark() ? 0.6 : 0.8,
            }));
            group.insert_child_at_index(widget, 0);
            this._overviewBgs.push(widget);
        }
        const rebuild = () => {
            this._destroyOverview();
            this._buildOverview();
        };
        this._local.push([Main.layoutManager, Main.layoutManager.connect('monitors-changed', rebuild)]);
        const entry = Main.overview?._overview?._controls?._searchEntry;
        if (entry)
            this._glass(entry, 'card');
    }

    _destroyOverview() {
        for (const m of this._overviewManagers ?? [])
            m.destroy();
        for (const w of this._overviewBgs ?? [])
            w.destroy();
        this._overviewManagers = [];
        this._overviewBgs = [];
    }

    // ------------------------------------------------------------------ Kalulu's window
    _isKalulu(win) {
        return win?.get_gtk_application_id?.() === KALULU_APP_ID ||
            (win?.get_wm_class?.() ?? '').toLowerCase() === KALULU_APP_ID.toLowerCase();
    }

    _watchKalulu() {
        this._local.push([global.display, global.display.connect('window-created', (_d, win) => {
            // the app id arrives right after creation on Wayland
            GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._maybeBlurWindow(win);
                return GLib.SOURCE_REMOVE;
            });
        })]);
        for (const actor of global.get_window_actors())
            this._maybeBlurWindow(actor.meta_window);
    }

    _maybeBlurWindow(win) {
        if (!win || !this._isKalulu(win) || this._kaluluBlurs.has(win))
            return;
        const winActor = win.get_compositor_private();
        if (!winActor)
            return;
        const blur = new St.Widget({reactive: false, style_class: 'kalulu-glass-window-blur'});
        blur.add_effect(this._blurEffect(1.2));
        global.window_group.insert_child_below(blur, winActor);
        const sync = () => {
            const r = win.get_frame_rect();
            blur.set_position(r.x + KALULU_INSET, r.y + KALULU_INSET);
            blur.set_size(Math.max(0, r.width - 2 * KALULU_INSET), Math.max(0, r.height - 2 * KALULU_INSET));
            blur.visible = winActor.visible && !win.minimized;
            blur.opacity = winActor.opacity;
        };
        const ids = [
            [win, win.connect('position-changed', sync)],
            [win, win.connect('size-changed', sync)],
            [winActor, winActor.connect('notify::visible', sync)],
            [winActor, winActor.connect('notify::opacity', sync)],
        ];
        const done = () => {
            for (const [o, id] of ids) {
                try {
                    o.disconnect(id);
                } catch (_e) {}
            }
            this._kaluluBlurs?.delete(win);
            blur.destroy();
        };
        ids.push([win, win.connect('unmanaged', done)]);
        blur.connect('destroy', () => {
            for (const [o, id] of ids) {
                try {
                    o.disconnect(id);
                } catch (_e) {}
            }
        });
        this._kaluluBlurs.set(win, blur);
        sync();
    }
}
