# S0.5 — Network spike: phone reaches the laptop

> **Task:** `phases.md` S0.5 · **Owner:** Tanzeel · **Related:** ADR-03, ADR-16, architecture §3, §12.3, MOB-018, H13, H15
> **Goal:** a hello-world FastAPI on Tanzeel's laptop, bound to `0.0.0.0:8000`, is reached from the Android phone over the team's Wi-Fi, with one Windows Firewall rule (TCP 8000, Private profile only).

## Environment (checked 2026-10-09)

| Item | Value |
|---|---|
| Laptop | Tanzeel's laptop, Windows 10 Pro 22H2 (19045) |
| Wi-Fi network | `SPARTACUS`, Windows profile **Private** |
| Laptop LAN IP | `192.168.0.100/24` (interface `Wi-Fi 2`; DHCP, so it can change) |
| Python / uv | Python 3.12.10, uv 0.12.7 |
| Spike server | fastapi 0.143.0, uvicorn 0.54.0 (ephemeral `uv run --with`, nothing installed in the repo) |
| Phone | _fill in: model, Android version_ |

## Steps (run by hand)

1. **Firewall rule.** In PowerShell **run as Administrator**:

   ```powershell
   New-NetFirewallRule -DisplayName "OnCallPilot backend-api TCP 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   Get-NetFirewallRule -DisplayName "OnCallPilot backend-api TCP 8000" | Format-List DisplayName, Enabled, Direction, Action, Profile
   ```

   Only the Private profile. Never Public or Domain (architecture §3, §12.3).

2. **Check the Wi-Fi is still Private** (a normal PowerShell is fine):

   ```powershell
   Get-NetConnectionProfile | Format-Table InterfaceAlias, Name, NetworkCategory
   ```

3. **Start the hello-world API** in a normal terminal from the repository root:

   ```powershell
   uv run --no-project --python 3.12 --with fastapi --with uvicorn python -c "import fastapi, uvicorn; app = fastapi.FastAPI(); app.get('/healthz')(lambda: {'status': 'ok', 'service': 'network-spike'}); uvicorn.run(app, host='0.0.0.0', port=8000)"
   ```

   If Windows shows "Windows Defender Firewall has blocked some features of Python", tick **Private networks only** and click **Allow access**. Do **not** click Cancel: Cancel creates a block rule for `python.exe`, and block rules override the port rule.

4. **Laptop self-test** (second terminal): `curl http://192.168.0.100:8000/healthz` → `{"status":"ok","service":"network-spike"}`.

5. **Phone test.** Connect the phone to the same Wi-Fi (`SPARTACUS`), open Chrome, go to `http://192.168.0.100:8000/healthz`. Expect the same JSON. The uvicorn terminal logs a `GET /healthz 200` line with the phone's IP.

6. **Stop the server** with Ctrl+C. Leave the firewall rule in place; `backend-api` uses the same port later.

If step 5 fails but step 4 works:
- the network may isolate clients (common on campus or guest Wi-Fi, H15): use a hotspot or router the team controls;
- check the firewall rule profile matches the network category from step 2;
- check no `python.exe` block rule exists: `Get-NetFirewallApplicationFilter -Program *python* | Get-NetFirewallRule | Format-Table DisplayName, Action, Profile` (as Administrator).

## Result

| Check | Result | Date | Notes |
|---|---|---|---|
| Firewall rule created (TCP 8000, Private only) | _pending_ | | |
| Laptop self-test via LAN IP | _pending_ | | |
| Phone reaches `/healthz` over Wi-Fi | _pending_ | | Phase 0 definition of done |

## Follow-up for MOB-018 (not part of S0.5)

The phone browser uses Android networking. The app's dio and WebSocket clients use `dart:io`, which may not apply the Android network security config. When the app first makes a request (task A1.5 or later), test on the phone that a debug build **cannot** reach a second cleartext host, and record the result here.
