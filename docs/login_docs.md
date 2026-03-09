# Sakhi Authentication — Frontend Integration Guide

This document explains the Netflix-style authentication system implemented in the Sakhi backend and how the frontend should interact with it.

## Overview

Sakhi uses a **family account** model:
1. A family signs up once (email + password).
2. The system automatically creates a **Parent Profile**.
3. From the dashboard, parents can create multiple **Child Profiles** (Aarav, Priya, etc.).
4. When the app opens, users see a **Profile Picker Screen** (like Netflix).
5. **Children** can tap their profile to enter instantly (no password).
6. **Parents** must enter the family password to access their profile (dashboard).

---

## The Three-Token System

To make this seamless, the backend uses three different JWTs. **The frontend must store and manage these securely.**

| Token | Lifespan | Stored Where? | Used For |
|---|---|---|---|
| **Account Token** | 30 Days | `localStorage` (or Secure Store) | Identifying the family. Used to fetch profiles for the picker, create profiles, and enter a profile. |
| **Refresh Token** | 90 Days | `localStorage` (or Secure Store) | Silently swapping for a new Account Token when the 30-day one expires. |
| **Profile Token** | 8 Hours | Memory (React State / Context) | Identifying the *active user* (Parent or Child). Used for all core app actions (starting Voice sessions, fetching dashboard data). |

### The Golden Rule of UX
> A parent never has to fully log out for a child to use the app. The **Account Token** lives in the background always. Switching profiles just means acquiring a new **Profile Token** and throwing it away when done.

---

## API Endpoints Reference

All endpoints are prefixed with `/auth`.

### 1. Public Endpoints (No Token Required)

#### `POST /auth/signup`
Creates a family account and the initial Parent Profile.
- **Request:** `{ "email": "...", "password": "...", "family_name": "Sharma Family" }`
- **Response (201):** `{ "account", "parent_profile", "account_token", "refresh_token" }`
- **Action:** Store both tokens. Navigate to Profile Picker.

#### `POST /auth/login`
Authenticates a family.
- **Request:** `{ "email": "...", "password": "..." }`
- **Response (200):** `{ "account", "profiles": [...], "account_token", "refresh_token" }`
- **Action:** Store tokens. Render the `profiles` array on the Picker Screen.

---

### 2. Account Endpoints (Requires `Authorization: Bearer <Account_Token>`)

#### `GET /auth/profiles`
Fetches all profiles to render the Netflix-style picker.
- **Response (200):** `[ { id, type: 'parent', display_name... }, { id, type: 'child'... } ]`

#### `POST /auth/profiles`
Creates a new child profile.
- **Request:** `{ "display_name": "Aarav", "age": 7, "avatar": null }`
- **Response (201):** `{ "id", "type": "child", "display_name": "Aarav", ... }`

#### `POST /auth/profiles/{id}/enter`
The core profile switching endpoint. **This issues the Profile Token.**
- **Request (Child):** `{}` (Empty JSON body is fine, or omit password).
- **Request (Parent):** `{ "password": "family_password_here" }` (Will 401 if missing/wrong).
- **Response (200):** `{ "profile", "profile_token" }`
- **Action:** Keep the `profile_token` in memory. Navigate to the App (Voice UI for child, Dashboard for parent).

#### `POST /auth/logout`
Completely logs the family out of the device.
- **Response (204):** No Content.
- **Action:** Clear all tokens from `localStorage`/Memory. Navigate to Login screen.

---

### 3. Profile Endpoints (Requires `Authorization: Bearer <Profile_Token>`)

#### `POST /api/token` (LiveKit Voice Session)
**Note: This is a breaking change from the MVP.** It now requires the Profile Token.
- **Headers:** `Authorization: Bearer <Profile_Token>`
- **Request Body:** `{}` (The backend reads the child's name/age directly from the token/DB now).
- **Response (200):** `{ "token", "room_name", "livekit_url" }` (Pass these to `<LiveKitRoom>`).
- **Security:** If a Parent profile token tries to call this, it returns 403.

#### `GET /auth/profiles/me`
Gets the currently active profile data (useful on hard refresh).
- **Response (200):** `{ "id", "type", "display_name"... }`

#### `POST /auth/profiles/exit`
Leaves the current profile.
- **Response (204):** No Content.
- **Action:** Delete the `profile_token` from memory. Navigate back to Profile Picker.

---

### 4. Refresh Flow (Requires `Authorization: Bearer <Refresh_Token>`)

#### `POST /auth/refresh`
Silently swaps an expired Account Token for a new one.
- **Response (200):** `{ "account_token" }`
- **Action:** Replace the old account token in `localStorage`.

---

## Recommended Frontend Architecture (React)

You should maintain two distinct layers of state/context:

### 1. `AccountContext` (Persistent)
- Loads `accountToken` and `refreshToken` from `localStorage` on boot.
- Provides functions: `login()`, `signup()`, `logout()`.
- If `accountToken` is present but `profileToken` is not, the app **must** display the Profile Picker.

### 2. `ProfileContext` (Ephemeral)
- Holds the `profileToken` and the current `profile` object in pure React State (memory).
- Provides functions: `enterProfile(id, password)`, `exitProfile()`.
- If `profileToken` is present, the app displays the Child UI or Parent Dashboard (depending on `profile.type`).

### Axios Interceptor Strategy
Set up an Axios (or fetch) interceptor that handles 401s:
1. If a profile route 401s, clear `profileToken` and push to Picker.
2. If an account route 401s, try calling `/auth/refresh` with the refresh token.
3. If refresh succeeds, retry the original request.
4. If refresh fails (or returns 401), clear everything and push to Login.
