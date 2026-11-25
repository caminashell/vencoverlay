/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 akiloyutu (Akii-G) and VencOverlay contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import { definePluginSettings, migratePluginSettings } from "@api/Settings";
import { makeRange, OptionType } from "@utils/types";

import { DEFAULT_FRAME_RATE, DEFAULT_PORT, DEFAULT_PREFERENCES, FRAME_RATES, normaliseBoolean, normaliseFrameRate, normalisePort, normalisePreferences, PREFERENCE_LIMITS, Preferences } from "./protocol";

const LOCAL_DEFAULTS = {
    showSelf: true,
    showOwnWebcam: true,
    toasts: true,
} as const;

interface SettingsCallbacks {
    appearance(): void;
    webcam(): void;
    frameRate(fps: number): void;
    port(port: number): void;
}

interface EffectiveSettings {
    preferences: Preferences;
    showSelf: boolean;
    showOwnWebcam: boolean;
    toasts: boolean;
    webcamFps: number;
    port: number;
}

/** Read safe effective values without modifying Vencord's saved settings. */
export function readSettings(value: unknown): EffectiveSettings {
    const s = typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
    return {
        preferences: normalisePreferences({
            position: s.position,
            scale: s.scale,
            opacity: s.opacity,
            accent: s.accentColor,
            style: s.style,
            showNames: s.showNames,
            avatarSize: s.avatarSize,
            onlySpeaking: s.showOnlySpeaking,
            showWebcams: s.showWebcams,
            webcamWidth: s.webcamWidth,
        }),
        showSelf: normaliseBoolean(s.showSelf, LOCAL_DEFAULTS.showSelf),
        showOwnWebcam: normaliseBoolean(s.showOwnWebcam, LOCAL_DEFAULTS.showOwnWebcam),
        toasts: normaliseBoolean(s.toasts, LOCAL_DEFAULTS.toasts),
        webcamFps: normaliseFrameRate(s.webcamFps),
        port: normalisePort(s.port),
    };
}

export function createSettings(callbacks: SettingsCallbacks) {
    // Preserve saved preferences and the enabled state when upgrading the plugin.
    migratePluginSettings("VencOverlay", "OverlayBridge");
    return definePluginSettings({
        style: {
            type: OptionType.SELECT,
            description: "Overlay visual style",
            options: [
                { label: "Discord-style overlay", value: DEFAULT_PREFERENCES.style, default: true },
                { label: "Mac-style cards", value: "mac" },
            ],
            onChange: callbacks.appearance,
        },
        showNames: {
            type: OptionType.SELECT,
            description: "Show names",
            options: [
                { label: "Always", value: DEFAULT_PREFERENCES.showNames, default: true },
                { label: "Only while speaking", value: "speaking" },
                { label: "Never", value: "never" },
            ],
            onChange: callbacks.appearance,
        },
        avatarSize: {
            type: OptionType.SELECT,
            description: "Avatar size",
            options: [
                { label: "Large", value: DEFAULT_PREFERENCES.avatarSize, default: true },
                { label: "Small", value: "small" },
            ],
            onChange: callbacks.appearance,
        },
        position: {
            type: OptionType.SELECT,
            description: "Overlay position on the selected monitor",
            options: [
                { label: "Top left", value: DEFAULT_PREFERENCES.position, default: true },
                { label: "Top right", value: "top-right" },
                { label: "Bottom left", value: "bottom-left" },
                { label: "Bottom right", value: "bottom-right" },
                { label: "Left middle", value: "middle-left" },
                { label: "Right middle", value: "middle-right" },
            ],
            onChange: callbacks.appearance,
        },
        scale: {
            type: OptionType.SLIDER,
            description: "Overlay scale",
            default: DEFAULT_PREFERENCES.scale,
            markers: makeRange(...PREFERENCE_LIMITS.scale, 0.2),
            onChange: callbacks.appearance,
        },
        opacity: {
            type: OptionType.SLIDER,
            description: "Card opacity (lower values are more transparent)",
            default: DEFAULT_PREFERENCES.opacity,
            markers: makeRange(...PREFERENCE_LIMITS.opacity, 0.1),
            onChange: callbacks.appearance,
        },
        accentColor: {
            type: OptionType.STRING,
            description: "Speaking glow colour (six hexadecimal digits, with or without #)",
            default: DEFAULT_PREFERENCES.accent,
            onChange: callbacks.appearance,
        },
        showOnlySpeaking: {
            type: OptionType.BOOLEAN,
            description: "Only show speaking participants and participants with an active webcam",
            default: DEFAULT_PREFERENCES.onlySpeaking,
            onChange: callbacks.appearance,
        },
        showSelf: {
            type: OptionType.BOOLEAN,
            description: "Show yourself in the overlay",
            default: LOCAL_DEFAULTS.showSelf,
            onChange: callbacks.webcam,
        },
        showWebcams: {
            type: OptionType.BOOLEAN,
            description: "Show participant webcams (keep the Discord call view open; screen shares are excluded)",
            default: DEFAULT_PREFERENCES.showWebcams,
            onChange: callbacks.webcam,
        },
        showOwnWebcam: {
            type: OptionType.BOOLEAN,
            description: "Show your own webcam preview (requires Show webcams and Show yourself)",
            default: LOCAL_DEFAULTS.showOwnWebcam,
            onChange: callbacks.webcam,
        },
        webcamFps: {
            type: OptionType.SLIDER,
            description: "Maximum webcam frames per second (lower values reduce capture work)",
            default: DEFAULT_FRAME_RATE,
            markers: [...FRAME_RATES],
            stickToMarkers: true,
            onChange: v => callbacks.frameRate(normaliseFrameRate(v)),
        },
        webcamWidth: {
            type: OptionType.SLIDER,
            description: "Webcam tile width in pixels before overlay scaling",
            default: DEFAULT_PREFERENCES.webcamWidth,
            markers: [PREFERENCE_LIMITS.webcamWidth[0], 200, 240, DEFAULT_PREFERENCES.webcamWidth, 320, 400, PREFERENCE_LIMITS.webcamWidth[1]],
            stickToMarkers: true,
            onChange: callbacks.webcam,
        },
        toasts: {
            type: OptionType.BOOLEAN,
            description: "Show a notification when another participant joins or leaves your call",
            default: LOCAL_DEFAULTS.toasts,
        },
        port: {
            type: OptionType.NUMBER,
            description: "Local overlay connection port (1–65535; must match the application configuration)",
            default: DEFAULT_PORT,
            onChange: v => callbacks.port(normalisePort(v)),
        },
    });
}
