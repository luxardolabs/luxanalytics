# iOS SDK Change: Add `platform` Field

## Context

LuxAnalytics server v1.0.3 has been refactored with a new schema that promotes frequently-queried metadata fields to dedicated database columns. One of those promoted columns is `platform` — used to distinguish between iOS, Android, and future platforms as we onboard multiple apps.

Currently the server defaults `platform = "ios"` when it's not present in the event metadata. This works, but it's implicit. We want the SDK to explicitly declare its platform.

## What Needs to Change

### File: `Sources/LuxAnalytics/AppAnalyticsContext.swift`

In the `generateContext()` method, add `"platform": "ios"` to the context dictionary.

**Current code** (approximately):
```swift
@MainActor
private func generateContext() -> [String: String] {
    var context: [String: String] = [:]
    context["device_model"] = UIDevice.modelCode()
    context["device_type"] = UIDevice.current.model
    context["system_version"] = UIDevice.current.systemVersion
    context["app_version"] = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
    context["build_number"] = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "unknown"
    context["screen_resolution"] = "\(Int(UIScreen.main.bounds.width))x\(Int(UIScreen.main.bounds.height))"
    context["locale"] = Locale.current.identifier
    context["timezone"] = TimeZone.current.identifier
    context["device_id"] = getOrCreateDeviceID()
    context["is_testflight"] = Self.isTestFlightBuild() ? "true" : "false"
    return context
}
```

**Add this line:**
```swift
context["platform"] = "ios"
```

### Where to Add It

Add it anywhere in the `generateContext()` method alongside the other context fields. Suggested placement — after `is_testflight`:

```swift
context["is_testflight"] = Self.isTestFlightBuild() ? "true" : "false"
context["platform"] = "ios"  // ← NEW: identifies this as the iOS SDK
```

## Why

- The analytics server uses `platform` as a promoted database column for filtering events by platform
- When we onboard a second app (Android, web, etc.), each SDK declares its platform
- The server currently defaults to `"ios"` when missing, but explicit is better than implicit
- This enables future per-platform dashboards, device breakdowns, and comparisons

## Impact

- **Backward compatible** — the server already handles events without `platform` (defaults to `"ios"`)
- **No API changes** — the field goes in the existing `metadata` dictionary
- **No version bump required** — this is an additive, non-breaking change
- **Wire format**: The `platform` key will appear in the event JSON metadata alongside all other context fields

## Example Event After Change

```json
{
  "name": "screen_view",
  "timestamp": "2026-03-28T20:36:30+00:00",
  "user_id": "D4726705-...",
  "session_id": "44B15D4E",
  "metadata": {
    "device_model": "iPhone17,1",
    "device_type": "iPhone",
    "system_version": "26.4",
    "app_version": "1.0.23",
    "build_number": "3",
    "screen_resolution": "402x874",
    "locale": "en_US",
    "timezone": "America/Chicago",
    "device_id": "7a32d2d4...",
    "is_testflight": "true",
    "platform": "ios",           // ← NEW
    "screen": "watchlist",
    "feature": "stations"
  }
}
```

## Server-Side Handling

The server extracts `platform` from metadata on ingest:
```python
platform = metadata.get("platform", "ios")  # defaults to "ios" if missing
```

It's stored as a promoted column on the `events` table and used for:
- Filtering dashboard views by platform
- Device analytics breakdowns
- Future cross-platform comparison

## Testing

After making the change:
1. Run the app in debug mode
2. Check the analytics debug output — events should include `"platform": "ios"` in metadata
3. Verify events appear in the LuxAnalytics dashboard with the `ios` platform badge

## Timeline

This change can be made at any time. The server is already deployed and handling events with or without the `platform` field. There is no rush — but doing it before we onboard the second app keeps things clean.
