# cluefinder-puzzles

Online puzzle feed for the **Clue Finder / کاشف سرنخ** Android game.

- `feed/puzzles.json` — the file the app downloads: encrypted puzzles, signed. Nobody can read future puzzles from it.
- `state/` — pipeline state (encrypted puzzles + review status). Never edit by hand.
- `pipeline/` — generation (free Google Gemini API, Groq as backup), checks, Telegram review bot.

## How it works
1. **Daily** (`Generate puzzles`): new puzzles are written by AI (Gemini; Groq if Gemini fails), checked (format, answer leaks, duplicates,
   banned-word list, and a second AI acting as a strict censor) and sent to the review bot.
2. **Every 10 minutes** (`Review bot`): your ✅ / ❌ taps are applied. A rejected puzzle is replaced automatically.
   Puzzles you do not review are approved after 7 days (or 3 days before their date, whichever is first).
3. Approved puzzles are published in `feed/puzzles.json`; phones download them and keep a year ahead offline.

Bot commands: tap ✅ / ❌ under a puzzle, or send `رد 123` / `تایید 123`, or `وضعیت` for a summary.

## Secrets (Settings → Secrets and variables → Actions)
`REVIEW_BOT_TOKEN`, `REVIEW_CHAT_ID`, `PUZZLE_MASTER_KEY`, `PUZZLE_SIGNING_KEY`, `GEMINI_API_KEY` (and optionally `GROQ_API_KEY`).
The two PUZZLE keys must match the ones built into the app — never change or lose them.
