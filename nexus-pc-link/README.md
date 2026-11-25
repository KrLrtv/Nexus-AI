# NEXUS PC Link — 0.3.0-preview

Independent implementation: M5StickC Plus2 -> Bluetooth Classic SPP -> native Android bridge -> certificate-pinned HTTPS -> local Ollama agent on Windows PC.

The existing main/Termux implementation is not modified. The parts directory contains a split XZ archive of UTF-8 source files for reproducible transport. To inspect/build: `python nexus-pc-link/unpack.py work` (Python 3.12+). The extractor verifies SHA256 before using tar's data filter. All source is then readable in work/pc, work/android, work/firmware. This is not an executable archive.

CI compiles the Android debug APK, ESP32 firmware and Windows executable separately. Python tests use real SQLite and HTTP/TLS but mocked model and speech responses; 74 tests passed locally. Java/C++ wire encoders match the Python reference. No actual phone, microphone, ESP32, SMS delivery or local-model quality test has been performed. A successful build is not evidence of hardware end-to-end reliability.

Capabilities: local notes/memory, bounded public HTTPS reading/search, multi-step Ollama tool calls, persistent declarative recipes with model-authored self-tests, Android SMS and opt-in notification replies with explicit physical confirmation. Notification replies are not a full Telegram client. No arbitrary host code execution, fine-tuning, universal self-programming or unattended firmware replacement is claimed.

No secrets, audio, user conversations or model weights are included. Debug APK is a test build, not a production-signed release. Binaries are only produced by successful build jobs and are not auto-installed.
