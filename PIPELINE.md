# How the pipeline works

The browser captures the microphone and sends it over WebRTC to a FastAPI
server. Per call, the server builds one Pipecat pipeline: incoming audio
goes to ElevenLabs for speech-to-text, the text goes to Gemini with three
function tools (check availability, book, cancel), and Gemini's reply goes
to ElevenLabs for text-to-speech and back to the caller's speaker. Silero
VAD marks who is speaking so both sides can interrupt cleanly.

The agent asks for the caller's phone number first and reads it back
before it books or cancels anything. Tool calls run against a small
SQLite database of doctors, slots and appointments, and every transcript
line plus every booking change is pushed over a websocket to the
dashboard, which is how the day book updates live during a call.
