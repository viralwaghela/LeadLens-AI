# Running the scheduler on Windows Task Scheduler

`scheduler/run_scheduled_checks.py` is a plain Python script — it does not
need the Streamlit app to be running, and it reads/writes the same database
the app uses (see `core/memory.py`: SQLite locally, or Postgres/Supabase
once `DATABASE_URL` is set in `.env`).

This sets it up to run automatically once an hour.

## One-time setup

1. Open **Task Scheduler** (Start menu → search "Task Scheduler").
2. **Action → Create Task…** (not "Create Basic Task" — this gives access
   to the "Start in" field, which the script needs).
3. **General tab:**
   - Name: `LeadLens Scheduler`
   - "Run whether user is logged on or not" — leave unchecked unless you
     specifically want it to run while locked/logged out (it needs your
     user session for the venv either way, so "Run only when logged on"
     is the simpler default).
4. **Triggers tab → New…**
   - Begin the task: `On a schedule`
   - Settings: `Daily`, recur every 1 day
   - Advanced settings: check `Repeat task every` → `1 hour`, for a
     duration of `Indefinitely`
   - Click OK.
5. **Actions tab → New…**
   - Action: `Start a program`
   - Program/script — full path to the project's venv Python, e.g.:
     ```
     C:\Users\Viral Waghela\LeadLens polish\Jarvis Experience\LeadLens_CareOS\.venv\Scripts\python.exe
     ```
   - Add arguments:
     ```
     scheduler\run_scheduled_checks.py
     ```
   - Start in (required — this is what makes relative paths and `.env`
     loading work):
     ```
     C:\Users\Viral Waghela\LeadLens polish\Jarvis Experience\LeadLens_CareOS
     ```
   - Click OK.
6. **Conditions tab:** uncheck "Start the task only if the computer is on
   AC power" if this runs on a laptop that's sometimes on battery.
7. **Settings tab:** check "If the task fails, restart every" → 5 minutes,
   up to 3 attempts — a single transient DB hiccup shouldn't need a full
   hour's wait to retry.
8. Click OK, enter your Windows password if prompted (required for
   "Run whether user is logged on or not"; not required otherwise).

## Verifying it's wired up correctly

Right-click the task → **Run**. Then check:

- Task Scheduler's own **History** tab (or Event Viewer →
  Applications and Services Logs → Microsoft → Windows → TaskScheduler)
  shows the action completed with result `0x0`.
- A new entry appears in the `scheduler_runs` section of memory — from a
  Python shell in the project's venv:
  ```python
  from core.memory import get_memory_section
  get_memory_section("scheduler_runs")[-1]
  ```

## Running it manually (for testing, without waiting for the trigger)

From the project root, with the venv active:

```
python scheduler/run_scheduled_checks.py
```

Exit code `0` means every registered check ran without raising. Exit code
`1` means at least one check failed — the log line for that check (and the
`scheduler_runs` entry's `failures` field) has the exception detail; every
other check still ran normally.
