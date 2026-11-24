# NexovAI Care — voice front desk

A caller phones the clinic from the browser. An AI agent answers, checks
doctor availability, books or cancels appointments, and the dashboard shows
every step live. Caller identity is the phone number spoken on the call.

## Stack

| Part | Choice |
|---|---|
| Voice pipeline | Pipecat 1.17 (Python 3.11) |
| STT | ElevenLabs `scribe_v2` |
| TTS | ElevenLabs `eleven_flash_v2_5` |
| LLM | Google Gemini `gemini-3-flash-preview` |
| Turn-taking | Silero VAD |
| Browser media | WebRTC via Pipecat SmallWebRTC transport |
| Server | FastAPI, SQLite |
| Dashboard | React + Vite, plain CSS, no UI kit |

## Run it

Needs Python 3.11 (3.14+ has no onnxruntime wheels, which Silero requires).

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp .env.example .env   # fill in ELEVENLABS_API_KEY and GEMINI_API_KEY
.venv/bin/python -m uvicorn backend.app:app --port 7860

cd frontend && npm install && npm run dev   # http://localhost:5173
```

One server is enough in production: `npm run build` in `frontend/`,
then the FastAPI app serves `frontend/dist` itself on 7860.

Without keys the voice button reports what is missing and the text box in
the call panel still books through the same tools and database.

## Notes for reviewers

- Caller identity is the spoken phone number only, per the brief. That is
  the assignment's rule, not a production auth story: anyone who knows a
  number could cancel its bookings. A real deployment would add OTP.
- The dashboard masks phone digits (`••••••3210`). The full number stays
  in SQLite because the voice tools match on it.
- The voice agent is instructed to read the number back and wait for a yes
  before booking or cancelling. That rule lives in the agent prompt.

## How a call works

1. The browser posts an SDP offer to `POST /api/call/offer`.
2. The server answers and starts one Pipecat pipeline per call:
   WebRTC in → ElevenLabs STT → Gemini → ElevenLabs TTS → WebRTC out.
3. Gemini sees three tools: `check_doctor_availability`,
   `book_appointment`, `cancel_appointment`. The prompt tells it to ask for
   the caller's number first and read it back before booking or cancelling.
4. Tool results hit the same SQLite rows the dashboard reads. Transcript
   lines and booking events go out over `/ws/dashboard`, so the day book
   highlights the moment a call changes it.

## Layout

```
backend/app.py        REST, dashboard socket, WebRTC signalling
backend/pipeline.py   Pipecat pipeline (STT → LLM → TTS, VAD, tools)
backend/tools.py      availability / booking / cancellation
backend/store.py      SQLite schema, seed doctors and slots
backend/chat.py       text fallback sharing the same tools
backend/config.py     environment settings, no secrets in code
backend/agent_prompt.py  front-desk voice prompt
backend/tests/        pytest suite for the booking rules
frontend/src/         ledger panels, phone dock, waveform, sounds
```

## Endpoints

| Method | Path | What |
|---|---|---|
| GET | `/api/health` | voice readiness, counts |
| GET | `/api/doctors` | doctors with upcoming open slots |
| GET | `/api/appointments` | day book, phones masked |
| POST | `/api/book` | book a slot: `phone`, `slot_id`, `patient_name` |
| POST | `/api/cancel` | cancel: `phone`, optional `appointment_id` |
| POST | `/api/chat` | text fallback sharing the same tools |
| POST | `/api/call/offer` | WebRTC SDP offer → answer, starts a call |
| WS | `/ws/dashboard` | transcript, booking and call events |

## Check

```bash
.venv/bin/python -m pytest backend/tests -q
cd frontend && npm run build
```
