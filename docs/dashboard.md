# Dashboard API Context for Frontend

This document explains how to integrate the Sakhi Parent Dashboard into the React frontend.

## Overview

The dashboard data pipeline tracks **zero** data during the active conversation (to avoid LLM costs). Instead, it relies on two mechanisms:
1. **Real-time Emotions**: As the child talks, their prosody (detected by Hume via the `emotion_detector` process) is saved as "snapshots" to the database every 2-3 seconds.
2. **Session-End Summarization**: When the voice session ends (child disconnects), the `sakhi` voice agent extracts the in-memory chat transcript and triggers a single, cheap LLM call. This LLM reads the transcript + emotion timeline and extracts the topics, mood summary, and any concerning alerts.

Because this happens asynchronously when the session closes, the dashboard data will reflect sessions generally just a few seconds after the child hangs up.

## Authentication

All dashboard endpoints require a **Parent Profile Token**.
- You must send this token in the `Authorization: Bearer <TOKEN>` header.
- The endpoints expect a `profile_id` query parameter indicating **which child** the parent is viewing.
- If `profile_id` is omitted, it attempts to fetch data for the profile associated with the token (which should be the parent, but parents typically don't have voice sessions). Always pass `?profile_id=<CHILD_PROFILE_ID>`.

## The `Overview` Endpoint

For the main dashboard view, you should use the unified overview endpoint rather than making 5 separate API calls.

**Endpoint**: `GET /api/dashboard/overview?profile_id={child_profile_id}`

### Response Structure

```json
{
  "time_spent": {
    "total_minutes": 45.2,
    "daily": [
      {
        "date": "2026-03-08",
        "minutes": 15.0,
        "sessions": 1
      },
      ...
    ]
  },
  "mood": {
    "summaries": [
      {
        "date": "2026-03-09",
        "mood": "Mostly happy and curious, with brief frustration during math problems"
      }
    ],
    "emotion_distribution": [
      {
        "emotion": "Joy",
        "count": 142
      },
      {
        "emotion": "Interest",
        "count": 89
      }
    ]
  },
  "topics": {
    "topics": [
      {
        "name": "photosynthesis",
        "count": 3
      },
      {
        "name": "fractions",
        "count": 2
      }
    ],
    "total_unique": 12
  },
  "streak": {
    "current_streak": 3,
    "longest_streak": 7
  },
  "alerts": {
    "alerts": [
      {
        "id": "uuid-string",
        "type": "content",
        "severity": "warning",    // "info", "warning", or "critical"
        "title": "Sakhi noticed something",
        "description": "Child expressed anxiety about an upcoming math test.",
        "recorded_at": "2026-03-09T14:30:00Z",
        "dismissed": false
      }
    ]
  }
}
```

## Individual Endpoints

If you prefer to lazy-load sections or add detailed views later, you can hit the individual endpoints natively. All take `?profile_id={child_id}` and most accept an optional `&days=7` parameter (except streak).

- `GET /api/dashboard/time-spent?profile_id=...&days=7`
- `GET /api/dashboard/mood?profile_id=...&days=7`
- `GET /api/dashboard/topics?profile_id=...&days=7`
- `GET /api/dashboard/streak?profile_id=...`
- `GET /api/dashboard/alerts?profile_id=...&limit=20`

## Implementation Notes for Frontend Devs

1. **Streak Calculation**: The streak assumes UTC time tracking. It checks backward day-by-day. If the child hasn't logged in "today", but had a session "yesterday", the streak is maintained.
2. **Alert Severity**: UI should map the severity levels. E.g., `info` (blue dot), `warning` (yellow triangle), `critical` (red pulsing icon).
3. **Empty States**: Remember to handle empty arrays cleanly for new child accounts:
   - Topic count: `0`
   - Streak: `0`
   - Alerts: `[]`
   - `total_minutes`: `0.0`
