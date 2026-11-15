# NEXUS AI 1.0 — final software pass / honest acceptance report

Implemented and locally validated (Python only): persisted memory/goals, auth tools, voice WAV endpoint, multi-turn session state, rule-based notification prioritization, SQLite snapshots and checks, independent service supervisor with crash-loop stop, automatic DB-only recovery from the last valid snapshot while server is offline, staging of trusted signed-off release archive with SHA-256 check and syntax validation.

Not validated/fully implemented: physical M5 microphone + display build, actual Gemini API, Termux:API integration, system background restrictions, complete Bluetooth fallback, automatic recording after voice end, firmware OTA rollback. The BLE Android bridge has a native encrypted GATT server and localhost forwarding; firmware includes a compile-time optional NimBLE client. These parts are unbuilt and not interoperability-tested, so BLE fallback is NOT certified operational.

Voice session is push-to-talk at each turn by design. It is NOT always-on or full-duplex. Termux cannot bypass Android system permissions or foreground-service restrictions.

Next required step is hardware integration/testing, not adding new feature families. See README.md for setup and specific tests.

## BLE experimental wiring (short messages only)
- Build `android_ble_companion` with Android Studio, grant Bluetooth permissions and start its foreground service manually.
- In the companion app, enter the identical device token used by Termux; it is stored in app-private preferences (not hardware-backed encryption).
- Enable `-DNEXUS_ENABLE_BLE` in firmware PlatformIO build flags and compile; bond M5 to phone (Just Works bonding is NOT proof of user identity).
- Only the four allowlisted events go via BLE. Voice data always requires Wi-Fi.
- Not compiled or tested on Android/M5, so treat as integration candidate.
