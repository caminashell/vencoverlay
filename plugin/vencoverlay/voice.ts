/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 akiloyutu (Akii-G) and VencOverlay contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import { Channel, Participant, Preferences, SpeakingMessage, StateMessage, ToastMessage } from "./protocol";

interface Identity {
    name: string;
    avatar: string | null;
}

interface Member {
    id: string;
    mute: boolean;
    deaf: boolean;
    serverMuted: boolean;
    video: boolean;
    live: boolean;
}

function record(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** One authoritative membership snapshot, independent of Discord's stores. */
export class VoiceTracker {
    private channel: Channel | null = null;
    private members = new Map<string, Member>();
    private speaking = new Set<string>();
    private self: string | null = null;
    private complete = false;
    session = 0;
    revision = 0;

    constructor(private readonly identity: (id: string, guildId: string | null) => Identity) {}

    get channelId() {
        return this.channel?.id ?? null;
    }

    reset() {
        this.channel = null;
        this.members.clear();
        this.speaking.clear();
        this.self = null;
        this.complete = false;
        this.session++;
        this.revision++;
    }

    observe(channel: Channel | null, states: unknown, self: string | null, guildId: string | null): ToastMessage[] {
        if (!channel || !record(states)) {
            if (this.channel !== null) this.reset();
            return [];
        }
        const next = new Map<string, Member>();
        let complete = true;
        for (const value of Object.values(states)) {
            if (!record(value) || typeof value.userId !== "string" || !value.userId || next.has(value.userId)) {
                complete = false;
                continue;
            }
            next.set(value.userId, {
                id: value.userId,
                mute: value.mute === true || value.selfMute === true || value.suppress === true,
                deaf: value.deaf === true || value.selfDeaf === true,
                serverMuted: value.mute === true || value.deaf === true,
                video: value.selfVideo === true,
                live: value.selfStream === true,
            });
        }
        // A selected channel alone is not evidence of active membership. During
        // joins/leaves Discord may update selection and voice stores separately.
        if (self === null || !next.has(self)) {
            if (this.channel !== null) this.reset();
            return [];
        }
        const sameChannel = this.channel?.id === channel.id && this.self === self;
        const changed = !sameChannel || next.size !== this.members.size || [...next.values()].some(member => {
            const previous = this.members.get(member.id);
            return !previous || previous.video !== member.video;
        });
        if (!sameChannel) {
            this.speaking.clear();
            this.session++;
        }
        if (changed) this.revision++;
        // Only announce membership changes between complete snapshots.
        // Initial selection and store recovery are baselines.
        const toasts: ToastMessage[] = [];
        if (sameChannel && this.complete && complete) {
            const add = (id: string, kind: "join" | "leave") => {
                if (id === self) return;
                const { name, avatar } = this.identity(id, guildId);
                toasts.push({ t: "toast", kind, text: `${name} ${kind === "join" ? "joined" : "left"}`, avatar });
            };
            for (const id of next.keys()) if (!this.members.has(id)) add(id, "join");
            for (const id of this.members.keys()) if (!next.has(id)) add(id, "leave");
        }
        for (const id of this.speaking) if (!next.has(id)) this.speaking.delete(id);
        this.channel = channel;
        this.members = next;
        this.self = self;
        this.complete = complete;
        return toasts;
    }

    hasMember(id: string): boolean {
        return this.members.has(id);
    }

    updateSpeaking(id: string, on: boolean, channelId?: string): SpeakingMessage | null {
        if (!this.channel || (channelId !== undefined && channelId !== this.channel.id)
            || !this.members.has(id) || this.speaking.has(id) === on) return null;
        if (on) this.speaking.add(id);
        else this.speaking.delete(id);
        return { t: "speaking", id, on };
    }

    snapshot(config: Preferences, guildId: string | null, showSelf: boolean, showOwnWebcam: boolean): StateMessage {
        const users: Participant[] = [...this.members.values()]
            .filter(member => showSelf || member.id !== this.self)
            .map(member => {
                const me = member.id === this.self;
                return {
                    id: member.id,
                    ...this.identity(member.id, guildId),
                    me,
                    speaking: this.speaking.has(member.id),
                    mute: member.mute,
                    deaf: member.deaf,
                    serverMuted: member.serverMuted,
                    video: member.video && config.showWebcams && (!me || showOwnWebcam),
                    live: member.live,
                };
            })
            .sort((a, b) => Number(b.me) - Number(a.me) || a.name.localeCompare(b.name));
        return { t: "state", channel: this.channel, users, config };
    }

    videoUsers(showSelf: boolean, showOwnWebcam: boolean): Set<string> {
        return new Set([...this.members.values()]
            .filter(member => member.video && (member.id !== this.self || (showSelf && showOwnWebcam)))
            .map(member => member.id));
    }
}
