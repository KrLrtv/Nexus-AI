# NEXUS AI 1.0 — completion pass

## Implemented in this source package
- Privacy-first local notification prioritization (`notification_engine.py`): rule-based high/normal/low with no notification body sent to cloud. Needs Termux:API permissions.
- Bounded follow-up voice session (`session.py`), started/stopped by B button. Each subsequent recording **requires A**; no continuous unattended audio capture. This is not full duplex streaming.
- Automated SQLite snapshot backups (six-hour interval while process runs), integrity checking, manual stop-and-restore tool (`nexus_maintenance.py`); no unsafe automatic database replacement while running.
- Existing Wi-Fi reconnection, screen dimming, goals, memory, permissions and gestures preserved.

## Still not completed and why
- BLE GATT fallback to Android: ESP32 and Termux do not form a robust BLE bridge without an Android BLE GATT component; this source package does **not** falsely claim it.
- Truly uninterrupted low-latency streaming voice: requires Android foreground-service bridge and M5 incremental audio; existing WAV push-to-talk is retained.
- Reliable background notifications depend on Android restrictions, Termux:API and user permissions.
- Firmware compilation and real-device integration remain untested as requested.

## On Termux
```
cd android_bridge
python -m unittest discover -p 'test_*.py'
python nexus_maintenance.py check
python nexus_maintenance.py backup
# Stop the server before restoring:
python nexus_maintenance.py restore --snapshot /path/to/agent-....sqlite3
```
