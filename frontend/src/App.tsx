import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, socketUrl, type Appointment, type DashEvent, type Doctor, type Slot } from "./api";
import PhoneDock, { type Line } from "./PhoneDock";

function fmtDay(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true });
}

export default function App() {
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [appointments, setAppointments] = useState<Appointment[]>([]);
  const [voiceReady, setVoiceReady] = useState(true);
  const [missing, setMissing] = useState<string[]>([]);
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [confirm, setConfirm] = useState<Record<string, string> | null>(null);
  const [highlight, setHighlight] = useState("");
  const [callerPhone, setCallerPhone] = useState("");
  const [dial, setDial] = useState(0);
  const [dockSlots, setDockSlots] = useState<Slot[]>([]);
  const [dockChoices, setDockChoices] = useState<Appointment[]>([]);
  const phoneOpenRef = useRef(phoneOpen);
  phoneOpenRef.current = phoneOpen;
  const sessionId = useMemo(() => `desk-${Math.random().toString(36).slice(2, 8)}`, []);
  const retry = useRef(0);

  const refresh = useCallback(async () => {
    const [h, d, a] = await Promise.all([api.health(), api.doctors(), api.appointments()]);
    setVoiceReady(h.voice_ready);
    setMissing(h.missing);
    setDoctors(d.doctors);
    setAppointments(a.appointments);
  }, []);

  const pushLine = useCallback((role: string, text: string) => {
    setLines((prev) => [...prev.slice(-60), { role, text }]);
  }, []);

  useEffect(() => {
    void refresh().catch(() => {});
    let sock: WebSocket | null = null;
    let closed = false;

    const connect = () => {
      sock = new WebSocket(socketUrl());
      sock.onmessage = (ev) => {
        let msg: DashEvent;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "transcript") {
          pushLine(String(msg.role), String(msg.text));
          if (!phoneOpenRef.current) setPhoneOpen(true);
        } else if (msg.type === "appointments_changed") {
          void refresh().catch(() => {});
          if (typeof msg.highlight === "string") {
            setHighlight(msg.highlight);
            window.setTimeout(() => setHighlight(""), 2600);
          }
        } else if (msg.type === "booking_confirmed") {
          const appt = msg.appointment as Record<string, string>;
          setConfirm(appt);
          const when = new Date(appt.starts_at);
          const day = when.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
          const time = when.toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true });
          pushLine("system", `Booked ${appt.doctor} on ${day}, ${time}`);
        } else if (msg.type === "caller_known" && typeof msg.phone === "string") {
          setCallerPhone(msg.phone);
        } else if (msg.type === "call_error") {
          pushLine("system", "The voice model hit an error. Your bookings are safe — try the text box.");
        }
      };
      sock.onclose = () => {
        if (closed) return;
        retry.current += 1;
        window.setTimeout(connect, Math.min(5000, 500 * retry.current));
      };
    };
    connect();
    return () => {
      closed = true;
      sock?.close();
    };
  }, [refresh, pushLine]);

  const today = new Date().toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  const tryCall = () => {
    setPhoneOpen(true);
    setDial((d) => d + 1);
  };

  const bookSlot = async (slotId: string) => {
    if (!callerPhone) {
      setPhoneOpen(true);
      pushLine("agent", "Which phone number should I book under? Say or type all 10 digits.");
      return;
    }
    const out = await api.book(callerPhone, slotId);
    if (out.ok) {
      setConfirm(out as unknown as Record<string, string>);
      setDockSlots([]);
      void refresh();
    } else {
      pushLine("agent", String(out.error ?? "That slot is gone, pick another."));
      setPhoneOpen(true);
    }
  };

  const sendDockChat = async (text: string) => {
    pushLine("caller", text);
    try {
      const out = await api.chat(sessionId, text);
      pushLine("agent", out.reply);
      setDockSlots(out.slots ?? []);
      setDockChoices(out.choices ?? []);
      if (out.phone) setCallerPhone(out.phone);
      if (out.cancelled_id) setConfirm(null);
    } catch {
      pushLine("agent", "The desk is unreachable. Check the server and try again.");
    }
  };

  const cancelChoice = async (appointmentId: string) => {
    if (!callerPhone) return;
    const out = await api.cancel(callerPhone, appointmentId);
    if (out.ok) {
      setConfirm(null);
      setDockChoices([]);
      void refresh();
    } else {
      pushLine("agent", String((out as { error?: string }).error ?? "Could not cancel that one."));
    }
  };

  return (
    <>
      <header className="topbar">
        <div className="wordmark">Nexovai <span>Care</span></div>
        <div className="dateline">{today} · front desk</div>
        <div className="spacer" />
        <div className={voiceReady ? "status live" : "status down"}>
          {voiceReady ? "Voice desk open" : `Text only — missing ${missing.join(", ")}`}
        </div>
        <button className="call-button" onClick={tryCall}>Try call</button>
      </header>

      <main className="layout">
        <section className="ledger" aria-label="Doctors and open slots">
          <h2>Morning list</h2>
          <p className="sub">Who is in, and when. Tap a time to book it.</p>
          {doctors.length === 0 ? <p className="nothing">No doctors on the board.</p> : null}
          {doctors.map((d) => (
            <div className="doctor" key={d.id}>
              <div className="who">
                <span className="name">{d.name}</span>
                <span className="meta">{d.speciality} · {d.room}</span>
              </div>
              <div className="slots">
                {d.open_slots.length === 0 ? (
                  <span className="meta">Fully booked</span>
                ) : (
                  d.open_slots.map((s) => (
                    <button key={s.slot_id} className="slot" onClick={() => void bookSlot(s.slot_id)}>
                      {fmtDay(s.starts_at)} · {fmtTime(s.starts_at)}
                    </button>
                  ))
                )}
              </div>
            </div>
          ))}
        </section>

        <section className="ledger" aria-label="Appointments">
          <h2>Booked</h2>
          <p className="sub">The day book. Updates the moment a call books or cancels.</p>
          {appointments.length === 0 ? (
            <p className="nothing">Nothing booked yet. Calls land here.</p>
          ) : null}
          {appointments.map((a) => (
            <div className={a.id === highlight ? "appt flash" : "appt"} key={a.id}>
              <div className="line">
                <span className="when">{fmtDay(a.starts_at)} · {fmtTime(a.starts_at)}</span>
                <span className={a.status === "booked" ? "stamp booked" : "stamp cancelled"}>
                  {a.status === "booked" ? "Booked" : "Cancelled"}
                </span>
              </div>
              <div className="who">{a.doctor} · {a.room} · {a.patient_phone}</div>
            </div>
          ))}
        </section>
      </main>

      <PhoneDock
        open={phoneOpen}
        dial={dial}
        onClose={() => setPhoneOpen(false)}
        lines={lines}
        confirm={confirm}
        slots={dockSlots}
        onPickSlot={(id) => void bookSlot(id)}
        choices={dockChoices}
        onPickChoice={(id) => void cancelChoice(id)}
        onSendChat={(text) => void sendDockChat(text)}
      />
    </>
  );
}
