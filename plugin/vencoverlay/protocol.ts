/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 akiloyutu (Akii-G) and VencOverlay contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

// Keep these limits aligned with src/vencoverlay/protocol.py.
export const MAX_MESSAGE_BYTES = 8 * 1024 * 1024;
export const MAX_FRAME_BYTES = 2 * 1024 * 1024;
export const MAX_FRAME_WIDTH = 960;
export const MAX_FRAME_HEIGHT = 1920;
export const DEFAULT_PORT = 6889;

const POSITIONS = ["top-left", "top-right", "bottom-left", "bottom-right", "middle-left", "middle-right"] as const;
const STYLES = ["discord", "mac"] as const;
const NAME_MODES = ["always", "speaking", "never"] as const;
const AVATAR_SIZES = ["large", "small"] as const;
const TOAST_KINDS = ["join", "leave"] as const;
export const FRAME_RATES = [5, 10, 15, 20, 24, 30] as const;
export const DEFAULT_FRAME_RATE = 15;
export const PREFERENCE_LIMITS = {
    scale: [0.6, 1.8],
    opacity: [0.3, 1],
    webcamWidth: [160, 480],
} as const;

export interface Preferences {
    position: typeof POSITIONS[number];
    scale: number;
    opacity: number;
    accent: string;
    style: typeof STYLES[number];
    showNames: typeof NAME_MODES[number];
    avatarSize: typeof AVATAR_SIZES[number];
    onlySpeaking: boolean;
    showWebcams: boolean;
    webcamWidth: number;
}

// Shared by the settings UI and the wire-format normaliser.
export const DEFAULT_PREFERENCES = {
    position: "top-left",
    scale: 1,
    opacity: 0.85,
    accent: "#23a55a",
    style: "discord",
    showNames: "always",
    avatarSize: "large",
    onlySpeaking: false,
    showWebcams: true,
    webcamWidth: 280,
} as const satisfies Preferences;

export interface Channel {
    id: string;
    name: string;
    guild: string | null;
    icon: string | null;
}

export interface Participant {
    id: string;
    name: string;
    avatar: string | null;
    me: boolean;
    speaking: boolean;
    mute: boolean;
    deaf: boolean;
    serverMuted: boolean;
    video: boolean;
    live: boolean;
}

export interface StateMessage {
    t: "state";
    channel: Channel | null;
    users: Participant[];
    config: Preferences;
}

export interface SpeakingMessage {
    t: "speaking";
    id: string;
    on: boolean;
}

export interface ToastMessage {
    t: "toast";
    kind: typeof TOAST_KINDS[number];
    text: string;
    avatar: string | null;
}

export interface FrameMessage {
    t: "frame";
    id: string;
    w: number;
    h: number;
    data: string;
}

export type ControlMessage = StateMessage | SpeakingMessage | ToastMessage;
export type Message = ControlMessage | FrameMessage;

type Fields = Record<string, unknown>;

function isRecord(value: unknown): value is Fields {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasFields(value: Fields, fields: readonly string[]): boolean {
    return Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
}

function isChoice<T extends string | number>(value: unknown, choices: readonly T[]): value is T {
    return choices.some(choice => choice === value);
}

function isNumber(value: unknown, low: number, high: number): value is number {
    return typeof value === "number" && Number.isFinite(value) && value >= low && value <= high;
}

function isInteger(value: unknown, low: number, high: number): value is number {
    return isNumber(value, low, high) && Number.isInteger(value);
}

function isNullableString(value: unknown): value is string | null {
    return value === null || typeof value === "string";
}

function isAccent(value: unknown): value is string {
    if (typeof value !== "string") return false;
    const hex = value.startsWith("#") ? value.slice(1) : value;
    return hex.length === 6 && !/[^0-9a-fA-F]/.test(hex);
}

export function normaliseBoolean(value: unknown, fallback: boolean): boolean {
    return typeof value === "boolean" ? value : fallback;
}

export function isPort(value: unknown): value is number {
    return isInteger(value, 1, 65535);
}

export function normalisePort(value: unknown): number {
    // The number input may supply a string when edited in the settings UI.
    const port = typeof value === "string" && /^[0-9]+$/.test(value) ? Number(value) : value;
    return isPort(port) ? port : DEFAULT_PORT;
}

export function normaliseFrameRate(value: unknown): number {
    return isChoice(value, FRAME_RATES) ? value : DEFAULT_FRAME_RATE;
}

export function normalisePreferences(value: unknown): Preferences {
    const s = isRecord(value) ? value : {};
    return {
        position: isChoice(s.position, POSITIONS) ? s.position : DEFAULT_PREFERENCES.position,
        scale: isNumber(s.scale, ...PREFERENCE_LIMITS.scale) ? s.scale : DEFAULT_PREFERENCES.scale,
        opacity: isNumber(s.opacity, ...PREFERENCE_LIMITS.opacity) ? s.opacity : DEFAULT_PREFERENCES.opacity,
        accent: isAccent(s.accent) ? s.accent : DEFAULT_PREFERENCES.accent,
        style: isChoice(s.style, STYLES) ? s.style : DEFAULT_PREFERENCES.style,
        showNames: isChoice(s.showNames, NAME_MODES) ? s.showNames : DEFAULT_PREFERENCES.showNames,
        avatarSize: isChoice(s.avatarSize, AVATAR_SIZES) ? s.avatarSize : DEFAULT_PREFERENCES.avatarSize,
        onlySpeaking: normaliseBoolean(s.onlySpeaking, DEFAULT_PREFERENCES.onlySpeaking),
        showWebcams: normaliseBoolean(s.showWebcams, DEFAULT_PREFERENCES.showWebcams),
        webcamWidth: isInteger(s.webcamWidth, ...PREFERENCE_LIMITS.webcamWidth) ? s.webcamWidth : DEFAULT_PREFERENCES.webcamWidth,
    };
}

function isPreferences(value: unknown): value is Preferences {
    return isRecord(value)
        && hasFields(value, ["position", "scale", "opacity", "accent", "style", "showNames", "avatarSize", "onlySpeaking", "showWebcams", "webcamWidth"])
        && isChoice(value.position, POSITIONS)
        && isNumber(value.scale, ...PREFERENCE_LIMITS.scale)
        && isNumber(value.opacity, ...PREFERENCE_LIMITS.opacity)
        && isAccent(value.accent)
        && isChoice(value.style, STYLES)
        && isChoice(value.showNames, NAME_MODES)
        && isChoice(value.avatarSize, AVATAR_SIZES)
        && typeof value.onlySpeaking === "boolean"
        && typeof value.showWebcams === "boolean"
        && isInteger(value.webcamWidth, ...PREFERENCE_LIMITS.webcamWidth);
}

function isChannel(value: unknown): value is Channel {
    return isRecord(value)
        && hasFields(value, ["id", "name", "guild", "icon"])
        && typeof value.id === "string"
        && typeof value.name === "string"
        && isNullableString(value.guild)
        && isNullableString(value.icon);
}

function isParticipant(value: unknown): value is Participant {
    return isRecord(value)
        && hasFields(value, ["id", "name", "avatar", "me", "speaking", "mute", "deaf", "serverMuted", "video", "live"])
        && typeof value.id === "string"
        && typeof value.name === "string"
        && isNullableString(value.avatar)
        && [value.me, value.speaking, value.mute, value.deaf, value.serverMuted, value.video, value.live].every(flag => typeof flag === "boolean");
}

function isParticipants(value: unknown): value is Participant[] {
    if (!Array.isArray(value)) return false;
    const ids = new Set<string>();
    for (const user of value) {
        if (!isParticipant(user) || ids.has(user.id)) return false;
        ids.add(user.id);
    }
    return true;
}

export function isControlMessage(value: unknown): value is ControlMessage {
    if (!isRecord(value)) return false;
    switch (value.t) {
        case "state":
            return hasFields(value, ["t", "channel", "users", "config"])
                && (value.channel === null || isChannel(value.channel))
                && isParticipants(value.users)
                && isPreferences(value.config);
        case "speaking":
            return hasFields(value, ["t", "id", "on"])
                && typeof value.id === "string" && typeof value.on === "boolean";
        case "toast":
            return hasFields(value, ["t", "kind", "text", "avatar"])
                && isChoice(value.kind, TOAST_KINDS)
                && typeof value.text === "string" && isNullableString(value.avatar);
        default:
            return false;
    }
}

function serialise(message: Message): string | null {
    const line = JSON.stringify(message);
    // The Python limit excludes the newline separator and counts UTF-8 bytes.
    if (line.length > MAX_MESSAGE_BYTES || new TextEncoder().encode(line).length > MAX_MESSAGE_BYTES) return null;
    return line;
}

export function serialiseControl(value: unknown): string | null {
    return isControlMessage(value) ? serialise(value) : null;
}

export function isFramePayload(id: unknown, jpeg: unknown, w: unknown, h: unknown): boolean {
    return typeof id === "string" && jpeg instanceof Uint8Array && jpeg.byteLength > 0 && jpeg.byteLength <= MAX_FRAME_BYTES
        && isInteger(w, 1, MAX_FRAME_WIDTH) && isInteger(h, 1, MAX_FRAME_HEIGHT);
}

export function serialiseFrame(id: string, jpeg: Uint8Array, w: number, h: number, encode: (bytes: Uint8Array) => string): string | null {
    if (!isFramePayload(id, jpeg, w, h)) return null;
    return serialise({ t: "frame", id, w, h, data: encode(jpeg) });
}

export function captureDimensions(width: number, height: number, tileWidth: number): { w: number; h: number; } | null {
    if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width < 1 || height < 1) return null;
    const tile = isInteger(tileWidth, ...PREFERENCE_LIMITS.webcamWidth) ? tileWidth : DEFAULT_PREFERENCES.webcamWidth;
    const scale = Math.min(1, tile * 2 / width, MAX_FRAME_WIDTH / width, MAX_FRAME_HEIGHT / height);
    return {
        w: Math.max(1, Math.min(MAX_FRAME_WIDTH, Math.round(width * scale))),
        h: Math.max(1, Math.min(MAX_FRAME_HEIGHT, Math.round(height * scale))),
    };
}
