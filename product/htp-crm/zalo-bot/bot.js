// htp-crm-bot — Zalo group bot sidecar (zca-js, unofficial personal-account API).
//
// Bridges the family Zalo group <-> htp-crm over a tiny internal HTTP API,
// authenticated with a shared secret (X-Bot-Token). Every outbound call to
// the CRM is best-effort; every inbound call from Zalo is defensive — a dead
// or misbehaving Zalo session must never crash this process, and a dead CRM
// must never crash the listener.
//
// Session persists to /app/data/creds.json (cookie/imei/userAgent) so a
// container restart doesn't require rescanning the QR code. First run (or a
// dead/banned session) falls back to loginQR(), written to /app/data/qr.png
// and served at GET /qr for the CRM's /zalo admin page to display.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { Zalo, ThreadType, LoginQRCallbackEventType } from "zca-js";

const DATA_DIR = process.env.DATA_DIR || "/app/data";
const CREDS_PATH = path.join(DATA_DIR, "creds.json");
const QR_PATH = path.join(DATA_DIR, "qr.png");
const PORT = Number(process.env.PORT || 8100);
const CRM_URL = process.env.CRM_URL || "http://htp-crm-app:8000";
const BOT_TOKEN = process.env.BOT_TOKEN || "";
const GROUP_THREAD_ID = process.env.GROUP_THREAD_ID || "";
const DIGEST_HOUR = Number(process.env.DIGEST_HOUR || 7);
const RECONNECT_MS = 60_000;

fs.mkdirSync(DATA_DIR, { recursive: true });

const state = {
    loggedIn: false,
    awaitingQR: false,
    lastEventAt: null,
};

let api = null;
let lastDigestDate = null;
let reconnectTimer = null;

function log(...args) {
    console.log(new Date().toISOString(), ...args);
}

function localDateStr(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
}

function loadCredentials() {
    if (!fs.existsSync(CREDS_PATH)) return null;
    try {
        const raw = JSON.parse(fs.readFileSync(CREDS_PATH, "utf-8"));
        if (raw.cookie && raw.imei && raw.userAgent) return raw;
    } catch (e) {
        log("creds.json unreadable, ignoring:", e.message);
    }
    return null;
}

function saveCredentials(context) {
    const creds = {
        cookie: context.cookie.toJSON()?.cookies || [],
        imei: context.imei,
        userAgent: context.userAgent,
    };
    fs.writeFileSync(CREDS_PATH, JSON.stringify(creds, null, 2), "utf-8");
}

function clearCredentials() {
    try {
        fs.unlinkSync(CREDS_PATH);
    } catch {
        // nothing to clean up
    }
}

function scheduleReconnect() {
    state.loggedIn = false;
    api = null;
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
    }, RECONNECT_MS);
}

async function crmPost(pathName, body) {
    const res = await fetch(`${CRM_URL}${pathName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Bot-Token": BOT_TOKEN },
        body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`CRM ${pathName} -> ${res.status}`);
    return res.json();
}

async function crmGet(pathName) {
    const res = await fetch(`${CRM_URL}${pathName}`, {
        headers: { "X-Bot-Token": BOT_TOKEN },
    });
    if (!res.ok) throw new Error(`CRM ${pathName} -> ${res.status}`);
    return res.json();
}

async function onMessage(message) {
    if (message.type !== ThreadType.Group) return;
    if (message.isSelf) return;
    if (typeof message.data.content !== "string") return;

    state.lastEventAt = new Date().toISOString();

    if (!GROUP_THREAD_ID) {
        log("discovery: group message threadId=", message.threadId, "uid=", message.data.uidFrom);
        return;
    }
    if (message.threadId !== GROUP_THREAD_ID) return;

    try {
        const result = await crmPost("/bot/inbound", {
            uid: message.data.uidFrom,
            name: message.data.dName || "",
            text: message.data.content,
        });
        if (result && result.reply) {
            await api.sendMessage({ msg: result.reply }, GROUP_THREAD_ID, ThreadType.Group);
        }
    } catch (e) {
        log("inbound forward failed:", e.message);
    }
}

async function digestTick() {
    if (!state.loggedIn || !GROUP_THREAD_ID) return;
    const now = new Date();
    if (now.getHours() !== DIGEST_HOUR) return;
    const todayStr = localDateStr(now);
    if (lastDigestDate === todayStr) return;

    try {
        const { text } = await crmGet("/bot/digest");
        if (text) {
            await api.sendMessage({ msg: text }, GROUP_THREAD_ID, ThreadType.Group);
        }
        lastDigestDate = todayStr;
    } catch (e) {
        log("digest send failed:", e.message);
    }
}

// zca-js only writes qr.png when the caller explicitly calls
// event.actions.saveToFile() from the callback — passing a callback opts out
// of the library's own auto-save. QRCodeExpired/QRCodeDeclined similarly need
// an explicit actions.retry() or the underlying promise just hangs (declined)
// or rejects the whole connect() attempt (expired), losing 60s to a full
// reconnect instead of looping straight to a fresh code.
function qrCallback(event) {
    switch (event.type) {
        case LoginQRCallbackEventType.QRCodeGenerated:
            event.actions.saveToFile(QR_PATH).then(() => log("QR code generated at", QR_PATH));
            break;
        case LoginQRCallbackEventType.QRCodeExpired:
            log("QR expired, generating a new one");
            event.actions.retry();
            break;
        case LoginQRCallbackEventType.QRCodeScanned:
            log("QR scanned, waiting for confirm on phone");
            break;
        case LoginQRCallbackEventType.QRCodeDeclined:
            log("QR login declined, generating a new one");
            event.actions.retry();
            break;
    }
}

async function connect() {
    const zalo = new Zalo();
    const credentials = loadCredentials();

    try {
        if (credentials) {
            try {
                api = await zalo.login(credentials);
            } catch (e) {
                log("saved session rejected, falling back to QR:", e.message);
                clearCredentials();
                state.awaitingQR = true;
                api = await zalo.loginQR({ qrPath: QR_PATH, userAgent: "" }, qrCallback);
            }
        } else {
            state.awaitingQR = true;
            api = await zalo.loginQR({ qrPath: QR_PATH, userAgent: "" }, qrCallback);
        }
    } catch (e) {
        log("login failed:", e.message);
        state.awaitingQR = false;
        scheduleReconnect();
        return;
    }

    state.awaitingQR = false;
    state.loggedIn = true;
    saveCredentials(api.getContext());

    const { listener } = api;
    listener.on("message", onMessage);
    listener.onConnected(() => log("listener connected"));
    listener.onClosed(() => {
        log("listener closed — reconnecting in 60s");
        scheduleReconnect();
    });
    listener.onError((err) => {
        log("listener error:", err?.message || err);
    });
    listener.start();

    log("bot ready — loggedIn=true, groupConfigured=", Boolean(GROUP_THREAD_ID));
}

setInterval(digestTick, 60_000);

const server = http.createServer((req, res) => {
    const url = new URL(req.url, `http://localhost:${PORT}`);

    if (url.pathname === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(state));
        return;
    }

    if (url.pathname === "/qr" && req.method === "GET") {
        if (!state.awaitingQR || !fs.existsSync(QR_PATH)) {
            res.writeHead(404);
            res.end();
            return;
        }
        res.writeHead(200, { "Content-Type": "image/png" });
        fs.createReadStream(QR_PATH).pipe(res);
        return;
    }

    if (url.pathname === "/send" && req.method === "POST") {
        const token = req.headers["x-bot-token"] || "";
        if (!BOT_TOKEN || token !== BOT_TOKEN) {
            res.writeHead(403);
            res.end();
            return;
        }
        if (!state.loggedIn || !GROUP_THREAD_ID) {
            res.writeHead(503);
            res.end(JSON.stringify({ error: "bot not ready" }));
            return;
        }
        let body = "";
        req.on("data", (chunk) => (body += chunk));
        req.on("end", async () => {
            try {
                const { text } = JSON.parse(body || "{}");
                if (!text) {
                    res.writeHead(400);
                    res.end();
                    return;
                }
                await api.sendMessage({ msg: text }, GROUP_THREAD_ID, ThreadType.Group);
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ status: "ok" }));
            } catch (e) {
                res.writeHead(500);
                res.end(JSON.stringify({ error: e.message }));
            }
        });
        return;
    }

    res.writeHead(404);
    res.end();
});

server.listen(PORT, () => log(`bot http server on :${PORT}`));

connect();
