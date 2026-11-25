/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 akiloyutu (Akii-G) and VencOverlay contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import { Link } from "@components/Link";
import { Logger } from "@utils/Logger";
import definePlugin, { PluginNative, ReporterTestable } from "@utils/types";
import { ChannelStore, GuildMemberStore, GuildStore, React, SelectedChannelStore, UserStore, VoiceStateStore } from "@webpack/common";

import { ControlMessage, DEFAULT_PREFERENCES, normaliseBoolean, StateMessage } from "./protocol";
import { createSettings, readSettings } from "./settings";
import { VoiceTracker } from "./voice";
import { cameraUserId, discoverCameras, WebcamCapture } from "./webcam";

const Native = VencordNative.pluginHelpers.VencOverlay as PluginNative<typeof import("./native")>;
const logger = new Logger("VencOverlay");

const settings = createSettings({
    appearance: pushState,
    webcam: webcamSettingsChanged,
    frameRate: restartFrameLoop,
    port: changePort,
});

const voice = new VoiceTracker((id, guildId) => ({ name: displayName(id, guildId), avatar: avatarUrl(id, guildId) }));
let voiceGuildId: string | null = null;
let publishedSession: number | null = null;
let publishedRevision: number | null = null;
let pushTimer: ReturnType<typeof setTimeout> | null = null;
let heartbeat: ReturnType<typeof setInterval> | null = null;
let running = false;
let lifecycle = 0;
let connectionEpoch: number | null = null;
let stateDirty = true;
let lastStateSent = 0;
let syncing = false;
let ipcFailureReported = false;
let frameLoop: ReturnType<typeof setInterval> | null = null;
let cameraSettingsRevision = 0;
let publishedCameraSettingsRevision: number | null = null;
const webcam = new WebcamCapture({
    videos: () => Array.from(document.querySelectorAll("video")),
    createCanvas: (width, height) => new OffscreenCanvas(width, height),
    now: () => performance.now(),
    onError: error => logger.debug("Frame capture failed", error),
});

function readOptional<T>(read: () => T): T | undefined {
    try {
        return read();
    } catch {
        return undefined;
    }
}

function displayName(userId: string | undefined, guildId: string | null | undefined): string {
    if (!userId) return "Unknown";
    const nick = guildId ? readOptional(() => GuildMemberStore?.getNick(guildId, userId)) : null;
    const user = readOptional(() => UserStore?.getUser(userId));
    const globalName = user && "globalName" in user && typeof user.globalName === "string" ? user.globalName : null;
    return nick || globalName || user?.username || "Unknown";
}

function avatarUrl(userId: string, guildId: string | null | undefined): string | null {
    const source = readOptional(() => UserStore?.getUser(userId)?.getAvatarURL(guildId ?? undefined, 128, false, "png"));
    if (!source) return null;
    const url = readOptional(() => new URL(source));
    if (!url || url.protocol !== "https:") return source;
    if (url.hostname !== "cdn.discordapp.com" && url.hostname !== "media.discordapp.net") return source;

    // Discord can ignore getAvatarURL's requested format and size. Use the
    // documented CDN representation explicitly, preserving guild avatar hashes.
    const avatar = /^\/(?:avatars\/\d+|guilds\/\d+\/users\/\d+\/avatars)\/[\w-]+\.(?:png|webp|jpe?g|gif|avif)$/i;
    const defaultAvatar = /^\/embed\/avatars\/\d+\.png$/;
    if (avatar.test(url.pathname)) {
        return `https://cdn.discordapp.com${url.pathname.replace(/\.[^.]+$/, ".png")}?size=128`;
    }
    if (defaultAvatar.test(url.pathname)) return `https://cdn.discordapp.com${url.pathname}`;
    return source;
}

function refreshVoice() {
    if (!running) return;
    const previous = voice.revision;
    try {
        const channelId = SelectedChannelStore?.getVoiceChannelId();
        const channel = channelId ? readOptional(() => ChannelStore?.getChannel(channelId)) : null;
        voiceGuildId = channel?.guild_id ?? null;
        const guildId = voiceGuildId;
        const guild = guildId ? readOptional(() => GuildStore?.getGuild(guildId)) : null;
        const me = UserStore?.getCurrentUser()?.id ?? null;
        const states: unknown = channelId ? VoiceStateStore?.getVoiceStatesForChannel(channelId) : null;
        const name = channel?.name || (readOptional(() => channel?.isDM?.()) ? displayName(channel?.recipients?.[0], null) : "Call");
        const toasts = voice.observe(channelId ? {
            id: channelId, name, guild: guild?.name ?? null, icon: readOptional(() => (guild as any)?.getIconURL?.(64, false)) ?? null,
        } : null, states, me, voiceGuildId);
        if (readSettings(settings.store).toasts) {
            for (const toast of toasts) sendControl(toast);
        }
    } catch {
        // Store discovery and channel changes can be temporarily incomplete.
        voiceGuildId = null;
        voice.observe(null, null, null, null);
    }
    if (voice.revision !== previous) {
        webcam.clear();
        stateDirty = true;
    }
}

function buildState(): StateMessage {
    refreshVoice();
    const { preferences, showSelf, showOwnWebcam } = readSettings(settings.store);
    return voice.snapshot(preferences, voiceGuildId, showSelf, showOwnWebcam);
}

function reportIpcFailure(error: unknown) {
    if (ipcFailureReported) return;
    ipcFailureReported = true;
    logger.error("Overlay IPC failed", error);
}

function changePort(port: number) {
    if (!running) return;
    webcam.clear();
    connectionEpoch = null;
    publishedSession = null;
    stateDirty = true;
    // Invalidate an outstanding status/snapshot request for the old port.
    lifecycle++;
    Native.setPort(port).catch(reportIpcFailure);
}

async function synchroniseState() {
    if (!running || syncing) return;
    syncing = true;
    const generation = lifecycle;
    try {
        const epoch = await Native.connectionStatus();
        if (!running || generation !== lifecycle) return;
        if (epoch === null) {
            webcam.clear();
            connectionEpoch = null;
            publishedSession = null;
            return;
        }
        if (epoch !== connectionEpoch || stateDirty || Date.now() - lastStateSent >= 2000) {
            // Do not publish incremental events to a connection still receiving its initial state.
            if (epoch !== connectionEpoch) {
                webcam.clear();
                publishedSession = null;
            }
            const message = buildState();
            const { session, revision } = voice;
            const preferencesRevision = cameraSettingsRevision;
            stateDirty = false;
            const sent = await Native.send(message, epoch);
            if (!running || generation !== lifecycle) return;
            connectionEpoch = sent ? epoch : null;
            publishedSession = sent && session === voice.session ? session : null;
            publishedRevision = sent && revision === voice.revision ? revision : null;
            publishedCameraSettingsRevision = sent ? preferencesRevision : null;
            if (sent) lastStateSent = Date.now();
            if (!sent || revision !== voice.revision) stateDirty = true;
        }
        ipcFailureReported = false;
    } catch (error) {
        if (running && generation === lifecycle) {
            connectionEpoch = null;
            publishedSession = null;
            stateDirty = true;
            reportIpcFailure(error);
        }
    } finally {
        syncing = false;
    }
}

function pushState() {
    if (!running) return;
    stateDirty = true;
    if (pushTimer) return;
    pushTimer = setTimeout(() => {
        pushTimer = null;
        void synchroniseState();
    }, 30);
}

function sendControl(message: ControlMessage) {
    const epoch = connectionEpoch;
    if (!running || epoch === null || publishedSession !== voice.session) return;
    const generation = lifecycle;
    Native.send(message, epoch).then(sent => {
        if (!sent && running && generation === lifecycle && connectionEpoch === epoch) {
            connectionEpoch = null;
            publishedSession = null;
            stateDirty = true;
        }
    }).catch(error => {
        if (running && generation === lifecycle && connectionEpoch === epoch) {
            connectionEpoch = null;
            publishedSession = null;
            stateDirty = true;
            reportIpcFailure(error);
        }
    });
}

// Only explicitly identified camera tiles are captured; unknown videos remain placeholders.
function videoUsers() {
    const { showSelf, showOwnWebcam } = readSettings(settings.store);
    return voice.videoUsers(showSelf, showOwnWebcam);
}

// Run VencOverlayDebug() in Discord DevTools to inspect camera matching.
function debugVideos() {
    const { channelId } = voice;
    const candidates = channelId ? videoUsers() : new Set<string>();
    const elements = Array.from(document.querySelectorAll("video"));
    const videos = elements.map(video => ({
        size: `${video.videoWidth}x${video.videoHeight}`,
        readyState: video.readyState,
        hasStream: !!video.srcObject,
        tile: video.closest("[data-selenium-video-tile]")?.getAttribute("data-selenium-video-tile") ?? null,
        matched: cameraUserId(video, candidates),
    }));
    const found = [...discoverCameras(elements, candidates).keys()];
    const effective = readSettings(settings.store);
    const connection = {
        port: effective.port,
        epoch: connectionEpoch,
        snapshotReady: connectionEpoch !== null && !stateDirty && !syncing && publishedSession === voice.session
            && publishedRevision === voice.revision && publishedCameraSettingsRevision === cameraSettingsRevision,
    };
    const report = { channelId, candidates: [...candidates], videos, found, connection, settings: effective };
    console.log("[VencOverlay]", report);
    return report;
}

function webcamSettingsChanged() {
    cameraSettingsRevision++;
    webcam.clear();
    pushState();
}

async function captureFrames() {
    const epoch = connectionEpoch;
    const generation = lifecycle;
    const { revision, session, channelId } = voice;
    const preferencesRevision = cameraSettingsRevision;
    const current = () => running && epoch !== null && connectionEpoch === epoch && generation === lifecycle
        && channelId !== null && revision === voice.revision && session === voice.session
        && publishedSession === session && publishedRevision === revision
        && preferencesRevision === cameraSettingsRevision && publishedCameraSettingsRevision === preferencesRevision
        && normaliseBoolean(settings.store.showWebcams, DEFAULT_PREFERENCES.showWebcams)
        && readOptional(() => SelectedChannelStore?.getVoiceChannelId()) === channelId;
    if (epoch === null || !current()) {
        webcam.clear();
        return;
    }
    try {
        const { preferences, showSelf, showOwnWebcam } = readSettings(settings.store);
        await webcam.capture({
            scope: `${generation}:${epoch}:${session}:${revision}:${preferencesRevision}`,
            candidates: voice.videoUsers(showSelf, showOwnWebcam),
            tileWidth: preferences.webcamWidth,
            isCurrent: current,
            canSend: () => Native.canSendFrame(epoch),
            send: (id, bytes, width, height) => Native.sendFrame(id, bytes, width, height, epoch),
        });
    } catch (error) {
        reportIpcFailure(error);
    }
}

function restartFrameLoop(fps = readSettings(settings.store).webcamFps) {
    if (!running) return;
    webcam.clear();
    if (frameLoop) clearInterval(frameLoop);
    frameLoop = setInterval(captureFrames, 1000 / fps);
}

export default definePlugin({
    name: "VencOverlay",
    description: "Shows your Discord voice call over games: participants, speaking, mute/deafen and webcams. Requires the VencOverlay application.",
    tags: ["Voice", "Utility"],
    authors: [
        { name: "caminashell", id: 0n },
        { name: "akiloyutu", id: 0n },
    ],
    settingsAboutComponent: () => React.createElement(
        Link,
        { href: "https://github.com/caminashell/vencoverlay" },
        "VencOverlay source and support"
    ),
    reporterTestable: ReporterTestable.None,
    settings,

    flux: {
        VOICE_STATE_UPDATES() {
            refreshVoice();
            pushState();
        },
        VOICE_CHANNEL_SELECT() {
            refreshVoice();
            pushState();
        },
        RTC_CONNECTION_STATE() {
            refreshVoice();
            pushState();
        },
        SPEAKING({ userId, speakingFlags, channelId }: { userId: string; speakingFlags: number; channelId?: string; }) {
            if (!running || typeof userId !== "string" || !Number.isFinite(speakingFlags)) return;
            // A channel transition may precede its corresponding Flux event.
            try {
                if ((SelectedChannelStore?.getVoiceChannelId() ?? null) !== voice.channelId || !voice.hasMember(userId)) refreshVoice();
            } catch {
                refreshVoice();
            }
            const message = voice.updateSpeaking(userId, speakingFlags !== 0, channelId);
            if (!message) return;
            if (publishedSession !== voice.session || publishedRevision !== voice.revision || syncing || connectionEpoch === null) pushState();
            else sendControl(message);
        },
        USER_UPDATE() {
            pushState();
        },
        GUILD_MEMBER_UPDATE() {
            pushState();
        },
    },

    start() {
        (window as any).VencOverlayDebug = debugVideos;
        running = true;
        lifecycle++;
        connectionEpoch = null;
        publishedSession = null;
        voice.reset();
        webcam.clear();
        publishedCameraSettingsRevision = null;
        publishedRevision = null;
        voiceGuildId = null;
        stateDirty = true;
        Native.start(readSettings(settings.store).port).catch(reportIpcFailure);
        refreshVoice();
        pushState();
        // Detect new connections promptly; retain the two-second state heartbeat.
        heartbeat = setInterval(synchroniseState, 250);
        restartFrameLoop();
    },

    stop() {
        running = false;
        lifecycle++;
        connectionEpoch = null;
        publishedSession = null;
        delete (window as any).VencOverlayDebug;
        if (heartbeat) clearInterval(heartbeat);
        if (frameLoop) clearInterval(frameLoop);
        if (pushTimer) clearTimeout(pushTimer);
        heartbeat = frameLoop = pushTimer = null;
        voice.reset();
        webcam.clear();
        publishedCameraSettingsRevision = null;
        publishedRevision = null;
        voiceGuildId = null;
        Native.disconnect().catch(reportIpcFailure);
    },
});
