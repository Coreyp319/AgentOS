# Remote Control setup — driving the packaged wallpaper from agentosd

How agentosd pushes the yielding-resident throttle ladder (`r.ScreenPercentage`,
`t.MaxFPS`, `r.Lumen.*`, …) into the **packaged** UE 5.8 `-game` wallpaper at
runtime, over a loopback HTTP socket.

> Status of facts: items marked **[VERIFIED-SOURCE]** were read directly out of
> this machine's engine source/headers; **[DOC]** from Epic docs; **[CAVEAT]**
> is a known-finicky area to validate on first real cook.

---

## 1. Enable the plugins so they are compiled into the package

The engine ships the plugins already (verified present under
`~/UnrealEngine/Engine/Plugins/VirtualProduction/`):
`RemoteControl`, `RemoteControlWebInterface`, `RemoteControlInterception`, … .

In the editor this is the **"Remote Control API"** plugin (Messaging category)
[DOC]. To guarantee it is **cooked into the package**, enable it in the project
file rather than relying on editor state.

`~/UnrealProjects/AgentOSBlank/AgentOSBlank.uproject` — add to `Plugins`:

```jsonc
"Plugins": [
  { "Name": "PythonScriptPlugin", "Enabled": true },
  { "Name": "RemoteControl",            "Enabled": true },
  { "Name": "RemoteControlWebInterface","Enabled": true }   // optional: the web UI; the HTTP API lives in RemoteControl itself
]
```

**Why this packages cleanly [VERIFIED-SOURCE]:** in
`RemoteControl.uplugin`, the modules we need are `"Type": "Runtime"` —
`RemoteControl`, `WebRemoteControl`, `RemoteControlCommon`,
`RemoteControlInterception`, `RemoteControlLogic`, `RemoteControlProtocol`.
Only `RemoteControlUI` and `RemoteControlProtocolWidgets` are `"Type": "Editor"`
and are dropped from a `-game` cook. So the HTTP control surface (which lives in
the `WebRemoteControl` runtime module) **does** ship in the package.

`WebRemoteControl` is a transitive dependency of `RemoteControl`, so enabling
`RemoteControl` is sufficient for the HTTP API; `RemoteControlWebInterface` only
adds the browser dashboard (not needed for agentosd, which speaks raw HTTP).

---

## 2. Make the packaged runtime start the HTTP server (loopback)

Three knobs, all verified against `RemoteControlSettings.h` /
`WebRemoteControl.cpp` in this install:

| Setting | Default | Source |
|---|---|---|
| `bAutoStartWebServer` | `true` | `RemoteControlSettings.h:303` [VERIFIED-SOURCE] |
| `RemoteControlHttpServerPort` | `30010` | `RemoteControlSettings.h:311` [VERIFIED-SOURCE] |
| `RemoteControlWebSocketServerPort` | `30020` | `RemoteControlSettings.h:319` [VERIFIED-SOURCE] |
| CVar `WebControl.EnableServerOnStartup` | `0` | `WebRemoteControl.cpp:69` [VERIFIED-SOURCE] |
| Console cmd `WebControl.StartServer` | — | `WebRemoteControl.cpp:852` [VERIFIED-SOURCE] |

Boot logic (`WebRemoteControl.cpp:361`) [VERIFIED-SOURCE]:
```cpp
if (GetDefault<URemoteControlSettings>()->bAutoStartWebServer
    || CVarWebControlStartOnBoot.GetValueOnAnyThread() > 0) { /* start http */ }
```
i.e. the server starts on boot if **either** the config flag is true **or** the
`WebControl.EnableServerOnStartup` CVar is set.

### 2a. The config (DefaultEngine.ini) — declares port + binds to loopback

The `URemoteControlSettings` class is `UCLASS(config = RemoteControl)` with
container `Project` [VERIFIED-SOURCE], so its canonical home is
`Config/DefaultRemoteControl.ini` under section
`[/Script/RemoteControlCommon.RemoteControlSettings]`. (It also reads fine from
`DefaultEngine.ini` since both are merged into the runtime `Engine` config.)

```ini
; ---- Config/DefaultRemoteControl.ini ----
[/Script/RemoteControlCommon.RemoteControlSettings]
bAutoStartWebServer=True
bAutoStartWebSocketServer=False        ; agentosd uses plain HTTP, not the websocket
RemoteControlHttpServerPort=30010
```

**Bind the HTTP listener to loopback only.** Important nuance
[VERIFIED-SOURCE]: the HTTP server's *bind address* is NOT a RemoteControl
property — `WebRemoteControl` uses the engine's shared `FHttpServerModule`,
whose bind address comes from the global `[HTTPServer.Listeners]` section of the
Engine config (`HttpServerConfig.cpp:60` reads `DefaultBindAddress` from
`GEngineIni`). So set it in **DefaultEngine.ini**:

```ini
; ---- Config/DefaultEngine.ini ----
[HTTPServer.Listeners]
DefaultBindAddress=127.0.0.1            ; loopback ONLY — never expose RC to the network
```

(The RemoteControl websocket has its *own* bind property
`RemoteControlWebsocketServerBindAddress`, default `0.0.0.0`
[VERIFIED-SOURCE] — another reason to leave the websocket server off and pin the
HTTP listener to `127.0.0.1` as above.)

> Security note: Epic explicitly warns *"Do not attempt to open the hostname and
> port of your Unreal Engine application to the open Internet."* [DOC]. agentosd
> and the wallpaper are co-resident on one box → loopback is exactly right.

### 2b. The belt-and-braces: also pass it on the command line

[CAVEAT] There is a long-standing report that in a **packaged standalone**
(`-game`) build the auto-start does not always fire the way it does in PIE
(forum: "Remote Control on Standalone Game Mode" — none of auto-start, BeginPlay
`WebControl.StartServer`, or `-RCWebControlEnable` worked for that user, while
PIE was fine). Treat config auto-start as necessary-but-verify, and force it on
the launch line so startup does not depend on the config path alone:

```text
-ExecCmds="WebControl.StartServer" -RCWebControlEnable
```

`WebControl.StartServer` is a real registered console command in this engine
[VERIFIED-SOURCE], and `-ExecCmds` runs it just after engine init. This is the
robust trigger; the INI auto-start is the convenience path. **Validate on the
first real cook which of the two actually opens :30010 in the package**, and
keep whichever works (likely: keep both).

---

## 3. The exact curl to push a console command

`r.ScreenPercentage 50` (one rung of the throttle ladder) via the generic
function-call endpoint. Endpoint, port, and JSON shape are from Epic's Remote
Control HTTP reference [DOC]; `ExecuteConsoleCommand` is confirmed present at
`KismetSystemLibrary.h:604` in this install [VERIFIED-SOURCE]:
`static void ExecuteConsoleCommand(const UObject* WorldContextObject, const FString& Command, APlayerController* SpecificPlayer = NULL)`.

```bash
curl -sS -X PUT http://127.0.0.1:30010/remote/object/call \
  -H 'Content-Type: application/json' \
  -d '{
    "objectPath": "/Script/Engine.Default__KismetSystemLibrary",
    "functionName": "ExecuteConsoleCommand",
    "parameters": {
      "WorldContextObject": null,
      "Command": "r.ScreenPercentage 50"
    },
    "generateTransaction": false
  }'
```

- **Endpoint:** `PUT /remote/object/call` [DOC] — generic "call a UFUNCTION on a
  UObject by path".
- **objectPath:** `/Script/Engine.Default__KismetSystemLibrary` — the CDO of the
  Blueprint function library; calling a static UFUNCTION on the CDO is the
  documented way to reach `ExecuteConsoleCommand` without a level actor path.
- **Command:** any cvar/console command. The whole throttle ladder is just
  different `Command` strings:
  `t.MaxFPS 30`, `r.ScreenPercentage 35`, `r.Lumen.DiffuseIndirect.Allow 0`,
  `r.DynamicRes.OperationMode 1`, `r.VSync 1`, …
- **generateTransaction:false** — don't create an undo transaction for a cvar
  poke (cheaper; avoids editor-transaction semantics in a runtime build).

agentosd's throttle driver is then literally: for each rung of the
yielding-resident ladder, one of these PUTs with the rung's command string.
HTTP 200 + empty/`{}` body == accepted.

### Optional sanity probe (no GPU side-effect)
`stat unit` / `stat fps` toggles the on-screen overlay — useful to eyeball that
the channel is live without changing render load:
```bash
curl -sS -X PUT http://127.0.0.1:30010/remote/object/call \
  -H 'Content-Type: application/json' \
  -d '{"objectPath":"/Script/Engine.Default__KismetSystemLibrary",
       "functionName":"ExecuteConsoleCommand",
       "parameters":{"WorldContextObject":null,"Command":"stat fps"},
       "generateTransaction":false}'
```

---

## Sources
- Remote Control API HTTP Reference (`/remote/object/call`, port 30010, host
  127.0.0.1): <https://dev.epicgames.com/documentation/unreal-engine/remote-control-api-http-reference-for-unreal-engine> (and the 5.2 mirror <https://docs.unrealengine.com/5.2/en-US/remote-control-api-http-reference-for-unreal-engine/>)
- Remote Control Quick Start (plugin name "Remote Control API", `WebControl.StartServer` / `WebControl.EnableServerOnStartup`, loopback default, Internet warning): <https://dev.epicgames.com/documentation/en-us/unreal-engine/remote-control-quick-start-for-unreal-engine>
- Remote Control in standalone `-game` is finicky (CAVEAT): <https://forums.unrealengine.com/t/remote-control-on-standalone-game-mode/2663578>
- Local engine source [VERIFIED-SOURCE]:
  `Engine/Plugins/VirtualProduction/RemoteControl/Source/RemoteControlCommon/Public/RemoteControlSettings.h`,
  `.../WebRemoteControl/Private/WebRemoteControl.cpp`,
  `Engine/Source/Runtime/Online/HTTPServer/Private/HttpServerConfig.cpp`,
  `Engine/Source/Runtime/Engine/Classes/Kismet/KismetSystemLibrary.h`,
  `Engine/Plugins/VirtualProduction/RemoteControl/RemoteControl.uplugin`.
