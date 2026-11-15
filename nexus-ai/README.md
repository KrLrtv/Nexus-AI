# NEXUS AI 1.0 — Developer Autonomy (source prototype)

A **developer prototype**, built on NEXUS AI 0.8, for **M5StickC PLUS2 + Android Termux**. No Windows needed. This version **has not been built/flashed/tested on hardware or integrated with a real Gemini key**. It is not a signed Android APK. Carefully distinguish prepared source from working end-to-end features.

## Architecture

```
M5StickC PLUS2 (screen + IMU + microphone, Wi-Fi 2.4GHz)
  |  HTTP with shared device token, private local network only
Android Termux / Flask bridge / SQLite (persistent memory, tools, approvals)
  |  outbound HTTPS
Gemini API (text + audio transcription + bounded tool planner)
```

**Capabilities and implementation status**

| Requested | Implemented in source | Limits |
| --- | --- | --- |
| Graph memory/context | `core10.py` nodes and labeled edges, searchable SQLite; Gemini tools `graph_add`, `graph_search` | No embeddings or automated entity resolution; graph link creation via Python API |
| Self-development | `evolution10.py` Gemini code **draft** generation, AST checks, hashed staging | **No execution, installation, real sandbox or auto-patching**; AST is NOT a security sandbox |
| Long-term goals | SQLite goals and steps, bounded Gemini tools | Planner does not independently execute all project tasks; progress is user-driven |
| Autonomous research | `research10.py` fixed RSS whitelist, `runtime10.py` six-hour scheduled check (opt-in) | RSS headlines only; not verified full-text fact-checking; network can fail |
| Dialogue session | Device can enable up to 3 consecutive 4-second clips with short B press | NOT always-on streaming; silence threshold needs tuning; cloud quota is consumed |
| Gesture training | Inherited six-feature nearest-example matcher with B hold | Requires real-device calibration; not precise position tracking |
| Smart Android notifications | `notifications10.py` shows app/title metadata via `termux-notification-list` if available | Requires Android notification access; no always-on push listener or AI ranking yet |
| Self-recovery and rollback | Original server modules remain unchanged outside added integrations; runtime restart support via Termux:Boot guidance; code drafts never loaded | Automated transactional rollback NOT implemented; Termux can be killed by Android |
| Wi-Fi/BLE reconnection | Firmware auto-retries Wi-Fi every 15 seconds; firmware heartbeat/poll | BLE transport and phone BLE server NOT implemented (placeholder for later release) |
| Energy saving | Dim TFT after 60 seconds idle, slower drawing while dimmed | Not deep sleep; Wi-Fi use and idle polling still consume battery |
| Developer autonomy | Inherited Gemini tool planner and limited workflow extension recipes | No autonomous unrestricted Python execution |
| Permanent permissions | Voice request `разрешаю навсегда фонарик` etc + one-time code + B hold; saved grants | Grants limited to `torch_on/off`, `notify`, `speak`. Android runtime permission dialogs still require Android UI |

## Files

- `firmware/` M5StickC PLUS2 PlatformIO sketch, based on 0.8 firmware.
- `android_bridge/app.py` Termux HTTP service and localhost dashboard.
- `android_bridge/core10.py` graph, goals, tasks, permissions, events and audit logs.
- `android_bridge/research10.py`, `runtime10.py`: bounded RSS scans and optional background checks.
- `android_bridge/evolution10.py`: draft-only code generator, **never executes generated code**.
- `android_bridge/notifications10.py`: opt-in notification title digest.
- Other 0.8 files are included for earlier voice, IMU gesture and approval integrations.
- `android_bridge/test_v10.py`: isolated database tests.

## Termux setup (later, when ready to debug)

1. Install Termux and optional **Termux:API** companion from compatible distribution, grant only necessary permissions.
2. In Termux: `pkg update && pkg install python termux-api`
3. Extract this archive, run `python -m pip install -r android_bridge/requirements.txt`.
4. Set `GEMINI_API_KEY`, `NEXUS_DEVICE_TOKEN` (long random secret), optionally `GEMINI_MODEL` to the free-tier model **currently available in your own AI Studio quota dashboard**.
5. `cd android_bridge && python app.py`. Open `http://127.0.0.1:8765` on the phone only.
6. Edit Wi-Fi and `BRIDGE_URL` to the actual phone IP and identical `DEVICE_TOKEN` in `firmware/src/main.cpp`.
7. On a computer with PlatformIO, open `firmware/`, build and flash the M5StickC PLUS2. **No hardware build has been carried out.**

To opt into background headline research: `export NEXUS_AUTO_RESEARCH=1` and optional `NEXUS_RESEARCH_TOPIC=technology` before start. Turn it off by stopping the server or leaving the opt-in unset. Background timing is best-effort.

## Voice and gesture controls

- **A**: record a 4-second clip, send over Wi-Fi to Android bridge.
- **B short**: toggle limited dialog session mode. With mode on, after A trigger, device attempts up to two follow-up 4-second clips; empty speech is suppressed with a rough RMS threshold. No hotword.
- **B hold ~1s**: take gyro gesture sample, or **3s** for a pending high-impact approval.
- Shaking: event sent to Android bridge; gesture classifier still needs training examples.
- **Stop**: a voice 'стоп' cancels approvals; remote stop endpoint additionally cancels RSS background checks, not currently a universal interrupt for in-flight cloud requests.

Voice grant example: "Разрешаю навсегда фонарик" → NEXUS generates 4-digit challenge → say "Разрешаю 1234" → hold B 3s. Revocation: "Запрещаю навсегда фонарик" + same process. Permanent grants are **only scoped for previously installed, known functions**. New Android operating system permissions cannot be silently granted.

## Major caveats/security

- Device HTTP traffic is **not encrypted**, and the shared token is carried over LAN. Use an isolated trusted network; do not forward the port. Avoid sensitive requests on untrusted Wi-Fi. A native Android BLE transport or authenticated TLS proxy is future work.
- Existing 0.8 dashboard is localhost-only; only `/status`, `/event`, `/voice`, `/gesture/sample`, and `/v1/events` accept token-bearing requests from LAN.
- No phone-side security isolation for arbitrary generated plugins is claimed. New code is saved to draft files only; it must be reviewed/tested outside the agent before any activation.
- RSS feed content, audio transcriptions, AI output and notification data are untrusted and may be inaccurate. Agent actions use strict allowlists.
- This prototype can consume free Gemini quotas and can be rate-limited. API access, features and available models change.
- Termux does not provide reliable real-time alarms when Android force-stops it; consider Termux:Boot and battery management, understanding battery cost.
- Device display uses transliteration for Cyrillic; buzzer does not offer intelligible spoken responses. Speech TTS is Android-side.

## Unit tests

```sh
cd android_bridge
python -m unittest test_v10 test_authority test_extensions -v
```

This tests database and approval logic only. It does not validate the Android OS integration, PlatformIO compilation or device microphone.

## Future acceptance tests

1. Build firmware with actual Plus2 board/library versions; verify microphone sample amplitude and screen rotation.
2. Confirm Wi-Fi hotspot IP and reachability; reconnect after dropping phone Wi-Fi.
3. Record/recognize Russian 4-second clips; tune silence threshold.
4. Test gesture sample training and battery impact.
5. Check Termux:API permissions, background restrictions and notification digest field names.
6. Test requests approaching and exceeding free-tier Gemini limits; no paid fallback by default.
7. Validate graceful recovery after Termux process termination; backup and restore of SQLite on a separate test install.


## Final software completion pass
- `android_bridge/supervisor.py`: bounded supervised process restarts; crashes 5 times/5 min -> stop. Automatically restores **SQLite only** from latest valid snapshot when corrupt before launching Flask; preserves broken database.
- `android_bridge/recovery_tools.py`: stage an explicitly trusted ZIP release after SHA-256 verification; no AI-generated code is executed. Do not consider a digest alone to be a signature or authenticity proof.
- `scripts/start_termux.sh`: launch the supervisor; `scripts/boot_example.sh`: optional Termux:Boot example. Install Termux:Boot separately and configure OS battery restrictions.
- `android_ble_companion/`: Android native BLE GATT experiment (incomplete, no end-to-end transport). **Do not install as production BLE fallback**.
- `FINAL_STATUS.md`: exact capability status and outstanding integration checks.

Quick start: configure Python dependencies and environment variables per original instructions, then run `bash scripts/start_termux.sh` from project root. This is still a source prototype; device build and hardware tests remain outstanding.


## BLE short-command optional source
Android Studio `android_ble_companion` plus optional `firmware/src/BleFallback.h` are included. Edit `firmware/platformio.ini` build flags to add `-DNEXUS_ENABLE_BLE` after verifying library compatibility; default is disabled. This BLE path is **experimental, unbuilt, and requires pairing and device tests**. Always use Wi-Fi for microphone.

## Limits, especially requested continuous dialogue
The current dialogue remembers context for up to three voice clips, but each clip is initiated with A; this is deliberate microphone privacy. No wake word/always-listening loop and no genuinely seamless full-duplex streaming. Implementing one needs a microphone ring buffer, end-of-speech detection and Android foreground media capture policy handling.
