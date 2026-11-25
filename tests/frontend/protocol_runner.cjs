/* Test the real compiled protocol module, without loading Discord or Electron. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const p = require(process.argv[2]);
const voiceModule = require(path.join(path.dirname(process.argv[2]), "voice.js"));
const webcamModule = require(path.join(path.dirname(process.argv[2]), "webcam.js"));
const encode = bytes => Buffer.from(bytes).toString("base64");
const preferences = p.normalisePreferences({});
const user = {
    id: "2", name: "Zoë 🎮", avatar: null, me: true, speaking: true,
    mute: false, deaf: true, serverMuted: false, video: true, live: true,
};
const state = {
    t: "state", channel: { id: "1", name: "Call", guild: "Guild", icon: null },
    users: [user], config: p.normalisePreferences({
        position: "middle-right", scale: 1.8, opacity: 0.3, accent: "ABCDEF",
        style: "mac", showNames: "speaking", avatarSize: "small", onlySpeaking: true,
        showWebcams: true, webcamWidth: 480,
    }),
};
const idle = { t: "state", channel: null, users: [], config: preferences };
const speaking = { t: "speaking", id: "2", on: false };
const toast = { t: "toast", kind: "join", text: "Zoë 🎮 joined\nvoice", avatar: null };

function wire() {
    for (const message of [state, idle, speaking, toast, { ...toast, kind: "leave" }]) {
        const line = p.serialiseControl(message);
        assert.equal(typeof line, "string");
        assert.equal(line.includes("\n"), false);
        console.log(line);
    }
    console.log(p.serialiseFrame("2", Uint8Array.of(1, 2, 3), 960, 1920, encode));
}

function boundaries() {
    assert.deepEqual(p.normalisePreferences(null), preferences);
    assert.deepEqual(p.normalisePreferences({
        position: "centre", scale: Infinity, opacity: NaN, accent: "abcdef\n",
        style: "unknown", showNames: 1, avatarSize: false, onlySpeaking: "true",
        showWebcams: 0, webcamWidth: 480.5,
    }), preferences);
    assert.deepEqual(preferences, {
        position: "top-left", scale: 1, opacity: 0.85, accent: "#23a55a",
        style: "discord", showNames: "always", avatarSize: "large", onlySpeaking: false,
        showWebcams: true, webcamWidth: 280,
    });
    assert.equal(p.normalisePort("6889"), 6889);
    assert.equal(p.normalisePort("65535"), 65535);
    for (const value of [0, 65536, 2.5, true, NaN, "invalid", null]) {
        assert.equal(p.normalisePort(value), 6889);
        assert.equal(p.isPort(value), false);
    }
    for (const rate of [5, 10, 15, 20, 24, 30]) assert.equal(p.normaliseFrameRate(rate), rate);
    assert.equal(p.normaliseFrameRate(29), 15);
    assert.deepEqual(p.captureDimensions(1920, 1080, 280), { w: 560, h: 315 });
    assert.deepEqual(p.captureDimensions(1920, 1080, 480), { w: 960, h: 540 });
    assert.deepEqual(p.captureDimensions(100, 50, 480), { w: 100, h: 50 });
    assert.deepEqual(p.captureDimensions(1000, 4000, 480), { w: 480, h: 1920 });
    assert.deepEqual(p.captureDimensions(10000, 1, 480), { w: 960, h: 1 });
    assert.equal(p.captureDimensions(0, 100, 280), null);
    assert.equal(p.captureDimensions(1, Infinity, 280), null);
    for (const message of [null, [], {}, { t: "unknown" }, { ...state, users: [user, user] },
        { ...state, users: [{ ...user, mute: 1 }] }, { ...state, users: [{ ...user, avatar: 1 }] },
        { ...state, channel: { ...state.channel, id: 1 } },
        { ...state, config: { ...preferences, accent: "abcdef\n" } },
        { ...state, config: { ...preferences, scale: 2 } },
        { ...state, config: { ...preferences, webcamWidth: 280.5 } },
        { ...state, config: { ...preferences, onlySpeaking: 1 } },
        { ...speaking, on: 1 }, { ...speaking, id: 2 }, { ...toast, kind: "other" },
        { ...toast, extra: true }, { t: "frame", id: "2", w: 1, h: 1, data: "AQ==" }]) {
        assert.equal(p.serialiseControl(message), null);
    }
    // Include the exact UTF-8 boundary, not just JavaScript character counts.
    const overhead = Buffer.byteLength(p.serialiseControl({ ...toast, text: "" }));
    const count = p.MAX_MESSAGE_BYTES - overhead;
    const exact = { ...toast, text: "é".repeat(Math.floor(count / 2)) + "a".repeat(count % 2) };
    assert.equal(Buffer.byteLength(p.serialiseControl(exact)), p.MAX_MESSAGE_BYTES);
    assert.equal(p.serialiseControl({ ...exact, text: exact.text + "a" }), null);
    for (const [id, jpeg, w, h] of [
        [1, Uint8Array.of(1), 1, 1], ["2", [1], 1, 1], ["2", new Uint8Array(), 1, 1],
        ["2", new Uint8Array(p.MAX_FRAME_BYTES + 1), 1, 1], ["2", Uint8Array.of(1), 961, 1],
        ["2", Uint8Array.of(1), 1, 1921], ["2", Uint8Array.of(1), true, 1],
    ]) assert.equal(p.serialiseFrame(id, jpeg, w, h, encode), null);
    assert.notEqual(p.serialiseFrame("2", new Uint8Array(p.MAX_FRAME_BYTES), 960, 1920, encode), null);
    console.log(JSON.stringify([p.MAX_MESSAGE_BYTES, p.MAX_FRAME_BYTES, p.MAX_FRAME_WIDTH, p.MAX_FRAME_HEIGHT]));
}

function loadNative() {
    // Transpile the native entry point with the same compiler used for protocol.ts.
    // Full native type checking is provided separately by Vencord's own tsc gate.
    const ts = require(process.argv[4]);
    const source = fs.readFileSync(process.argv[5], "utf8");
    const result = ts.transpileModule(source, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
        reportDiagnostics: true,
    });
    assert.equal(result.diagnostics.length, 0);
    const nativePath = path.join(path.dirname(process.argv[2]), "native.cjs");
    fs.writeFileSync(nativePath, result.outputText);
    return require(nativePath);
}

async function native() {
    console.info = (...args) => console.error(...args);
    const helper = loadNative();
    let client;
    let received = "";
    const server = net.createServer(socket => {
        client = socket;
        socket.setEncoding("utf8");
        socket.on("data", chunk => { received += chunk; });
    });
    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
    const until = async predicate => {
        for (let i = 0; i < 200; i++) {
            if (predicate()) return;
            await delay(10);
        }
        assert.fail("Timed out waiting for native IPC test condition");
    };
    try {
        await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
        assert.equal(helper.send(null, state, null), false);
        assert.equal(helper.setPort(null, server.address().port), false);
        assert.equal(helper.start(null, 65536), false);
        await delay(30);
        assert.equal(client, undefined);
        assert.equal(helper.start(null, server.address().port), true);
        assert.equal(helper.setPort(null, 65536), false);
        await until(() => helper.connectionStatus() !== null);
        const epoch = helper.connectionStatus();
        assert.equal(helper.send(null, speaking, epoch), false);
        assert.equal(helper.sendFrame(null, "2", Uint8Array.of(1), 1, 1, epoch), false);
        assert.equal(helper.send(null, state, epoch), true);
        assert.equal(helper.send(null, { ...speaking, on: 1 }, epoch), false);
        assert.equal(helper.send(null, { ...state, users: [user, user] }, epoch), false);
        assert.equal(helper.sendFrame(null, "2", Uint8Array.of(1), 961, 1, epoch), false);
        assert.equal(helper.sendFrame(null, "2", Uint8Array.of(1, 2, 3), 960, 1920, epoch), true);
        assert.equal(helper.sendFrame(null, "2", new Uint8Array(p.MAX_FRAME_BYTES + 1), 1, 1, epoch), false);
        await until(() => received.split("\n").length >= 3);
        assert.equal(received, p.serialiseControl(state) + "\n" + p.serialiseFrame("2", Uint8Array.of(1, 2, 3), 960, 1920, encode) + "\n");
        process.stdout.write(received);
    } finally {
        helper.disconnect();
        client?.destroy();
        await new Promise(resolve => server.close(resolve));
    }
}

async function reconnect() {
    const helper = loadNative();
    const clients = new Set();
    let received = "";
    const server = net.createServer(socket => {
        clients.add(socket);
        socket.setEncoding("utf8");
        socket.on("data", chunk => { received += chunk; });
        socket.on("close", () => clients.delete(socket));
    });
    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
    const until = async predicate => {
        for (let i = 0; i < 600; i++) {
            if (predicate()) return;
            await delay(10);
        }
        assert.fail("Timed out waiting for reconnect test condition");
    };
    const listen = port => new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(port, "127.0.0.1", resolve);
    });
    const stop = async () => {
        for (const client of clients) client.destroy();
        if (server.listening) await new Promise(resolve => server.close(resolve));
    };
    try {
        await listen(0);
        const { port } = server.address();
        helper.start(null, port);
        await until(() => helper.connectionStatus() !== null);
        const first = helper.connectionStatus();
        assert.equal(helper.send(null, state, first), true);
        await until(() => received.endsWith("\n"));
        assert.equal(received, p.serialiseControl(state) + "\n");
        await stop();
        await until(() => helper.connectionStatus() === null);
        // Restart the overlay listener on the same port; no renderer send or
        // status query is needed to initiate a retry.
        received = "";
        await listen(port);
        await until(() => clients.size === 1);
        await until(() => helper.connectionStatus() !== null);
        const second = helper.connectionStatus();
        assert.notEqual(second, first);
        assert.equal(helper.send(null, state, first), false);
        assert.equal(helper.send(null, speaking, second), false);
        assert.equal(helper.send(null, idle, second), true);
        assert.equal(helper.send(null, speaking, second), true);
        await until(() => received.split("\n").length === 3);
        assert.equal(received, p.serialiseControl(idle) + "\n" + p.serialiseControl(speaking) + "\n");
        helper.disconnect();
        await until(() => clients.size === 0);
        assert.equal(helper.connectionStatus(), null);
    } finally {
        helper.disconnect();
        await stop();
    }
}

// Deterministic transport tests: control the socket and clock without relying
// on OS buffering or timing races. The real TCP path is tested separately.
function lifecycle() {
    const { EventEmitter } = require("node:events");
    const originalConnect = net.connect;
    const originalSetTimeout = global.setTimeout;
    const originalClearTimeout = global.clearTimeout;
    const sockets = [];
    const timers = new Set();
    class FakeSocket extends EventEmitter {
        writable = true;
        destroyed = false;
        writableLength = 0;
        lines = [];
        setNoDelay() {}
        setTimeout(ms) { this.timeout = ms; }
        destroy() { this.destroyed = true; this.writable = false; }
        write(line) { this.lines.push(line); return false; }
    }
    net.connect = options => {
        const socket = new FakeSocket();
        socket.port = options.port;
        assert.equal(options.host, "127.0.0.1");
        sockets.push(socket);
        return socket;
    };
    global.setTimeout = (fn, ms) => {
        assert.equal(ms, 2000);
        const timer = { fn };
        timers.add(timer);
        return timer;
    };
    global.clearTimeout = timer => timers.delete(timer);
    const fireRetry = () => {
        assert.equal(timers.size, 1);
        const [timer] = timers;
        timers.delete(timer);
        timer.fn();
    };
    let helper;
    try {
        helper = loadNative();
        assert.equal(helper.start(null, 12345), true);
        assert.equal(helper.start(null, 12345), true);
        assert.equal(sockets.length, 1);
        assert.equal(helper.connectionStatus(), null);
        assert.equal(helper.send(null, state, null), false);
        const [first] = sockets;
        assert.equal(first.timeout, 5000);
        assert.equal(helper.setPort(null, 12346), true);
        assert.equal(first.destroyed, true);
        const second = sockets[1];
        assert.equal(second.port, 12346);
        first.emit("connect");
        first.emit("error", new Error("obsolete"));
        first.emit("close");
        assert.equal(helper.connectionStatus(), null);
        assert.equal(timers.size, 0);
        second.emit("connect");
        const epoch = helper.connectionStatus();
        assert.equal(typeof epoch, "number");
        assert.equal(second.timeout, 0);
        assert.equal(helper.send(null, speaking, epoch), false);
        assert.equal(helper.canSendFrame(null, epoch), false);
        assert.equal(helper.send(null, state, epoch), true);
        assert.equal(helper.canSendFrame(null, epoch), true);
        assert.equal(helper.send(null, speaking, epoch), true);
        assert.equal(second.lines.length, 2);
        second.writableLength = 4 * 1024 * 1024 - 1;
        assert.equal(helper.sendFrame(null, "2", Uint8Array.of(1), 1, 1, epoch), false);
        assert.equal(second.destroyed, false);
        second.writableLength = 4 * 1024 * 1024;
        assert.equal(helper.canSendFrame(null, epoch), false);
        assert.equal(helper.send(null, speaking, epoch), true);
        second.writableLength = p.MAX_MESSAGE_BYTES;
        assert.equal(helper.send(null, speaking, epoch), false);
        assert.equal(second.destroyed, true);
        second.emit("close");
        fireRetry();
        const third = sockets[2];
        third.emit("connect");
        const nextEpoch = helper.connectionStatus();
        assert.notEqual(nextEpoch, epoch);
        assert.equal(helper.send(null, state, epoch), false);
        assert.equal(helper.sendFrame(null, "2", Uint8Array.of(1), 1, 1, epoch), false);
        assert.equal(helper.send(null, speaking, nextEpoch), false);
        assert.equal(helper.send(null, state, nextEpoch), true);
        third.emit("error", new Error("lost"));
        third.emit("close");
        assert.equal(timers.size, 1);
        helper.disconnect();
        assert.equal(timers.size, 0);
        assert.equal(helper.connectionStatus(), null);
        assert.equal(helper.setPort(null, 12347), false);
        assert.equal(helper.start(null, 12347), true);
        const fourth = sockets[3];
        fourth.emit("timeout");
        assert.equal(fourth.destroyed, true);
        fourth.emit("close");
        fireRetry();
        const fifth = sockets[4];
        helper.disconnect();
        assert.equal(fifth.destroyed, true);
        fifth.emit("connect");
        fifth.emit("close");
        assert.equal(helper.connectionStatus(), null);
        assert.equal(timers.size, 0);
        assert.equal(sockets.length, 5);
    } finally {
        helper?.disconnect();
        net.connect = originalConnect;
        global.setTimeout = originalSetTimeout;
        global.clearTimeout = originalClearTimeout;
    }
}

// Test renderer-side scheduling and epoch handling with Discord API stubs.
// The actual Vencord API compatibility is checked by the upstream tsc gate.
async function renderer(voiceTests = false, webcamTests = false, settingsTests = false) {
    const ts = require(process.argv[4]);
    const source = fs.readFileSync(process.argv[6], "utf8");
    const compiled = ts.transpileModule(source, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
        reportDiagnostics: true,
    });
    assert.equal(compiled.diagnostics.length, 0);
    const calls = [];
    const messages = [];
    const frameCalls = [];
    const timers = new Map();
    let timerId = 0;
    let epoch = null;
    let deferredStatus = null;
    let deferredSend = null;
    let deferredBlob = null;
    let canSend = true;
    let scans = 0;
    let encodes = 0;
    let definitions;
    let store;
    const settingsCalls = [];
    const debugWindow = {};
    const avatars = {
        self: "https://cdn.discordapp.com/embed/avatars/0.png",
        "2": "https://cdn.discordapp.com/avatars/123/abcdef.webp?size=240",
        "3": "https://media.discordapp.net/guilds/456/users/789/avatars/a_abcdef.gif?size=240&format=webp",
    };
    let channelId = "1";
    let members = { self: { userId: "self" }, "2": { userId: "2", selfVideo: true } };
    const native = {
        start: async port => { calls.push(["start", port]); return true; },
        setPort: async port => { calls.push(["port", port]); return true; },
        connectionStatus: async () => deferredStatus ?? epoch,
        send: async (message, generation) => {
            calls.push(["send", message.t, generation]);
            messages.push(message);
            return deferredSend ?? true;
        },
        canSendFrame: async () => canSend,
        sendFrame: async (id, _bytes, width, height, generation) => {
            frameCalls.push([id, width, height, generation]);
            return true;
        },
        disconnect: async () => { calls.push(["disconnect"]); },
    };
    const repositoryLink = () => {};
    const modules = {
        "@components/Link": { Link: repositoryLink },
        "@api/Settings": {
            migratePluginSettings(...args) { settingsCalls.push(["migrate", ...args]); },
            definePluginSettings(options) {
                settingsCalls.push(["define"]);
                definitions = options;
                store = Object.fromEntries(Object.entries(options).map(([key, option]) => [
                    key, option.default ?? option.options?.find(choice => choice.default)?.value,
                ]));
                return { store };
            },
        },
        "@utils/Logger": { Logger: class { error(error) { assert.fail(String(error)); } debug(...args) { assert.fail(String(args)); } } },
        // Match Vencord's ES module exports when transpiling to CommonJS.
        "@utils/types": {
            __esModule: true,
            default: plugin => plugin,
            makeRange: () => [],
            OptionType: {},
            ReporterTestable: {},
        },
        "@webpack/common": {
            React: { createElement: (type, props, ...children) => ({ type, props, children }) },
            SelectedChannelStore: { getVoiceChannelId: () => channelId },
            UserStore: {
                getCurrentUser: () => ({ id: "self" }),
                getUser: id => id === "missing" ? undefined : ({ username: id, globalName: id === "2" ? "Zoë" : null, getAvatarURL: () => avatars[id] ?? null }),
            },
            ChannelStore: { getChannel: id => ({ name: `Room ${id}`, guild_id: "guild" }) },
            GuildStore: { getGuild: () => ({ name: "Guild", getIconURL: () => null }) },
            GuildMemberStore: { getNick: (_guild, id) => id === "3" ? "Nickname" : null },
            VoiceStateStore: { getVoiceStatesForChannel: () => members },
        },
        "./protocol": p,
        "./voice": voiceModule,
        "./webcam": webcamModule,
    };
    const schedule = (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; };
    const clear = id => timers.delete(id);
    const exports = {};
    const video = cameraVideo("3");
    const blob = { size: 1, arrayBuffer: async () => new Uint8Array([1]).buffer };
    class Canvas {
        constructor(width, height) { this.width = width; this.height = height; }
        getContext() { return { drawImage() {} }; }
        async convertToBlob() { encodes++; return deferredBlob ?? blob; }
    }
    const settingsSource = fs.readFileSync(path.join(path.dirname(process.argv[6]), "settings.ts"), "utf8");
    const settingsCompiled = ts.transpileModule(settingsSource, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
        reportDiagnostics: true,
    });
    assert.equal(settingsCompiled.diagnostics.length, 0);
    const settingsModule = {};
    const load = name => {
        assert.ok(Object.hasOwn(modules, name), name);
        return modules[name];
    };
    new Function("require", "exports", settingsCompiled.outputText)(load, settingsModule);
    modules["./settings"] = settingsModule;
    const execute = new Function("require", "exports", "VencordNative", "window", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "document", "OffscreenCanvas", compiled.outputText);
    execute(load, exports, { pluginHelpers: { VencOverlay: native } }, debugWindow, schedule, clear, schedule, clear, { querySelectorAll: () => { scans++; return [video]; } }, Canvas);
    const plugin = exports.default;
    const tick = () => {
        const timer = [...timers.values()].find(timer => timer.ms === 250);
        assert.ok(timer);
        return timer.fn();
    };
    assert.deepEqual(settingsCalls, [["migrate", "VencOverlay", "OverlayBridge"], ["define"]]);
    plugin.start();
    assert.deepEqual(calls, [["start", 6889]]);
    await tick();
    assert.equal(calls.length, 1);
    epoch = 1;
    await tick();
    assert.deepEqual(calls.at(-1), ["send", "state", 1]);
    if (settingsTests) {
        assert.deepEqual(plugin.authors, [
            { name: "caminashell", id: 0n },
            { name: "akiloyutu", id: 0n },
        ]);
        assert.deepEqual(plugin.settingsAboutComponent(), {
            type: repositoryLink,
            props: { href: "https://github.com/caminashell/vencoverlay" },
            children: ["VencOverlay source and support"],
        });
        const defaults = {
            style: "discord", showNames: "always", avatarSize: "large", position: "top-left",
            scale: 1, opacity: 0.85, accentColor: "#23a55a", showOnlySpeaking: false,
            showSelf: true, showWebcams: true, showOwnWebcam: true, webcamFps: 15,
            webcamWidth: 280, toasts: true, port: 6889,
        };
        assert.deepEqual(store, defaults);
        const effectiveDefaults = {
            preferences, showSelf: true, showOwnWebcam: true, toasts: true, webcamFps: 15, port: 6889,
        };
        assert.deepEqual(settingsModule.readSettings(store), effectiveDefaults);
        assert.deepEqual(settingsModule.readSettings(null), effectiveDefaults);
        const saved = Object.freeze({
            style: "mac", showNames: "never", avatarSize: "small", position: "bottom-right",
            scale: 1.8, opacity: 0.3, accentColor: "ABCDEF", showOnlySpeaking: true,
            showSelf: false, showWebcams: false, showOwnWebcam: false, webcamFps: 24,
            webcamWidth: 480, toasts: false, port: "12345",
        });
        const effectiveSaved = settingsModule.readSettings(saved);
        assert.deepEqual(effectiveSaved, {
            preferences: {
                style: "mac", showNames: "never", avatarSize: "small", position: "bottom-right",
                scale: 1.8, opacity: 0.3, accent: "ABCDEF", onlySpeaking: true,
                showWebcams: false, webcamWidth: 480,
            },
            showSelf: false, showOwnWebcam: false, toasts: false, webcamFps: 24, port: 12345,
        });
        const invalid = Object.freeze({
            style: null, showNames: "other", avatarSize: 1, position: "centre",
            scale: Infinity, opacity: 2, accentColor: "bad", showOnlySpeaking: 1,
            showSelf: "false", showWebcams: 0, showOwnWebcam: null, webcamFps: 29,
            webcamWidth: 480.5, toasts: "false", port: "0",
        });
        assert.deepEqual(settingsModule.readSettings(invalid), effectiveDefaults);
        assert.equal(invalid.port, "0");
        for (const [key, choices] of Object.entries({
            style: ["discord", "mac"], showNames: ["always", "speaking", "never"],
            avatarSize: ["large", "small"], position: ["top-left", "top-right", "bottom-left", "bottom-right", "middle-left", "middle-right"],
        })) assert.deepEqual(definitions[key].options.map(choice => choice.value), choices);
        assert.deepEqual(definitions.webcamFps.markers, [5, 10, 15, 20, 24, 30]);
        assert.deepEqual(definitions.webcamWidth.markers, [160, 200, 240, 280, 320, 400, 480]);
        const update = (key, value) => {
            store[key] = value;
            definitions[key].onChange?.(value);
        };
        const before = messages.length;
        for (const key of ["style", "showNames", "avatarSize", "position", "scale", "opacity", "accentColor", "showOnlySpeaking"]) {
            update(key, saved[key]);
        }
        await tick();
        assert.equal(messages.length, before + 1);
        assert.deepEqual(messages.at(-1).config, { ...effectiveSaved.preferences, showWebcams: true, webcamWidth: 280 });
        assert.equal(calls.filter(call => call[0] === "start" || call[0] === "port").length, 1);
        // Camera controls publish a new state, while FPS is a local scheduling preference.
        update("showSelf", false);
        update("showOwnWebcam", false);
        update("showWebcams", false);
        update("webcamWidth", 480);
        await tick();
        assert.deepEqual(messages.at(-1).config, effectiveSaved.preferences);
        assert.deepEqual(messages.at(-1).users.map(user => user.id), ["2"]);
        update("webcamFps", 24);
        assert.ok([...timers.values()].some(timer => timer.ms === 1000 / 24));
        assert.ok(![...timers.values()].some(timer => timer.ms === 1000 / 15));
        update("toasts", false);
        members["3"] = { userId: "3" };
        plugin.flux.VOICE_STATE_UPDATES();
        await tick();
        assert.ok(!messages.some(message => message.t === "toast"));
        update("toasts", true);
        delete members["3"];
        plugin.flux.VOICE_STATE_UPDATES();
        assert.equal(messages.at(-1).t, "toast");
        // Invalid saved appearance values use effective defaults, not rewritten settings.
        update("accentColor", "bad");
        await tick();
        assert.equal(messages.at(-1).config.accent, "#23a55a");
        assert.equal(store.accentColor, "bad");
        update("port", "12345");
        assert.deepEqual(calls.at(-1), ["port", 12345]);
        epoch = 2;
        await tick();
        // Diagnostics expose effective settings and snapshot readiness, not images.
        const originalLog = console.log;
        let report;
        try {
            console.log = () => {};
            report = debugWindow.VencOverlayDebug();
        } finally {
            console.log = originalLog;
        }
        assert.deepEqual(report.connection, { port: 12345, epoch: 2, snapshotReady: true });
        assert.deepEqual(report.settings, settingsModule.readSettings(store));
        update("port", "0");
        assert.deepEqual(calls.at(-1), ["port", 6889]);
        assert.equal(store.port, "0");
        plugin.stop();
        const stopped = calls.length;
        update("port", 12345);
        update("webcamFps", 5);
        update("style", "discord");
        assert.equal(calls.length, stopped);
        assert.equal(timers.size, 0);
        assert.equal(debugWindow.VencOverlayDebug, undefined);
        plugin.start();
        assert.deepEqual(calls.at(-1), ["start", 12345]);
        assert.ok([...timers.values()].some(timer => timer.ms === 1000 / 5));
        plugin.stop();
        return;
    }
    if (webcamTests) {
        const capture = (fps = 15) => {
            const timer = [...timers.values()].find(timer => timer.ms === 1000 / fps);
            assert.ok(timer);
            return timer.fn();
        };
        // Only a confirmed, published camera is eligible.
        await capture();
        assert.equal(frameCalls.length, 0);
        video.tile = "2";
        // Restarting discovery with a relevant setting change.
        store.webcamWidth = 320;
        definitions.webcamWidth.onChange();
        await capture();
        assert.equal(frameCalls.length, 0);
        await tick();
        canSend = false;
        const beforeScan = scans;
        await capture();
        assert.equal(scans, beforeScan);
        assert.equal(encodes, 0);
        canSend = true;
        await capture();
        assert.deepEqual(frameCalls, [["2", 640, 360, 1]]);
        let resolveBlob;
        deferredBlob = new Promise(resolve => { resolveBlob = resolve; });
        const pending = capture();
        await Promise.resolve();
        await Promise.resolve();
        const before = encodes;
        await capture();
        assert.equal(encodes, before);
        // Disable and re-enable while an old image is encoding.
        store.showWebcams = false;
        definitions.showWebcams.onChange();
        await tick();
        store.showWebcams = true;
        definitions.showWebcams.onChange();
        await tick();
        await capture();
        assert.equal(encodes, before);
        resolveBlob(blob);
        await pending;
        deferredBlob = null;
        assert.equal(frameCalls.length, 1);
        await capture();
        assert.equal(frameCalls.length, 2);
        // Changing FPS replaces the interval and invalidates an old capture.
        deferredBlob = new Promise(resolve => { resolveBlob = resolve; });
        const pendingRate = capture();
        await Promise.resolve();
        await Promise.resolve();
        store.webcamFps = 30;
        definitions.webcamFps.onChange(store.webcamFps);
        await capture(30);
        resolveBlob(blob);
        await pendingRate;
        deferredBlob = null;
        assert.equal(frameCalls.length, 2);
        await capture(30);
        assert.equal(frameCalls.length, 3);
        // Stop/start must not release the in-flight guard prematurely.
        deferredBlob = new Promise(resolve => { resolveBlob = resolve; });
        const pendingStop = capture(30);
        await Promise.resolve();
        await Promise.resolve();
        plugin.stop();
        assert.equal(timers.size, 0);
        plugin.start();
        epoch = 2;
        await tick();
        await capture(30);
        resolveBlob(blob);
        await pendingStop;
        deferredBlob = null;
        assert.equal(frameCalls.length, 3);
        await capture(30);
        assert.deepEqual(frameCalls.at(-1), ["2", 640, 360, 2]);
        plugin.stop();
        assert.equal(timers.size, 0);
        return;
    }
    if (voiceTests) {
        const states = () => messages.filter(message => message.t === "state");
        const toasts = () => messages.filter(message => message.t === "toast");
        const last = () => states().at(-1);
        assert.deepEqual(last().users.map(user => user.id), ["self", "2"]);
        assert.equal(last().users[1].name, "Zoë");
        assert.equal(last().users[0].avatar, "https://cdn.discordapp.com/embed/avatars/0.png");
        assert.equal(last().users[1].avatar, "https://cdn.discordapp.com/avatars/123/abcdef.png?size=128");
        assert.equal(toasts().length, 0);
        // Unknown users and explicit events from another channel are ignored.
        const before = calls.length;
        plugin.flux.SPEAKING({ userId: "unknown", speakingFlags: 1 });
        plugin.flux.SPEAKING({ userId: "2", speakingFlags: 1, channelId: "other" });
        assert.equal(calls.length, before);
        plugin.flux.SPEAKING({ userId: "2", speakingFlags: 1 });
        assert.deepEqual(messages.at(-1), { t: "speaking", id: "2", on: true });
        members["3"] = { userId: "3", selfMute: true, selfDeaf: true, selfVideo: true, selfStream: true };
        plugin.flux.VOICE_STATE_UPDATES();
        plugin.flux.VOICE_STATE_UPDATES();
        assert.equal(toasts().length, 1);
        assert.equal(toasts()[0].text, "Nickname joined");
        assert.equal(toasts()[0].avatar, "https://cdn.discordapp.com/guilds/456/users/789/avatars/a_abcdef.png?size=128");
        // New members' speaking activity belongs in the next snapshot.
        plugin.flux.SPEAKING({ userId: "3", speakingFlags: 1 });
        await tick();
        assert.ok(last().users.find(user => user.id === "3").speaking);
        assert.equal(last().users.find(user => user.id === "3").avatar, toasts()[0].avatar);
        delete members["2"];
        plugin.flux.VOICE_STATE_UPDATES();
        assert.equal(toasts().at(-1).text, "Zoë left");
        members["2"] = { userId: "2" };
        plugin.flux.VOICE_STATE_UPDATES();
        await tick();
        assert.equal(last().users.find(user => user.id === "2").speaking, false);
        const capture = () => {
            const timer = [...timers.values()].find(timer => timer.ms === 1000 / 15);
            assert.ok(timer);
            return timer.fn();
        };
        await capture();
        assert.deepEqual(frameCalls, [["3", 560, 315, 1]]);
        let resolveBlob;
        deferredBlob = new Promise(resolve => { resolveBlob = resolve; });
        const pendingFrame = capture();
        await Promise.resolve();
        // Switching channels clears speaking even for a shared participant and
        // prevents an already encoding frame from crossing into the new call.
        let resolveSend;
        deferredSend = new Promise(resolve => { resolveSend = resolve; });
        plugin.flux.USER_UPDATE();
        const pendingSnapshot = tick();
        await Promise.resolve();
        assert.equal(last().channel.id, "1");
        const toastCount = toasts().length;
        channelId = "4";
        members = { self: { userId: "self" }, "3": { userId: "3", selfVideo: true } };
        plugin.flux.VOICE_CHANNEL_SELECT();
        plugin.flux.SPEAKING({ userId: "3", speakingFlags: 1, channelId: "1" });
        assert.equal(toasts().length, toastCount);
        resolveSend(true);
        await pendingSnapshot;
        deferredSend = null;
        await tick();
        assert.equal(last().channel.id, "4");
        assert.equal(last().users.find(user => user.id === "3").speaking, false);
        resolveBlob(blob);
        await pendingFrame;
        deferredBlob = null;
        assert.equal(frameCalls.length, 1);
        await capture();
        assert.equal(frameCalls.length, 2);
        // Missing voice store must not produce spurious leave/join events.
        members = undefined;
        plugin.flux.RTC_CONNECTION_STATE();
        await tick();
        assert.equal(last().channel, null);
        members = { self: { userId: "self" }, missing: { userId: "missing" } };
        plugin.flux.VOICE_STATE_UPDATES();
        await tick();
        assert.equal(last().users[1].name, "Unknown");
        assert.equal(last().users[1].avatar, null);
        assert.equal(toasts().length, toastCount);
        store.showSelf = false;
        definitions.showSelf.onChange();
        await tick();
        assert.deepEqual(last().users.map(user => user.id), ["missing"]);
        // Selection can lag behind the local user's departure.
        delete members.self;
        plugin.flux.VOICE_STATE_UPDATES();
        await tick();
        assert.equal(last().channel, null);
        assert.equal(toasts().length, toastCount);
        plugin.stop();
        members.self = { userId: "self" };
        plugin.start();
        epoch = 2;
        await tick();
        assert.equal(last().channel.id, "4");
        assert.equal(toasts().length, toastCount);
        plugin.stop();
        assert.equal(timers.size, 0);
        return;
    }
    plugin.flux.SPEAKING({ userId: "2", speakingFlags: 1 });
    assert.deepEqual(calls.at(-1), ["send", "speaking", 1]);
    await tick();
    assert.deepEqual(calls.at(-1), ["send", "speaking", 1]);
    epoch = null;
    await tick();
    plugin.flux.SPEAKING({ userId: "2", speakingFlags: 0 });
    assert.deepEqual(calls.at(-1), ["send", "speaking", 1]);
    epoch = 2;
    await tick();
    assert.deepEqual(calls.at(-1), ["send", "state", 2]);
    let resolveStatus;
    deferredStatus = new Promise(resolve => { resolveStatus = resolve; });
    const pending = tick();
    definitions.port.onChange(12345);
    assert.deepEqual(calls.at(-1), ["port", 12345]);
    resolveStatus(2);
    await pending;
    assert.deepEqual(calls.at(-1), ["port", 12345]);
    deferredStatus = null;
    epoch = 3;
    await tick();
    assert.deepEqual(calls.at(-1), ["send", "state", 3]);
    deferredStatus = new Promise(resolve => { resolveStatus = resolve; });
    const pendingStop = tick();
    plugin.stop();
    assert.equal(timers.size, 0);
    resolveStatus(4);
    await pendingStop;
    assert.deepEqual(calls.at(-1), ["disconnect"]);
    definitions.port.onChange(6889);
    plugin.flux.SPEAKING({ userId: "2", speakingFlags: 1 });
    assert.deepEqual(calls.at(-1), ["disconnect"]);
}

function voice() {
    const tracker = new voiceModule.VoiceTracker(id => ({ name: id === "2" ? "Zoë" : id, avatar: null }));
    const channel = { id: "1", name: "Call", guild: "Guild", icon: null };
    const self = { userId: "self", selfVideo: true };
    const other = { userId: "2", mute: true, selfDeaf: true, selfVideo: true, selfStream: true };
    const snapshot = (showSelf = true, showOwnWebcam = true) => tracker.snapshot(preferences, "guild", showSelf, showOwnWebcam);
    assert.deepEqual(tracker.observe(channel, { self, other }, "self", "guild"), []);
    assert.deepEqual(snapshot().users.map(user => user.id), ["self", "2"]);
    assert.equal(snapshot().users[1].serverMuted, true);
    assert.equal(snapshot().users[1].deaf, true);
    assert.equal(snapshot().users[1].live, true);
    assert.equal(snapshot(true, false).users[0].video, false);
    assert.deepEqual([...tracker.videoUsers(false, true)], ["2"]);
    assert.equal(tracker.updateSpeaking("unknown", true), null);
    assert.equal(tracker.updateSpeaking("2", true, "other"), null);
    assert.deepEqual(tracker.updateSpeaking("2", true), { t: "speaking", id: "2", on: true });
    assert.equal(tracker.updateSpeaking("2", true), null);
    const session = tracker.session;
    const revision = tracker.revision;
    assert.deepEqual(tracker.observe(channel, { self }, "self", "guild"), [{ t: "toast", kind: "leave", text: "Zoë left", avatar: null }]);
    assert.equal(tracker.session, session);
    assert.ok(tracker.revision > revision);
    assert.deepEqual(tracker.observe(channel, { self, other }, "self", "guild"), [{ t: "toast", kind: "join", text: "Zoë joined", avatar: null }]);
    assert.equal(snapshot().users[1].speaking, false);
    // Duplicate/incomplete snapshots cannot generate membership notifications.
    assert.deepEqual(tracker.observe(channel, { self, malformed: null }, "self", "guild"), []);
    assert.deepEqual(tracker.observe(channel, { self, other, duplicate: other }, "self", "guild"), []);
    assert.deepEqual(tracker.observe(channel, { self, other }, "self", "guild"), []);
    tracker.updateSpeaking("2", true);
    assert.deepEqual(tracker.observe({ ...channel, id: "2" }, { self, other }, "self", "guild"), []);
    assert.ok(tracker.session > session);
    assert.equal(snapshot().users[1].speaking, false);
    assert.deepEqual(tracker.observe(channel, undefined, "self", "guild"), []);
    assert.equal(snapshot().channel, null);
    assert.deepEqual(snapshot().users, []);
    assert.deepEqual(tracker.observe(channel, { other }, "self", "guild"), []);
    assert.equal(snapshot().channel, null);
    assert.deepEqual(tracker.observe(channel, { self, other }, "self", "guild"), []);
    tracker.reset();
    assert.deepEqual(snapshot().users, []);
    assert.equal(tracker.updateSpeaking("2", true), null);
}

function cameraVideo(tile, width = 640, height = 360) {
    return {
        tile, videoWidth: width, videoHeight: height, readyState: 2,
        isConnected: true, paused: false, ended: false, srcObject: {}, currentSrc: "",
        closest() { return this.tile === null ? null : { getAttribute: () => this.tile }; },
    };
}

async function webcam() {
    const { WebcamCapture, discoverCameras } = webcamModule;
    const candidates = new Set(["2", "3"]);
    const small = cameraVideo("2", 320, 180);
    const camera = cameraVideo("2");
    const second = cameraVideo("3");
    const unrelated = cameraVideo(null, 1920, 1080);
    const share = cameraVideo("guild:1:2:3");
    const stranger = cameraVideo("4");
    const paused = cameraVideo("3");
    paused.paused = true;
    const detached = cameraVideo("3");
    detached.isConnected = false;
    const invalid = cameraVideo("3", 0, 0);
    const elements = [small, camera, second, unrelated, share, stranger, paused, detached, invalid];
    assert.deepEqual([...discoverCameras(elements, candidates)], [["2", camera], ["3", second]]);
    assert.deepEqual([...discoverCameras([share, unrelated], new Set(["2"]))], []);
    let now = 0;
    let scans = 0;
    let allocations = 0;
    let encodes = 0;
    let canSend = true;
    let isCurrent = true;
    let deferred = null;
    let blobSize = 1;
    let frameBytes = 1;
    const frames = [];
    const drawn = [];
    const blob = () => ({ size: blobSize, arrayBuffer: async () => new Uint8Array(frameBytes).buffer });
    const capture = new WebcamCapture({
        videos: () => { scans++; return elements; },
        createCanvas: (width, height) => {
            allocations++;
            return { width, height,
                getContext: () => ({ drawImage: video => drawn.push(video) }),
                convertToBlob: async options => {
                    assert.deepEqual(options, { type: "image/jpeg", quality: 0.72 });
                    encodes++;
                    return deferred ?? blob();
                },
            };
        },
        now: () => now,
        onError: error => assert.fail(String(error)),
    });
    const request = {
        scope: "call1", candidates, tileWidth: 280,
        isCurrent: () => isCurrent,
        canSend: async () => canSend,
        send: async (id, bytes, w, h) => { frames.push([id, bytes.length, w, h]); return true; },
    };
    canSend = false;
    await capture.capture(request);
    assert.equal(scans, 0);
    assert.equal(allocations, 0);
    canSend = true;
    await capture.capture(request);
    assert.deepEqual(frames, [["2", 1, 560, 315], ["3", 1, 560, 315]]);
    assert.deepEqual(drawn, [camera, second]);
    assert.equal(allocations, 1);
    now = 499;
    await capture.capture(request);
    assert.equal(scans, 1);
    assert.equal(allocations, 1);
    now = 500;
    await capture.capture(request);
    assert.equal(scans, 2);
    // Invalidated cached element must not be captured before the next scan.
    camera.tile = "guild:1:2:2";
    second.srcObject = {};
    const count = frames.length;
    await capture.capture(request);
    assert.equal(frames.length, count);
    now = 1000;
    await capture.capture(request);
    assert.deepEqual(frames.slice(count), [["2", 1, 320, 180], ["3", 1, 560, 315]]);
    // Overlapping cycles cannot overwrite a canvas being encoded.
    let resolve;
    deferred = new Promise(done => { resolve = done; });
    const pending = capture.capture(request);
    await Promise.resolve();
    await Promise.resolve();
    const before = encodes;
    await capture.capture(request);
    assert.equal(encodes, before);
    capture.clear();
    await capture.capture({ ...request, scope: "call2" });
    assert.equal(encodes, before);
    const oldCount = frames.length;
    resolve(blob());
    await pending;
    deferred = null;
    assert.equal(frames.length, oldCount);
    await capture.capture({ ...request, scope: "call2" });
    assert.equal(frames.length, oldCount + 2);
    // Source replacement while encoding also invalidates the individual frame.
    deferred = new Promise(done => { resolve = done; });
    const single = { ...request, scope: "single", candidates: new Set(["3"]) };
    const pendingSource = capture.capture(single);
    await Promise.resolve();
    await Promise.resolve();
    const sourceCount = frames.length;
    second.srcObject = {};
    resolve(blob());
    await pendingSource;
    deferred = null;
    assert.equal(frames.length, sourceCount);
    now += 500;
    blobSize = p.MAX_FRAME_BYTES + 1;
    await capture.capture(single);
    assert.equal(frames.length, sourceCount);
    blobSize = 1;
    frameBytes = p.MAX_FRAME_BYTES + 1;
    await capture.capture(single);
    assert.equal(frames.length, sourceCount);
    frameBytes = 1;
    isCurrent = false;
    await capture.capture(single);
    assert.equal(frames.length, sourceCount);
    isCurrent = true;
    await capture.capture(single);
    assert.equal(frames.length, sourceCount + 1);
    // Capacity is checked again before encoding another participant.
    let capacityChecks = 0;
    const congestion = { ...request, scope: "congested", canSend: async () => ++capacityChecks === 1 };
    const congestionCount = frames.length;
    await capture.capture(congestion);
    assert.equal(capacityChecks, 2);
    assert.equal(frames.length, congestionCount + 1);
    capacityChecks = 0;
    await capture.capture(congestion);
    assert.equal(frames.length, congestionCount + 2);
    assert.notEqual(frames.at(-1)[0], frames.at(-2)[0]);
    const finalScans = scans;
    await capture.capture({ ...request, candidates: new Set() });
    assert.equal(scans, finalScans);
    const released = allocations;
    await capture.capture(request);
    assert.equal(allocations, released + 1);
}

switch (process.argv[3]) {
    case "webcam": webcam().catch(error => { console.error(error); process.exitCode = 1; }); break;
    case "settings-renderer": renderer(false, false, true).catch(error => { console.error(error); process.exitCode = 1; }); break;
    case "webcam-renderer": renderer(false, true).catch(error => { console.error(error); process.exitCode = 1; }); break;
    case "wire": wire(); break;
    case "boundaries": boundaries(); break;
    case "voice": voice(); break;
    case "voice-renderer": renderer(true).catch(error => { console.error(error); process.exitCode = 1; }); break;
    case "renderer": renderer().catch(error => { console.error(error); process.exitCode = 1; }); break;
    case "reconnect": reconnect().catch(error => { console.error(error); process.exitCode = 1; }); break;
    case "lifecycle": lifecycle(); break;
    case "native": native().catch(error => { console.error(error); process.exitCode = 1; }); break;
    default: throw new Error("Unknown test mode");
}
