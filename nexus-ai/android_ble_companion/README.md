# NEXUS BLE GATT companion — source candidate

An Android Studio Gradle project with foreground GATT server, BLE bonded-device restriction, encrypted short-command characteristic, user-entered Termux bridge token, and localhost HTTP event forwarding.

**Still requires firmware client and on-device pairing/integration testing.** No compiled APK or verified BLE connection is included. This is not a working fallback yet.

Native service needs manual startup (or user-enabled autostart) due to Android background/foreground restrictions.

Only short messages `shake`, `stop`, `session_start`, `session_stop` travel via BLE; speech uses Wi-Fi.
