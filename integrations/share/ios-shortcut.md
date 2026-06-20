# AgentOS Share — iOS Shortcut (the Share-sheet path)

iOS Safari can't be a Web Share Target, so the way to get **Photos → Share → AgentOS** on an
iPhone is an **iOS Shortcut** that appears in the system Share sheet and POSTs the photo to your
box's share hub (`:8770`). This is the "great" path: long-press any photo → Share → *Share to box*
→ pick a door → done.

There's also a no-build path: the **PWA** (open `https://4090.tail096c29.ts.net:8770/` in Safari →
Share → **Add to Home Screen**). That gives an installable app icon with its own photo/camera
picker and the same four doors. Use the PWA for "open the app and send"; use the Shortcut for
"share straight from Photos / Safari / anywhere."

---

## One-time prep on the box

1. **Make sure the hub is exposed** (tailnet-only):
   ```
   integrations/agentosd-remote.sh up      # serves :8770 (and the others) over tailscale
   ```
2. **Get your X-Share-Key.** The hub generates it on first run. Read it yourself (don't paste it
   anywhere public):
   ```
   cat ~/.config/agentos/share.key
   ```
   You'll paste this one string into the Shortcut below. It's defense-in-depth, not the real
   boundary — **the tailnet is** — but it stops anything on the tailnet that isn't your Shortcut.

---

## Build the Shortcut

Open **Shortcuts** on the iPhone → **+** (new) → rename it e.g. **"Share to box"**.
Add these actions in order:

1. **Text** → paste your X-Share-Key string.
   Then **Set Variable** `key` to that Text. *(Keeps the key in one place.)*

2. **Shortcut Input** is your shared image. To be safe with iPhone HEIC photos, add
   **Convert Image** → *Format: JPEG* (input: Shortcut Input). This also shrinks the payload.
   *(Optional: add **Resize Image** to 2048px on the longest side for faster sends.)*

3. **Base64 Encode** (Scripting) the converted image → **Line Breaks: None**.
   Set Variable `img`.

4. **Choose from Menu** with four items: **Dream · Ask Hermes · Hermes task · Claude**.
   In each branch, add a **Text** action with the matching code and **Set Variable** `dest`:
   - Dream → `lucid`
   - Ask Hermes → `hermes-chat`
   - Hermes task → `hermes-task`
   - Claude → `claude`

5. **Ask for Input** → *Text*, prompt "Caption? (optional)", **Allow empty: on**.
   Set Variable `caption`. *(Skip-friendly — a blank caption is fine.)*

6. **Get Contents of URL**:
   - **URL:** `https://4090.tail096c29.ts.net:8770/share`
   - **Method:** `POST`
   - **Headers:** add `X-Share-Key` = Variable `key`
   - **Request Body:** **JSON**, three fields:
     - `dest` (Text) = Variable `dest`
     - `image_b64` (Text) = Variable `img`
     - `caption` (Text) = Variable `caption`

7. **Get Dictionary Value** `ok` from **Get Contents of URL**, then **If** `ok` *is* `1` (true):
   - **Get Dictionary Value** `message` → **Show Notification** with it. *(Honest acknowledgements:
     "Your box read this photo and is opening a dream from it." / "Hermes read your photo — a message
     can't be unread." / "Saved as a proposal on your box. Nothing runs until you approve it.")*
   - **Otherwise** (the **If**'s else branch): **Get Dictionary Value** `reason`; if it's empty, **Get
     Dictionary Value** `error` instead. **Show Notification** with whichever has a value. *(So a
     refusal — e.g. a real face on the Dream door — or a stale key never shows a blank notification.)*

   > **If the notification says `unauthorized`, your X-Share-Key is stale** (the hub restarted with a
   > new key, or the file changed). Re-read `cat ~/.config/agentos/share.key` on the box and update the
   > **Text** in step 1. *(This is the only `403` you'll see; the §403 note at the bottom says the same.)*

8. *(Optional — watch it develop.)* The response also carries a `receipt` id. To land on the **same
   "your box understood this" receipt the PWA shows** — the slow "develop" of the dream's colour out of
   the dark — add inside the `ok`-true branch: **Get Dictionary Value** `receipt` → **Text**
   `https://4090.tail096c29.ts.net:8770/r/` immediately followed by that value → **Open URLs**.
   Leave this out for the calm, silent-fire feel (the step-7 notification is already a complete, honest
   acknowledgement); add it when you want the visible payoff. *(The receipt self-expires after a short
   while — it's a moment, not an archive; there's deliberately no "save" button.)*

9. **Shortcut settings (ⓘ)** → **Show in Share Sheet: ON**. Under **Share Sheet Types**, accept
   **Images** (and Photos). Optionally turn off **Show When Run** / the success chime for the calm,
   silent-fire feel the design intends.

Now: **Photos → pick a photo → Share → Share to box → choose a door.** Done.

---

## What each door does (so the menu is honest)

| Door | What happens | Reversible? |
|------|--------------|-------------|
| **Dream** | Starts a Lucid dream from the photo (runs the B2 likeness safety gate on the box). | Yes — delete the dream. |
| **Ask Hermes** | Sends the photo + caption to Hermes as a chat message. | **No** — a read message; the receipt says so. |
| **Hermes task** | *Phase 2 — not enabled yet.* Returns an honest "not yet," never a fake success. | (n/a) |
| **Claude** | Saves an **inert proposal** on the box. Nothing runs until you approve it on the desktop. | Yes — it never executed. |

Notes:
- **Tailscale must be connected** on the iPhone (it already is on your setup). The `*.ts.net`
  certificate is real, so there's no security warning.
- The photo's **EXIF/GPS is stripped** on the box before anything sees it.
- A real person's face will be **refused by the Dream door** (the B2 gate) unless consented — by
  design.
- If you ever get a **403**, your `X-Share-Key` is stale (the hub restarted with a new key, or the
  file changed). Re-read `~/.config/agentos/share.key` and update step 1.
