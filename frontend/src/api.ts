export type Slot = {
  slot_id: string;
  doctor: string;
  speciality: string;
  starts_at: string;
};

export type Doctor = {
  id: string;
  name: string;
  speciality: string;
  room: string;
  open_slots: Slot[];
};

export type Appointment = {
  id: string;
  starts_at: string;
  patient_phone: string;
  patient_name: string;
  status: string;
  doctor: string;
  speciality: string;
  room: string;
};

export type DashEvent =
  | { type: "transcript"; role: string; text: string }
  | { type: "appointments_changed"; highlight?: string }
  | { type: "booking_confirmed"; appointment: Record<string, string> }
  | { type: "caller_known"; phone: string }
  | { type: "call_started"; pc_id: string }
  | { type: string; [k: string]: unknown };

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${path} failed with ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  health: () => json<{ voice_ready: boolean; missing: string[] }>("/api/health"),
  doctors: () => json<{ doctors: Doctor[] }>("/api/doctors"),
  appointments: () =>
    json<{ appointments: Appointment[] }>("/api/appointments?include_cancelled=true"),
  book: (phone: string, slot_id: string, patient_name = "") =>
    json<Record<string, unknown>>("/api/book", {
      method: "POST",
      body: JSON.stringify({ phone, slot_id, patient_name }),
    }),
  cancel: (phone: string, appointment_id = "") =>
    json<Record<string, unknown>>("/api/cancel", {
      method: "POST",
      body: JSON.stringify({ phone, appointment_id }),
    }),
  chat: (session_id: string, message: string, slot_id = "") =>
    json<{ reply: string; events: DashEvent[]; slots?: Slot[]; choices?: Appointment[]; phone?: string }>(
      "/api/chat",
      { method: "POST", body: JSON.stringify({ session_id, message, slot_id }) },
    ),
  offer: (sdp: string, type: string, pc_id: string | null) =>
    json<{ ok: boolean; sdp?: string; type?: string; pc_id?: string; error?: string }>(
      "/api/call/offer",
      { method: "POST", body: JSON.stringify({ sdp, type, pc_id }) },
    ),
};

export function socketUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/dashboard`;
}
