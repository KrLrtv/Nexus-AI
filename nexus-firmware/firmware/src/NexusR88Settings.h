#pragma once
// NEXUS_R88_UNIFIED — configure device-side behavior; no UI redesign.
// Flip this to 1 if physical side gestures scroll in the wrong direction.
#define NEXUS_R88_INVERT_FLICK 0
// Angular velocity in degrees/s; high threshold prevents accidental scroll.
#define NEXUS_R88_FLICK_THRESHOLD 230.0f
#define NEXUS_R88_FLICK_COOLDOWN_MS 850u
#define NEXUS_R88_FLICK_REARM_MS 180u
// Battery display is only added to the existing *open* settings menu.
#define NEXUS_R88_BATTERY_REFRESH_MS 30000u

// NEXUS_R881_FLICK_FIX: moderate sensitivity; adjust only if needed.
// Typical fast sideways wrist turn exceeds ~100-150 degrees/second.
#define NEXUS_R881_GESTURE_THRESHOLD 110.0f
// 3 = try Z, Y, then X because older NexusUI versions differ in axis mapping.
// Force one axis if needed: 0 = Z; 1 = Y; 2 = X.
#define NEXUS_R881_UI_AXIS 3
// Synthetic angular-velocity pulse sent to the existing UI handler.
#define NEXUS_R881_UI_PULSE 650.0f
#define NEXUS_R881_AXIS_PROBE_MS 110u
#define NEXUS_R881_COOLDOWN_MS 600u
#define NEXUS_R881_QUIET_MS 220u

// NEXUS_R882_DIRECT_SCROLL: real IMU flick -> direct menu/text scroll.
// 3=automatic dominant axis, 0=X, 1=Y, 2=Z.
#define NEXUS_R882_AXIS 3
// degrees per second; lower if gestures are too difficult to trigger.
#define NEXUS_R882_THRESHOLD 70.0f
#define NEXUS_R882_STILL_DPS 25.0f
#define NEXUS_R882_STILL_MS 260u
#define NEXUS_R882_COOLDOWN_MS 650u
#define NEXUS_R882_RETURN_GUARD_MS 1350u

// NEXUS_R883_FORWARD_TILT: forward/backward tilt instead of sideways flick.
// sin(tilt_angle): 0.259 ~ 15 deg, 0.342 ~ 20 deg.
#define NEXUS_R883_TRIGGER_SIN 0.259f
#define NEXUS_R883_REARM_SIN 0.100f
#define NEXUS_R883_HOLD_MS 100u
#define NEXUS_R883_REARM_MS 250u
#define NEXUS_R883_CALIBRATION_MS 700u
#define NEXUS_R883_COOLDOWN_MS 500u
#define NEXUS_R883_QUIET_DPS 35.0f

#define NEXUS_R884_TEXT_LINES 5

// NEXUS_R885_IMU_DIAGNOSTIC — axis-independent forward/back tilting.
// 0.25 of normalized gravity chord is about 14 degrees.
#define NEXUS_R885_TRIGGER_CHORD 0.25f
#define NEXUS_R885_REARM_CHORD 0.11f
#define NEXUS_R885_CALIBRATION_MS 700u
#define NEXUS_R885_HOLD_MS 100u
#define NEXUS_R885_COOLDOWN_MS 500u
#define NEXUS_R885_REARM_MS 220u
// Temporary bottom-of-screen diagnostics; set to 0 once gestures work.
#define NEXUS_R885_DEBUG 1

// NEXUS_R886_ANY_POSITION: rotate relative to the BODY (screen), never gravity.
// 3=auto: find dominant X/Y/Z for each new deliberate tilt; 0/1/2=fix axis.
#define NEXUS_R886_AXIS 3
#define NEXUS_R886_START_DPS 65.0f
#define NEXUS_R886_TRIGGER_DEG 12.0f
#define NEXUS_R886_REARM_DEG 6.0f
#define NEXUS_R886_COOLDOWN_MS 500u
#define NEXUS_R886_DEBUG 1
