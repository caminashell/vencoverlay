/*
 * Vencord, a Discord client mod
 * Copyright (c) 2026 akiloyutu (Akii-G) and VencOverlay contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

import { captureDimensions, MAX_FRAME_BYTES } from "./protocol";

const DISCOVERY_INTERVAL_MS = 500;
const JPEG_QUALITY = 0.72;

/** Only explicit participant camera tiles are eligible. Never guess from leftovers. */
export function cameraUserId(video: HTMLVideoElement, candidates: ReadonlySet<string>): string | null {
    const id = video.closest("[data-selenium-video-tile]")?.getAttribute("data-selenium-video-tile");
    return id && !id.includes(":") && candidates.has(id) ? id : null;
}

function playable(video: HTMLVideoElement): boolean {
    return video.isConnected && !video.paused && !video.ended && video.readyState >= 2
        && video.videoWidth > 0 && video.videoHeight > 0;
}

interface Camera {
    video: HTMLVideoElement;
    source: HTMLVideoElement["srcObject"];
    url: string;
}

export function discoverCameras(videos: Iterable<HTMLVideoElement>, candidates: ReadonlySet<string>): Map<string, HTMLVideoElement> {
    const result = new Map<string, HTMLVideoElement>();
    for (const video of videos) {
        if (!playable(video)) continue;
        const id = cameraUserId(video, candidates);
        if (id === null) continue;
        const previous = result.get(id);
        if (!previous || video.videoWidth > previous.videoWidth) result.set(id, video);
    }
    return result;
}

interface CaptureRequest {
    scope: string;
    candidates: ReadonlySet<string>;
    tileWidth: number;
    isCurrent(): boolean;
    canSend(): Promise<boolean>;
    send(id: string, bytes: Uint8Array, width: number, height: number): Promise<boolean>;
}

interface CaptureEnvironment {
    videos(): Iterable<HTMLVideoElement>;
    createCanvas(width: number, height: number): OffscreenCanvas;
    now(): number;
    onError(error: unknown): void;
}

/** A single in-flight capture cycle and reusable canvas, even across stop/start. */
export class WebcamCapture {
    private busy = false;
    private generation = 0;
    private scope: string | null = null;
    private cameras = new Map<string, Camera>();
    private nextDiscovery = 0;
    private canvas: OffscreenCanvas | null = null;
    private context: OffscreenCanvasRenderingContext2D | null = null;
    private cursor = 0;

    constructor(private readonly environment: CaptureEnvironment) {}

    clear() {
        this.generation++;
        this.scope = null;
        this.cameras.clear();
        this.nextDiscovery = 0;
        this.canvas = null;
        this.context = null;
        this.cursor = 0;
        // Do not release the busy guard: an outstanding encoder still owns its canvas.
    }

    private validCamera(id: string, camera: Camera, candidates: ReadonlySet<string>): boolean {
        const { video } = camera;
        return playable(video) && cameraUserId(video, candidates) === id
            && video.srcObject === camera.source && video.currentSrc === camera.url;
    }

    private discover(candidates: ReadonlySet<string>) {
        // Revalidate cached elements before reuse, including replaced streams or tiles.
        for (const [id, camera] of this.cameras) {
            if (!this.validCamera(id, camera, candidates)) this.cameras.delete(id);
        }
        const now = this.environment.now();
        if (now < this.nextDiscovery) return;
        this.nextDiscovery = now + DISCOVERY_INTERVAL_MS;
        this.cameras.clear();
        for (const [id, video] of discoverCameras(this.environment.videos(), candidates)) {
            this.cameras.set(id, { video, source: video.srcObject, url: video.currentSrc });
        }
    }

    async capture(request: CaptureRequest): Promise<void> {
        if (this.busy) return;
        if (!request.candidates.size || !request.isCurrent()) {
            this.clear();
            return;
        }
        if (this.scope !== request.scope) {
            this.clear();
            this.scope = request.scope;
        }
        this.busy = true;
        const { generation } = this;
        const current = () => generation === this.generation && request.isCurrent();
        try {
            if (!await request.canSend() || !current()) return;
            this.discover(request.candidates);
            const entries = [...this.cameras];
            if (!entries.length) {
                this.canvas = null;
                this.context = null;
                return;
            }
            // Rotate starting point so congestion does not consistently favour the first camera.
            const start = this.cursor % entries.length;
            for (let offset = 0; offset < entries.length; offset++) {
                const index = (start + offset) % entries.length;
                const [id, camera] = entries[index];
                if (!current()) return;
                if (offset && (!await request.canSend() || !current())) return;
                this.cursor = (index + 1) % entries.length;
                if (!this.validCamera(id, camera, request.candidates)) continue;
                try {
                    const dimensions = captureDimensions(camera.video.videoWidth, camera.video.videoHeight, request.tileWidth);
                    if (!dimensions) continue;
                    const { w, h } = dimensions;
                    this.canvas ??= this.environment.createCanvas(w, h);
                    const { canvas } = this;
                    if (canvas.width !== w || canvas.height !== h) {
                        canvas.width = w;
                        canvas.height = h;
                        this.context = null;
                    }
                    this.context ??= canvas.getContext("2d");
                    if (!this.context) continue;
                    this.context.drawImage(camera.video, 0, 0, w, h);
                    const blob = await canvas.convertToBlob({ type: "image/jpeg", quality: JPEG_QUALITY });
                    if (!current()) return;
                    if (!this.validCamera(id, camera, request.candidates) || !blob.size || blob.size > MAX_FRAME_BYTES) continue;
                    const bytes = new Uint8Array(await blob.arrayBuffer());
                    if (!current()) return;
                    if (!this.validCamera(id, camera, request.candidates) || !bytes.byteLength || bytes.byteLength > MAX_FRAME_BYTES) continue;
                    if (!await request.send(id, bytes, w, h)) return;
                } catch (error) {
                    this.environment.onError(error);
                }
            }
        } finally {
            this.busy = false;
        }
    }
}
