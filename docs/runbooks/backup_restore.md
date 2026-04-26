# Backup And Restore Runbook

## Goal
Capture runtime state before risky changes and prove that the app can be restored.

## What must be preserved
- `backend/storage/`
- `runtime-logs/`
- environment configuration
- any external billing, auth, or webhook credentials stored outside the repo

## Backup process
1. Stop write-heavy actions if possible.
2. Snapshot the `backend-storage` Docker volume or copy `backend/storage/`.
3. Copy `runtime-logs/`.
4. Update `runtime-logs/backup-status.json` with:
   - `last_attempt_at`
   - `last_success_at`
   - storage location
   - retention window

## Restore drill
1. Restore the saved runtime files into a clean environment.
2. Start the API and frontend.
3. Verify health, workspace visibility, and job backlog state.
4. Record `restore_tested_at` in `runtime-logs/backup-status.json`.

## Failure handling
- If restore fails, do not mark `restore_tested_at`.
- Create an incident note describing the break and next action.
