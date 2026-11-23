// Kalulu Lock Screen — an iOS 26-style lock screen for GNOME Shell 46–50.
//
// What it draws on top of GNOME's own unlock dialog (primary monitor, clock page only):
//   * the wallpaper, sharp (GNOME blurs it), with a soft scrim for legibility
//   * the date, and a big glass clock that stretches down to sit *behind* the wallpaper's subject
//   * the subject cut-out on top of the clock (the depth effect)
//   * frosted-glass widgets: weather, battery, next calendar event
//   * GNOME's lock-screen notifications and media, restyled as glass cards at the bottom
//
// Kalulu (the launcher) does all slow work while you're logged in and leaves the results in
// ~/.local/state/kalulu/lockscreen/meta.json; this file only reads it.
//
// Safety: unlocking must never depend on this code. Nothing here is reactive, nothing replaces GNOME
// methods, the password prompt is untouched, and any error tears the decoration down and leaves the
// stock lock screen in place.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GnomeDesktop from 'gi://GnomeDesktop';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Calendar from 'resource:///org/gnome/shell/ui/calendar.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const META_PATH = GLib.build_filenamev([GLib.get_user_state_dir(), 'kalulu', 'lockscreen', 'meta.json']);
const WEATHER_MAX_AGE = 3 * 3600;
const META_POLL_SECONDS = 300;

function readMeta() {
    try {
        const [ok, bytes] = GLib.file_get_contents(META_PATH);
        if (!ok)
            return null;
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_e) {
        return null;
    }
}

function fileExists(path) {
    return !!path && GLib.file_test(path, GLib.FileTest.EXISTS);
}

function cssUrl(path) {
    return `url("${Gio.File.new_for_path(path).get_uri().replace(/"/g, '%22')}")`;
}

function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
    if (!m)
        return [255, 255, 255];
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// St applies styles lazily; measure only after forcing the new style (and the children's)
function natSize(actor) {
    const walk = a => {
        a.ensure_style?.();
        for (const c of a.get_children?.() ?? [])
            walk(c);
    };
    walk(actor);
    const [, w] = actor.get_preferred_width(-1);
    const [, h] = actor.get_preferred_height(w);
    return [w, h];
}

function safeFont(name) {
    return String(name || 'Ubuntu Sans').replace(/[^\w \-.]/g, '') || 'Ubuntu Sans';
}

function formatTime(date) {
    // Follow the user's 12/24 h choice, without AM/PM (like iOS)
    const fmt = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'}).get_string('clock-format');
    const dt = GLib.DateTime.new_from_unix_local(Math.floor(date.getTime() / 1000));
    return dt.format(fmt === '12h' ? '%l:%M' : '%H:%M').trim();
}

class Glass {
    // A frosted pill: real background blur + translucent fill (see stylesheet.css)
    static make(styleClass) {
        const box = new St.BoxLayout({style_class: `kalulu-glass ${styleClass}`, reactive: false});
        try {
            box.add_effect(new Shell.BlurEffect({mode: Shell.BlurMode.BACKGROUND, radius: 36, brightness: 1.0}));
        } catch (_e) {
            // older Shell without background blur: the translucent fill still reads as glass
        }
        return box;
    }
}

class LockDecoration {
    constructor(dialog) {
        this._dialog = dialog;
        this._signals = [];
        this._sources = [];
        this._hidden = [];
        this._meta = readMeta();
        const prefs = this._meta?.prefs ?? {};
        if (!this._meta || prefs.enabled === false)
            return;
        try {
            this._build(prefs);
        } catch (e) {
            logError(e, 'Kalulu lock screen: falling back to the stock lock screen');
            this.destroy();
        }
    }

    // ------------------------------------------------------------------ build
    _build(prefs) {
        const d = this._dialog;
        this._prefs = prefs;
        this._widgetsWanted = Array.isArray(prefs.widgets) ? prefs.widgets : ['weather', 'battery', 'calendar'];

        this._overlay = new St.Widget({
            style_class: 'kalulu-lock',
            reactive: false,
            layout_manager: new Clutter.FixedLayout(),
        });
        this._overlay.connect('destroy', () => this._cleanup());
        // Lay out again once on screen, when fonts and theme are certainly resolved
        this._overlay.connect('notify::mapped', () => {
            if (this._overlay?.mapped)
                this._relayout();
        });
        // Above GNOME's (blurred) background, below the clock/prompt stack and notifications
        d.insert_child_above(this._overlay, d._backgroundGroup);

        const meta = this._meta;
        const hasWall = fileExists(meta.wallpaper);
        const hasSubject = hasWall && fileExists(meta.subject);

        if (hasWall) {
            this._wall = new St.Widget({style_class: 'kalulu-lock-layer'});
            this._wall.style = `background-image: ${cssUrl(meta.wallpaper)};`;
            this._overlay.add_child(this._wall);
        }
        this._topScrim = new St.Widget({style_class: 'kalulu-lock-scrim-top'});
        this._bottomScrim = new St.Widget({style_class: 'kalulu-lock-scrim-bottom'});
        this._overlay.add_child(this._topScrim);
        this._overlay.add_child(this._bottomScrim);

        this._date = new St.Label({style_class: 'kalulu-lock-date'});
        this._clock = new St.Label({style_class: 'kalulu-lock-clock'});
        this._clock.clutter_text.set_line_wrap(false);
        this._overlay.add_child(this._date);
        this._overlay.add_child(this._clock);

        if (hasSubject) {
            this._subject = new St.Widget({style_class: 'kalulu-lock-layer'});
            this._subject.style = `background-image: ${cssUrl(meta.subject)};`;
            this._overlay.add_child(this._subject);
        }

        this._widgets = new St.BoxLayout({style_class: 'kalulu-lock-widgets', reactive: false});
        this._overlay.add_child(this._widgets);

        this._hint = new St.Label({style_class: 'kalulu-lock-hint', text: _hintText()});
        this._overlay.add_child(this._hint);

        // Hide GNOME's own time/date/hint (we draw our own); restored if we go away.
        for (const a of [d._clock?._time, d._clock?._date, d._clock?._hint]) {
            if (a && a.visible) {
                a.hide();
                this._hidden.push(a);
            }
        }
        d.add_style_class_name('kalulu-lock-active');

        // Follow GNOME's clock ⇄ password crossfade
        this._connect(d._adjustment, 'notify::value', () => this._syncProgress());
        this._connect(Main.layoutManager, 'monitors-changed', () => this._relayout());
        const themeContext = St.ThemeContext.get_for_stage(global.stage);
        this._connect(themeContext, 'notify::scale-factor', () => this._relayout());

        this._wallClock = new GnomeDesktop.WallClock({time_only: true});
        this._connect(this._wallClock, 'notify::clock', () => this._tick());

        this._buildWidgets();
        this._built = true;
        this._tick();
        this._relayout();
        this._syncProgress();

        this._sources.push(GLib.timeout_add_seconds(GLib.PRIORITY_LOW, META_POLL_SECONDS, () => {
            const m = readMeta();
            if (m) {
                this._meta = m;
                this._updateWeather();
            }
            return GLib.SOURCE_CONTINUE;
        }));
    }

    _connect(obj, signal, fn) {
        if (!obj)
            return;
        const id = obj.connect(signal, (...args) => {
            try {
                fn(...args);
            } catch (e) {
                logError(e, 'Kalulu lock screen');
            }
        });
        this._signals.push([obj, id]);
    }

    // ------------------------------------------------------------------ clock + layout
    _tick() {
        const now = new Date();
        this._clock.text = formatTime(now);
        this._date.text = GLib.DateTime.new_now_local().format('%A %-d %B');
        this._updateCalendar();
        this._relayout();
    }

    _relayout() {
        if (!this._overlay || !this._built)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const {x, y, width: W, height: H} = monitor;
        const sf = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._overlay.set_position(x, y);
        this._overlay.set_size(W, H);

        for (const layer of [this._wall, this._subject]) {
            if (layer) {
                layer.set_position(0, 0);
                layer.set_size(W, H);
            }
        }
        this._topScrim.set_position(0, 0);
        this._topScrim.set_size(W, Math.round(H * 0.5));
        this._bottomScrim.set_size(W, Math.round(H * 0.4));
        this._bottomScrim.set_position(0, H - this._bottomScrim.height);

        // Colours: a light tint of the scene behind the clock, or dark text on very bright skies
        const [r, g, b] = hexToRgb(this._meta.tint);
        const dark = !!this._meta.dark_text;
        const font = safeFont(this._prefs.font);
        const weight = Math.max(100, Math.min(900, parseInt(this._prefs.weight) || 700));
        const glass = `rgba(${r}, ${g}, ${b}, ${dark ? 0.78 : 0.86})`;
        const shadow = dark ? 'rgba(255, 255, 255, 0.35)' : 'rgba(0, 0, 0, 0.28)';
        this._overlay.style = `color: ${glass};`;

        // Date
        const datePx = Math.round(H * 0.026);
        this._date.style = `font-family: "${font}"; font-weight: 600; font-size: ${datePx / sf}px; ` +
            `color: ${glass}; text-shadow: 0px 1px 8px ${shadow};`;
        const [dateW, dateH] = natSize(this._date);
        const dateY = Math.round(H * 0.07);
        this._date.set_position(Math.round((W - dateW) / 2), dateY);

        // Clock: as big as the width allows, then (iOS 26's adaptive clock) stretched tall so the
        // wallpaper's subject overlaps the lower part of the digits, but never more than a readable
        // share of them. If the subject would hide too much even at a smaller size, the clock simply
        // goes in front of it.
        const styleFor = p => `font-family: "${font}"; font-weight: ${weight}; font-size: ${p / sf}px; ` +
            `font-feature-settings: "tnum"; color: ${glass}; text-shadow: 0px 4px 24px ${shadow};`;
        let px = Math.round(H * 0.2);
        this._clock.style = styleFor(px);
        let [clockW] = natSize(this._clock);
        if (clockW > W * 0.82) {
            px = Math.floor(px * (W * 0.82) / clockW);
            this._clock.style = styleFor(px);
            [clockW] = natSize(this._clock);
        }
        const digitTop = dateY + dateH + H * 0.004;
        const pxBefore = px;
        const outline = this._subject ? this._outlinePx(W, H) : null;
        let stretch = 1.06;
        let depth = false;
        if (outline) {
            const fit = this._fitClock(outline, W, px, clockW, digitTop, H);
            if (fit) {
                ({px, stretch} = fit);
                depth = true;
                if (fit.px !== pxBefore) {
                    this._clock.style = styleFor(px);
                    [clockW] = natSize(this._clock);
                }
            }
        }
        if (this._subject)
            this._subject.visible = depth;
        const [, clockH] = natSize(this._clock);
        // Digit tops sit ~0.17 em below the label's top; digits are ~0.72 em tall.
        const ascentPad = px * 0.17;
        const digitH = px * 0.72;
        this._clock.set_pivot_point(0.5, ascentPad / clockH);
        this._clock.scale_y = stretch;
        this._clock.set_position(Math.round((W - clockW) / 2), Math.round(digitTop - ascentPad));
        const clockBottom = digitTop + digitH * stretch;

        // Widgets under the clock
        this._widgets.style = `font-family: "${font}";`;
        const [wW] = natSize(this._widgets);
        const widgetsY = Math.round(clockBottom + H * 0.035);
        this._widgets.set_position(Math.round((W - wW) / 2), widgetsY);

        // Hint at the very bottom
        const [hW, hH] = natSize(this._hint);
        this._hint.set_position(Math.round((W - hW) / 2), Math.round(H - hH - H * 0.03));
    }

    // The subject's top edge in screen pixels as [x0, x1, top] strips (wallpaper drawn "cover", centred)
    _outlinePx(W, H) {
        const m = this._meta;
        if (!m.width || !m.height)
            return null;
        const s = Math.max(W / m.width, H / m.height);
        const ox = (W - m.width * s) / 2;
        const oy = (H - m.height * s) / 2;
        const prof = Array.isArray(m.profile) && m.profile.length ? m.profile
            : typeof m.subject_top === 'number' ? [m.subject_top] : null;
        if (!prof)
            return null;
        const n = prof.length;
        return prof.map((t, i) => [ox + (i / n) * m.width * s, ox + ((i + 1) / n) * m.width * s,
            t >= 1 ? Infinity : oy + t * m.height * s]);
    }

    // [mean, worst] share of the clock's box hidden by the subject (worst = the most-covered strip)
    _occlusion(outline, x0, x1, top, bottom) {
        let hidden = 0;
        let worst = 0;
        const h = Math.max(1, bottom - top);
        for (const [a, b, t] of outline) {
            const w = Math.min(b, x1) - Math.max(a, x0);
            if (w <= 0 || t >= bottom)
                continue;
            const covered = bottom - Math.max(t, top);
            hidden += w * covered;
            worst = Math.max(worst, covered / h);
        }
        return [hidden / Math.max(1, (x1 - x0) * h), worst];
    }

    // Biggest clock (stretch first, then size) where the subject overlaps the digits but no digit
    // loses more than ~40% of its height, so the time still reads at a glance.
    _fitClock(outline, W, px0, clockW0, digitTop, H) {
        const tryFit = (px, s) => {
            const cw = clockW0 * px / px0;
            const x0 = (W - cw) / 2 + cw * 0.06;          // glyphs don't reach the label's very edges
            const x1 = (W + cw) / 2 - cw * 0.06;
            const [mean, worst] = this._occlusion(outline, x0, x1, digitTop, digitTop + px * 0.72 * s);
            return mean > 0.03 && mean <= 0.24 && worst <= 0.42;
        };
        for (let s = 1.6; s >= 0.999; s -= 0.05) {
            if (tryFit(px0, s))
                return {px: px0, stretch: s};
        }
        for (let px = px0; px >= H * 0.13; px -= Math.max(2, Math.round(H * 0.005))) {
            for (let s = 1.3; s >= 0.999; s -= 0.05) {
                if (tryFit(px, s))
                    return {px: Math.round(px), stretch: s};
            }
        }
        return null;
    }

    _syncProgress() {
        const v = this._dialog._adjustment?.value ?? 0;
        this._overlay.opacity = Math.round(255 * (1 - v));
        this._overlay.visible = v < 1;
    }

    // ------------------------------------------------------------------ widgets
    _buildWidgets() {
        this._widgetBoxes = {};
        for (const kind of this._widgetsWanted) {
            const pill = Glass.make(`kalulu-widget kalulu-widget-${kind}`);
            const icon = new St.Icon({style_class: 'kalulu-widget-icon', icon_size: 22});
            const text = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
            if ('orientation' in text)
                text.orientation = Clutter.Orientation.VERTICAL;   // GNOME 48+
            else
                text.vertical = true;                              // GNOME 46–47
            const title = new St.Label({style_class: 'kalulu-widget-title'});
            const sub = new St.Label({style_class: 'kalulu-widget-sub'});
            text.add_child(title);
            text.add_child(sub);
            pill.add_child(icon);
            pill.add_child(text);
            pill.hide();
            this._widgets.add_child(pill);
            this._widgetBoxes[kind] = {pill, icon, title, sub};
        }
        if (this._widgetBoxes.weather)
            this._updateWeather();
        if (this._widgetBoxes.battery)
            this._initBattery();
        if (this._widgetBoxes.calendar)
            this._initCalendar();
    }

    _show(kind, iconName, title, sub) {
        const w = this._widgetBoxes?.[kind];
        if (!w)
            return;
        if (!title) {
            w.pill.hide();
        } else {
            w.icon.icon_name = iconName;
            w.title.text = title;
            w.sub.text = sub || '';
            w.sub.visible = !!sub;
            w.pill.show();
        }
        this._relayout();
    }

    _updateWeather() {
        const w = this._meta?.weather;
        const fresh = w && (Date.now() / 1000 - (w.time || 0)) < WEATHER_MAX_AGE;
        if (!fresh) {
            this._show('weather', null, null);
            return;
        }
        this._show('weather', w.icon || 'weather-overcast-symbolic', `${w.temp}°  ${w.label}`,
            `${w.city ? `${w.city}  ·  ` : ''}H ${w.high}°  L ${w.low}°`);
    }

    _initBattery() {
        Gio.DBusProxy.new_for_bus(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, null,
            'org.freedesktop.UPower', '/org/freedesktop/UPower/devices/DisplayDevice',
            'org.freedesktop.UPower.Device', null, (_src, res) => {
                try {
                    this._upower = Gio.DBusProxy.new_for_bus_finish(res);
                } catch (_e) {
                    return;
                }
                if (!this._overlay)
                    return;
                this._connect(this._upower, 'g-properties-changed', () => this._updateBattery());
                this._updateBattery();
            });
    }

    _updateBattery() {
        const get = n => this._upower?.get_cached_property(n)?.unpack();
        if (!get('IsPresent')) {
            this._show('battery', null, null);     // desktops: no battery widget
            return;
        }
        const pct = Math.round(get('Percentage') ?? 0);
        const state = get('State');               // 1 charging, 2 discharging, 4 full, 5 pending charge
        const secs = state === 1 ? get('TimeToFull') : get('TimeToEmpty');
        const dur = secs > 0 ? `${Math.floor(secs / 3600)} h ${Math.round((secs % 3600) / 60)} min` : '';
        const sub = state === 1 ? `Charging${dur ? `  ·  full in ${dur}` : ''}`
            : state === 4 ? 'Fully charged'
                : dur ? `${dur} left` : 'On battery';
        this._show('battery', get('IconName') || 'battery-symbolic', `${pct}%`, sub);
    }

    _initCalendar() {
        try {
            this._events = new Calendar.DBusEventSource();
        } catch (_e) {
            this._events = null;
            return;
        }
        this._connect(this._events, 'changed', () => this._updateCalendar());
        this._updateCalendar(true);
    }

    _updateCalendar(request = false) {
        if (!this._events)
            return;
        const now = new Date();
        const end = new Date(now.getTime() + 24 * 3600 * 1000);
        if (request || !this._rangeEnd || this._rangeEnd < end) {
            this._rangeEnd = new Date(end.getTime() + 6 * 3600 * 1000);
            this._events.requestRange(now, this._rangeEnd);
        }
        const upcoming = this._events.getEvents(now, end)
            .filter(e => e.end > now)
            .sort((a, b) => a.date - b.date);
        if (!upcoming.length) {
            this._show('calendar', null, null);
            return;
        }
        const e = upcoming[0];
        const allDay = e.end - e.date >= 24 * 3600 * 1000 - 1000;
        const when = e.date <= now ? 'Now'
            : allDay ? 'Today'
                : formatTime(e.date) + (e.date.getDate() !== now.getDate() ? '  tomorrow' : '');
        const title = this._prefs.event_titles === false ? 'Event' : (e.summary || 'Event');
        this._show('calendar', 'x-office-calendar-symbolic', when, title);
    }

    // ------------------------------------------------------------------ teardown
    _cleanup() {
        for (const [obj, id] of this._signals) {
            try {
                obj.disconnect(id);
            } catch (_e) {}
        }
        this._signals = [];
        for (const s of this._sources)
            GLib.source_remove(s);
        this._sources = [];
        this._wallClock?.run_dispose();
        this._wallClock = null;
        try {
            this._events?.destroy();
        } catch (_e) {}
        this._events = null;
        this._upower = null;
        this._overlay = null;
    }

    destroy() {
        for (const a of this._hidden)
            a.show();
        this._hidden = [];
        this._dialog?.remove_style_class_name?.('kalulu-lock-active');
        const o = this._overlay;
        this._cleanup();
        o?.destroy();
    }
}

function _hintText() {
    try {
        const backend = global.stage.get_context?.()?.get_backend?.() ?? Clutter.get_default_backend();
        if (backend.get_default_seat().touch_mode)
            return 'Swipe up to unlock';
    } catch (_e) {}
    return 'Press any key to unlock';
}

export default class KaluluLockScreenExtension extends Extension {
    enable() {
        this._decorations = new Map();
        this._pending = new Set();
        const shield = Main.screenShield;
        const group = shield?._lockDialogGroup;
        if (!group)
            return;
        this._group = group;
        this._addedId = group.connect('child-added', (_g, actor) => {
            // The dialog adds itself to the group before building its clock: decorate once it's done.
            const id = GLib.idle_add(GLib.PRIORITY_HIGH_IDLE, () => {
                this._pending.delete(id);
                this._decorate(actor);
                return GLib.SOURCE_REMOVE;
            });
            this._pending.add(id);
        });
        if (shield._dialog)
            this._decorate(shield._dialog);
    }

    _decorate(dialog) {
        if (!this._decorations || this._decorations.has(dialog))
            return;
        // Only GNOME's UnlockDialog has these; anything else (GDM's dialog, a custom one) is left alone.
        if (!dialog?._clock || !dialog._adjustment || !dialog._backgroundGroup)
            return;
        const deco = new LockDecoration(dialog);
        this._decorations.set(dialog, deco);
        dialog.connect('destroy', () => this._decorations?.delete(dialog));
    }

    disable() {
        if (this._group && this._addedId)
            this._group.disconnect(this._addedId);
        for (const id of this._pending ?? [])
            GLib.source_remove(id);
        this._pending?.clear();
        for (const deco of this._decorations?.values() ?? [])
            deco.destroy();
        this._decorations = null;
        this._group = null;
        this._addedId = 0;
    }
}
