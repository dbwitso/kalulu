// Kalulu Clipboard — GNOME Shell companion for Kalulu's clipboard history.
//
// On Wayland, Mutter only tells the focused window about clipboard changes, and it doesn't offer the
// data-control protocol that clipboard managers use elsewhere. The Shell itself sees every copy, so
// this extension watches the selection and hands the text to Kalulu over D-Bus (the app's
// "clip-add" action). It keeps nothing itself; if Kalulu isn't running the copy is simply skipped.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'dev.kalulu.Launcher';
const OBJECT_PATH = '/dev/kalulu/Launcher';
const MAX_CHARS = 200000;
// Password managers (KeePassXC, Bitwarden desktop…) mark secrets with this; never forward them.
const SENSITIVE_MIME = 'x-kde-passwordManagerHint';

export default class KaluluClipboardExtension extends Extension {
    enable() {
        this._clipboard = St.Clipboard.get_default();
        this._selection = global.display.get_selection();
        this._ownerId = this._selection.connect('owner-changed', (_sel, type, source) => {
            if (type !== Meta.SelectionType.SELECTION_CLIPBOARD || !source)
                return;
            const mimes = source.get_mimetypes();
            if (mimes.includes(SENSITIVE_MIME))
                return;
            if (!mimes.some(m => m.startsWith('text/') || m === 'UTF8_STRING' || m === 'STRING'))
                return;
            this._clipboard.get_text(St.ClipboardType.CLIPBOARD, (_c, text) => {
                if (text && text.trim() && text.length <= MAX_CHARS)
                    this._send(text);
            });
        });
    }

    _send(text) {
        Gio.DBus.session.call(
            BUS_NAME, OBJECT_PATH, 'org.gtk.Actions', 'Activate',
            new GLib.Variant('(sava{sv})', ['clip-add', [new GLib.Variant('s', text)], {}]),
            null, Gio.DBusCallFlags.NO_AUTO_START, 2000, null,
            (conn, res) => {
                try {
                    conn.call_finish(res);
                } catch (_e) {
                    // Kalulu not running: nothing to do.
                }
            });
    }

    disable() {
        if (this._selection && this._ownerId)
            this._selection.disconnect(this._ownerId);
        this._ownerId = 0;
        this._selection = null;
        this._clipboard = null;
    }
}
