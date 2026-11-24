import { useEffect, useRef, useState } from "react";
import { VoiceCall, type CallState } from "./call";
import { api } from "./api";
import { playEnd, startRing, stopRing } from "./sounds";
import Waveform from "./Waveform";

export type Line = { role: string; text: string };

type Props = {
  open: boolean;
  dial: number;
  onClose: () => void;
  lines: Line[];
  pushLine: (role: string, text: string) => void;
  confirm: Record<string, string> | null;
  sessionId: string;
};

const STATE_WORDS: Record<CallState, string> = {
  dialling: "Dialling",
  ringing: "Ringing",
  connected: "Connected",
  ended: "Call ended",
  failed: "Could not connect",
};

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true });
  return `${day} · ${time}`;
}

export default function PhoneDock({ open, dial, onClose, lines, pushLine, confirm, sessionId }: Props) {
  const [call] = useState(() => new VoiceCall());
  const [state, setState] = useState<CallState>("ended");
  const [detail, setDetail] = useState("");
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    call.onstate = (s, d) => {
      setState(s);
      setDetail(d ?? "");
      if (s === "ringing") startRing();
      else stopRing();
      if (s === "connected") setSeconds(0);
      if (s === "ended" || s === "failed") playEnd();
    };
    return () => call.hangup();
  }, [call]);

  useEffect(() => {
    if (state !== "connected") return;
    const t = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [state]);

  useEffect(() => {
    if (dial > 0 && open) void call.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dial]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [lines, open ]);

  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");

  const sendChat = async () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    pushLine("caller", text);
    try {
      const out = await api.chat(sessionId, text);
      pushLine("agent", out.reply);
    } catch {
      pushLine("agent", "The desk is unreachable. Check the server and try again.");
    }
  };

  const close = () => {
    call.hangup();
    onClose();
  };

  return (
    <>
      {!open ? null : <div className="scrim" onClick={close} />}
      <section className={open ? "phone-dock" : "phone-dock hidden"} aria-hidden={!open}>
        <div className="phone-state">
          <strong>{STATE_WORDS[state]}</strong>
          {detail ? ` — ${detail}` : ""}
          {state === "connected" ? <span className="phone-timer"> · {mm}:{ss}</span> : null}
          <button className="dock-close" onClick={close} aria-label="Close call panel">Close</button>
        </div>
        <div className="phone-name">Front desk</div>
        <Waveform analyser={() => call.analyser} live={state === "connected"} />

        <div className="transcript" role="log" aria-label="Call transcript">
          {lines.length === 0 ? (
            <div className="bubble system">The conversation appears here, line by line.</div>
          ) : null}
          {lines.map((l, i) =>
            l.role === "system" ? (
              <div className="bubble system" key={i}>{l.text}</div>
            ) : (
              <div className={l.role === "caller" ? "bubble caller" : "bubble agent"} key={i}>
                {l.text}
              </div>
            ),
          )}
          <div ref={endRef} />
        </div>

        {confirm ? (
          <div className="confirm-card">
            <strong>Booked — {confirm.doctor}</strong>
            {fmtWhen(confirm.starts_at)} · {confirm.appointment_id}
          </div>
        ) : null}

        <div className="phone-controls">
          {state === "connected" || state === "ringing" || state === "dialling" ? (
            <>
              <button
                className={muted ? "mute-btn off" : "mute-btn"}
                onClick={() => {
                  setMuted((m) => {
                    call.setMuted(!m);
                    return !m;
                  });
                }}
              >
                {muted ? "Unmute" : "Mute"}
              </button>
              <button className="hangup-btn" onClick={() => call.hangup()}>End call</button>
            </>
          ) : (
            <button className="call-button" style={{ flex: 1 }} onClick={() => void call.start()}>
              Call again
            </button>
          )}
        </div>

        <div className="chat-row">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Type instead of talking"
            onKeyDown={(e) => {
              if (e.key === "Enter") void sendChat();
            }}
          />
          <button onClick={() => void sendChat()}>Send</button>
        </div>
      </section>
    </>
  );
}
