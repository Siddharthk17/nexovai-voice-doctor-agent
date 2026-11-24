import { api } from "./api";

export type CallState = "idle" | "dialling" | "ringing" | "connected" | "ended" | "failed";

/** One browser call: mic in, agent audio out, signalling over /api/call/offer. */
export class VoiceCall {
  private pc: RTCPeerConnection | null = null;
  private local: MediaStream | null = null;
  private remoteAudio: HTMLAudioElement | null = null;
  analyser: AnalyserNode | null = null;
  onstate: (s: CallState, detail?: string) => void = () => {};
  pcId: string | null = null;

  async start() {
    this.onstate("dialling");
    const micTimeout = new Promise<never>((_, reject) =>
      window.setTimeout(() => reject(new Error("microphone timed out, check permission")), 10000),
    );
    try {
      this.local = await Promise.race([
        navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true },
        }),
        micTimeout,
      ]);
      this.pc = new RTCPeerConnection();
      this.local.getTracks().forEach((t) => this.pc!.addTrack(t, this.local!));

      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(this.local);
      this.analyser = ctx.createAnalyser();
      this.analyser.fftSize = 256;
      src.connect(this.analyser);

      this.pc.ontrack = (ev) => {
        this.remoteAudio = new Audio();
        this.remoteAudio.srcObject = ev.streams[0];
        void this.remoteAudio.play().catch(() => {});
        this.onstate("connected");
      };
      this.pc.onconnectionstatechange = () => {
        const st = this.pc?.connectionState;
        if (st === "failed" || st === "disconnected") this.hangup("failed");
      };

      const offer = await this.pc.createOffer({ offerToReceiveAudio: true });
      await this.pc.setLocalDescription(offer);
      this.onstate("ringing");
      const answer = await api.offer(offer.sdp ?? "", offer.type, this.pcId);
      if (!answer.ok || !answer.sdp) {
        this.hangup("failed", answer.error ?? "voice unavailable, use text chat");
        return;
      }
      this.pcId = answer.pc_id ?? null;
      await this.pc.setRemoteDescription({ type: "answer", sdp: answer.sdp });
    } catch (err) {
      this.hangup("failed", err instanceof Error ? err.message : "microphone blocked");
    }
  }

  setMuted(muted: boolean) {
    this.local?.getAudioTracks().forEach((t) => {
      t.enabled = !muted;
    });
  }

  hangup(as: CallState = "ended", detail?: string) {
    try {
      this.local?.getTracks().forEach((t) => t.stop());
      this.remoteAudio?.pause();
      this.pc?.close();
    } finally {
      this.pc = null;
      this.local = null;
      this.onstate(as, detail);
    }
  }
}
