/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 akiloyutu (Akii-G) and VencOverlay contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import { IpcMainInvokeEvent } from "electron";
import { connect, Socket } from "net";

import { ControlMessage, DEFAULT_PORT, isFramePayload, isPort, MAX_MESSAGE_BYTES, serialiseControl, serialiseFrame } from "./protocol";

const RETRY_MS = 2000;
const CONNECT_TIMEOUT_MS = 5000;
const MAX_FRAME_BUFFER = 4 * 1024 * 1024;
const MAX_CONTROL_BUFFER = MAX_MESSAGE_BYTES + 1;

let socket: Socket | null = null;
let active = false;
let connected = false;
let synchronised = false;
let epoch = 0;
let port = DEFAULT_PORT;
let retry: ReturnType<typeof setTimeout> | null = null;
let reportedFailure = false;

function cancelConnection() {
    if (retry !== null) clearTimeout(retry);
    retry = null;
    const previous = socket;
    socket = null;
    connected = synchronised = false;
    previous?.destroy();
}

function scheduleRetry() {
    if (!active || retry !== null || socket !== null) return;
    retry = setTimeout(() => {
        retry = null;
        openConnection();
    }, RETRY_MS);
}

function reportFailure() {
    if (reportedFailure) return;
    reportedFailure = true;
    console.warn("[VencOverlay] Overlay connection unavailable; retrying.");
}

function openConnection() {
    if (!active || socket !== null) return;
    let current: Socket;
    try {
        current = connect({ host: "127.0.0.1", port });
    } catch {
        reportFailure();
        scheduleRetry();
        return;
    }
    // Own the socket from the start, not only after the connect event.
    socket = current;
    current.setNoDelay(true);
    current.setTimeout(CONNECT_TIMEOUT_MS);
    current.on("connect", () => {
        if (socket !== current || !active) return;
        current.setTimeout(0);
        connected = true;
        synchronised = false;
        epoch++;
        reportedFailure = false;
        console.info("[VencOverlay] Overlay connected.");
    });
    current.on("timeout", () => {
        if (socket === current && !connected) current.destroy();
    });
    current.on("error", () => current.destroy());
    current.on("close", () => {
        if (socket !== current) return;
        socket = null;
        connected = synchronised = false;
        if (active) {
            reportFailure();
            scheduleRetry();
        }
    });
    current.on("data", () => { /* The overlay never sends data. */ });
}

export function start(_: IpcMainInvokeEvent, newPort: number) {
    if (!isPort(newPort)) return false;
    if (active && port === newPort) return true;
    cancelConnection();
    port = newPort;
    active = true;
    reportedFailure = false;
    openConnection();
    return true;
}

export function setPort(_: IpcMainInvokeEvent, newPort: number) {
    if (!isPort(newPort) || !active) return false;
    return start(_, newPort);
}

export function connectionStatus() {
    return connected && socket && !socket.destroyed ? epoch : null;
}

function currentSocket(expectedEpoch: number): Socket | null {
    return active && connected && Number.isSafeInteger(expectedEpoch) && expectedEpoch === connectionStatus() && socket?.writable ? socket : null;
}

function write(current: Socket, line: string, limit: number, control: boolean) {
    if (current.writableLength + Buffer.byteLength(line, "utf8") + 1 > limit) {
        // Dropping control messages could corrupt receiver state. Reconnect and
        // require a new snapshot instead. Video is disposable under pressure.
        if (control) current.destroy();
        return false;
    }
    try {
        // false from write() means accepted but backpressured, not rejected.
        current.write(line + "\n");
        return true;
    } catch {
        current.destroy();
        return false;
    }
}

export function send(_: IpcMainInvokeEvent, message: ControlMessage, expectedEpoch: number) {
    const current = currentSocket(expectedEpoch);
    if (!current) return false;
    const line = serialiseControl(message);
    if (line === null || (!synchronised && message.t !== "state")) return false;
    if (!write(current, line, MAX_CONTROL_BUFFER, true)) return false;
    if (message.t === "state") synchronised = true;
    return true;
}

export function canSendFrame(_: IpcMainInvokeEvent, expectedEpoch: number) {
    const current = currentSocket(expectedEpoch);
    return !!current && synchronised && current.writableLength < MAX_FRAME_BUFFER;
}

export function sendFrame(_: IpcMainInvokeEvent, id: string, jpeg: Uint8Array, w: number, h: number, expectedEpoch: number) {
    const current = currentSocket(expectedEpoch);
    if (!current || !synchronised || current.writableLength >= MAX_FRAME_BUFFER || !isFramePayload(id, jpeg, w, h)) return false;
    const line = serialiseFrame(id, jpeg, w, h, bytes => Buffer.from(bytes).toString("base64"));
    return line !== null && write(current, line, MAX_FRAME_BUFFER, false);
}

export function disconnect() {
    active = false;
    cancelConnection();
}
