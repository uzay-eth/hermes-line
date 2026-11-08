# L4 round 8 (coordinator): wireless adb

`git merge main`. The Pixel is now on wireless debugging, paired from the Studio; it appears as
`adb-5B050DLCQ000P3-eQkDjp._adb-tls-connect._tcp` (mDNS), endpoint currently 192.168.1.32:<port> (the port changes).
Coordinator check 2026-11-05 ~20:43Z: `otp_delay.py --serial <mdns name>` read 2 baseline messages (plugin read
latency 5672 ms), then failed mid-run with "adb exited with status 1"; `adb devices` then showed nothing, then
`offline`, then `device` again; `adb mdns services` listed the phone, then nothing (phone asleep?). Ping fails, TCP
port answers.
1. `AdbSource`: select by serial or mDNS service name, never "the only device"; accept a config setting
   `sms_adb_serial` (L1 owns the manifest: report the exact setting text for L1). On "not found" / "offline":
   `adb mdns services`, match the service name, `adb connect <ip:port>`, retry the query once; bounded (one reconnect
   per fetch, 10 s total). Clear, specific errors: "phone unreachable over Wi-Fi (asleep or off the network)",
   "paired device not advertised", "unauthorized", never a silent empty result.
2. Measure and cut the read latency over Wi-Fi (5.7 s is too slow for a live code): one adb shell per fetch, no
   redundant `devices`/`getprop` calls per poll, keep what the R1-reviewed verification needs.
3. `otp_delay.py` survives drops: logs each drop and reconnect with times, keeps watching.
4. README text for L1 (as a patch file `docs/release/plugin-readme-wireless.patch`): wireless debugging supported:
   pairing steps (`adb pair ip:port` with the pairing code, then auto-connect over mDNS), set `sms_adb_serial`,
   sleep or Wi-Fi changes can drop it, USB recommended for reliability.
5. Run a 3-minute live read check over Wi-Fi now (read only, no body, no code) and record reachability, drops,
   reconnects and per-fetch latency in `evidence/sms/`.
LIMITS as your brief; reading the Pixel inbox over adb is allowed; nothing is sent. DONE: tests, live check output
(masked), SHA, the setting text for L1.
