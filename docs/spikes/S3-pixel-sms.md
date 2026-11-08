# S3: Pixel SMS spike and OTP masking

Status 2026-11-04 (PT). Worker: S3 spike thread. Brief: `docs/briefs/S3-pixel-sms.md` (BRIEF.md sha256 verified).
Code: [`spikes/s3-sms/`](../../spikes/s3-sms/README.md). No device was attached, so the live probe is **waiting for owner step**.

## 1. Answers in one place

| Question | Answer |
|---|---|
| Does `adb shell content query --uri content://sms/inbox` work as the shell user? | **Yes on Android 16 and earlier, by the AOSP source: shell holds `READ_SMS`, and a non-default reader gets the inbox+sent view.** |
| On the Android version the Pixel 10 Pro XL runs today? | **Unknown until the phone is attached.** It shipped on Android 16; Android 17 stable followed in June 2026 and the phone is eligible, so it very likely runs 17. Android 17 adds an SMS OTP protection that hides OTP messages from non-exempt readers for 3 hours. Google's docs do not say whether the shell is exempt, and the provider code for it is not in public AOSP. The probe's `watch` mode measures it in one test. |
| Columns and filters | All `sms` columns; `--where` takes any SQL selection on them (`date>=<ms>`, `address='...'`); `--projection a:b`, `--sort`, `--user`. No subqueries. Details in section 2.3. |
| Recommended fallback | **capcom6 "SMS Gateway for Android" (SMSGate) in local-server mode**, reached over `adb forward`. Apache-2.0, release v1.78.1 on 2026-10-29. **It is not a fix for the Android 17 delay**: it is also a non-default SMS app. Section 4. |
| Biggest open risk | Android 17's OTP delay may apply to *every* non-default reader (shell and gateway alike). Only the probe on the real phone settles it. Decision options for the owner are in the final report. |

## 2. The adb answer, with sources

### 2.1 Why it works on Android 16 and earlier (verified in source)

1. The shell package declares the permission. `packages/Shell/AndroidManifest.xml` (AOSP `main`, fetched 2026-11-04) has `uses-permission` for `READ_SMS` (also `RECEIVE_SMS`, `SEND_SMS`, `WRITE_SMS`, `READ_CONTACTS`, `READ_CALL_LOG`). [source](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/packages/Shell/AndroidManifest.xml)
2. `content query` is a plain provider SELECT. `Content.java`: usage `content query --uri <URI> [--user <USER_ID>] [--projection <PROJECTION>] [--where <WHERE>] [--sort <SORT_ORDER>]`; the output is `Row: <n> ` followed by `col=value` pairs joined by `, `, one row per line; no rows print `No result found.`; `--where` and `--sort` are passed as `QUERY_ARG_SQL_SELECTION` / `QUERY_ARG_SQL_SORT_ORDER`. [source](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/cmds/content/src/com/android/commands/content/Content.java)
3. The SMS provider gates on the READ_SMS app-op (`setAppOps(OP_READ_SMS, OP_WRITE_SMS)`) and serves callers that are not system, phone or the default SMS app a **restricted view**: `ProviderUtil.isAccessRestricted()` is `!isSystemOrPhone(uid) && !isDefaultSmsApplication(pkg)`, and `sms_restricted` is `SELECT * FROM sms WHERE type=1 OR type=2` (inbox and sent). In restricted mode **subqueries are rejected** and the query returns `null`, which the `content` tool prints as `No result found.` (a silent empty result, not an error). Shell is neither system nor phone nor default SMS app, so it gets this view. [SmsProvider.java](https://android.googlesource.com/platform/packages/providers/TelephonyProvider/+/refs/heads/android16-release/src/com/android/providers/telephony/SmsProvider.java), [ProviderUtil.java](https://android.googlesource.com/platform/packages/providers/TelephonyProvider/+/refs/heads/android16-release/src/com/android/providers/telephony/ProviderUtil.java), [MmsSmsDatabaseHelper.java](https://android.googlesource.com/platform/packages/providers/TelephonyProvider/+/refs/heads/android16-release/src/com/android/providers/telephony/MmsSmsDatabaseHelper.java)
4. Independent confirmation of the restricted view and its limits: the `adbsms` project says that on Marshmallow and above non-default apps see only `inbox` and `sent`, and that the shell "may lack some or all of the necessary permissions, leading to possible SecurityExceptions". [README](https://github.com/gonodono/adbsms)

What I did **not** verify: a live run on any Android 16/17 device (none attached). The source above says it should work; the probe proves it.

### 2.2 Android 17: SMS OTP protection (the real risk)

Android 17 (API 37) behaviour changes, from Google's docs:

* **All apps, any target SDK:** messages in WebOTP or SMS Retriever format are not accessible to an app that has SMS permission "but is not the intended recipient" until three hours after receipt. "During this three hour delay, the `SMS_RECEIVED_ACTION` broadcast is withheld and SMS provider database queries are filtered." [behavior-changes-all](https://developer.android.com/about/versions/17/behavior-changes-all#sms-otp-all-apps)
* **Apps targeting API 37:** the same delay extends to **standard SMS messages containing an OTP**. [behavior-changes-17](https://developer.android.com/about/versions/17/behavior-changes-17#sms-otp-protection)
* **Exempt:** "Certain apps such as the default SMS assistant app, connected device companion apps, etc." Google's advice: use SMS Retriever or SMS User Consent.
* Not stated anywhere I could read: whether the **shell uid** is exempt, whether the shell package counts as targeting 37, and how a message is classified as an OTP. The filter is in the telephony provider, but public AOSP `packages/providers/TelephonyProvider` `main` last changed 2025-03-14 (checked via gitiles log), and the `android16-release` `SmsProvider.java` / `ProviderUtil.java` contain no OTP or retriever filtering, so the Android 17 logic cannot be read from public source.

Consequence for this project: on Android 17 a fresh code could be invisible to `content query` for three hours, which would make adb useless for the "read the code now" story. **If** the shell is exempt it works as designed; **if** not, the gateway app (also a non-default reader) is affected the same way. Both cases are decided by one measurement, and `probe.py watch` is built for it (section 3).

### 2.3 Columns and filters

* Columns of the inbox rows (all of `sms`, per [Telephony.TextBasedSmsColumns](https://developer.android.com/reference/android/provider/Telephony.TextBasedSmsColumns)): `_id`, `thread_id`, `address` (sender), `date` ("the date the message was received", ms since epoch), `date_sent`, `read`, `seen`, `status`, `type` (1 = inbox), `body`, `subject`, `protocol`, `service_center`, `locked`, `error_code`, `sub_id`.
* Filters that work: any SQL on those columns through `--where`, for example `date>=1793836800000`, `address='ACME'`, combined with `AND`. `--sort "date DESC"`, `--projection _id:address:date:type:body`, `--user <id>`.
* Filters that do **not** work for a restricted caller: subqueries (silently empty result).
* `address` is stored as received (`+1555…`, a national number, a short code like `32665`, or an alphanumeric ID like `ACME`), so an exact SQL match misses spelling variants. `AdbSource` therefore filters on `date` on the device and on `address` in Python after normalising both sides (`device_side_address_filter=True` opts into SQL).
* Not used: the `--sort "date DESC LIMIT n"` trick. I could not confirm it from source; `AdbSource` truncates client-side.
* Not visible to a restricted reader: drafts and outbox (by the view definition). RCS chat messages are widely reported to live in Google Messages' own database rather than the telephony provider; I did not verify that here. Verification codes normally arrive as plain SMS, but if the probe shows an expected message missing, check that first.
* Parser notes (`smsline/sources/adb.py`): put `body` last in the projection so every earlier field has a fixed shape; bodies can contain newlines and `, address=`; a sender can put a fake `Row: 1 _id=…` line inside a body. The source requires consecutive row indexes and cross-checks the parsed `_id` list against an `_id`-only query. Test `test_forged_row_would_pass_without_the_id_cross_check` shows the forgery succeeds without that check; `test_forged_row_inside_body_is_rejected` shows it blocked.

### 2.4 Android version of the Pixel 10 Pro XL

Press and tracker sources: the phone launched on Android 16 (Aug 2025); Android 17 beta 4.1 shipped 2026-06-01 and the stable release is reported as 2026-06-16, with the Pixel 6 and newer listed as eligible, including the Pixel 10 Pro XL. [Android release notes](https://developer.android.com/about/versions/17/release-notes), [update tracker](https://www.androidupdatetracker.com/p/google-pixel-10-pro-xl), [SlashGear](https://www.slashgear.com/2211384/android-17-coming-to-these-google-pixel-phones/), [Nokiamob](https://nokiamob.net/2026/06/17/android-17-rollout-begins-for-pixel-the-list-of-eligible-devices/). I did not read the stable date from a Google page, and the real phone's build is unknown until `probe.py check` runs (it reports `ro.build.version.release`, `sdk`, `security_patch`, and `android17_otp_protection_expected`).

## 3. Probe on the phone: waiting for owner step

Tooling: `adb` 37.0.1 installed with `brew install --cask android-platform-tools` (free, Android SDK licence). `adb devices` today:

```
$ adb devices
List of devices attached

$ python3 spikes/s3-sms/probe.py check ; echo exit=$?
{"detail": "no device attached", "owner_steps": ["plug the Pixel into the Mac by USB", "unlock it and accept 'Allow USB debugging?' for this Mac (tick 'Always allow')"], "probe": "status", "status": "waiting for owner step"}
exit=3
```

**Owner step (one short list, for the coordinator to pass on):** plug the Pixel into the Studio by USB, unlock it, accept "Allow USB debugging?" for this Mac. Then, in `spikes/s3-sms/`:

1. `python3 probe.py check`: reports model, Android release, SDK, patch level, and whether a READ_SMS `content query` is accepted (a SELECT that matches no row, so no message is read).
2. `python3 probe.py inbox --since-minutes 120`: masked listing (id, masked sender, UTC time, length, 40-char shape such as `A a a 9.`, OTP found yes/no). No body or code is printed.
3. `python3 probe.py watch --duration 900`: during these 15 minutes the line should receive one ordinary text and one real service verification code. The probe prints, per new message, `lag_s` = first time visible minus the phone's timestamp. Plain message visible in seconds but the code only hours later = Android 17 OTP protection applies to the shell. A hand-typed "your code is 123456" from a friend may not be classified as an OTP by the system, so use a real service code for the decisive run.

How to read the result: exit 3 = not ready; exit 4 = the provider refused the shell (then the gateway is the path); exit 5 = unexpected output (ParseError); `otp_shaped_lag_s` large with `plain_lag_s` small = delay applies.

Probe safety: it runs only `adb devices`, `adb shell getprop <name>` and `adb shell content query` (allow-list in `AdbSource._shell`, asserted by `test_only_read_only_remote_verbs`). It changes no setting, installs nothing, sends nothing, dials nothing.

## 4. Fallback: on-device SMS gateway apps

Repo facts below come from the GitHub API on 2026-11-04 (licence, latest release, last push); behaviour from each project's own docs. Nothing was installed.

| | License | Last release | Local-only mode | Auth | Delivery | Notes |
|---|---|---|---|---|---|---|
| **capcom6/android-sms-gateway** (SMSGate) | Apache-2.0 | v1.78.1, 2026-10-29 (pushed 2026-11-05) | **Yes**: on-device server, default port 8080, no account needed in local mode | HTTP Basic (credentials shown in the app); webhooks signed HMAC-SHA256 with `X-Signature` and `X-Timestamp` | **Both**: `GET /inbox` (local mode only; `from`/`to` ISO 8601, `limit` 1-500, `type=SMS`) and webhooks (`sms:received`, batch variants, 14 retries with exponential backoff) | Android 5+, Android 15 supported. Deliberately **not** the default SMS app. Needs `RECEIVE_SMS` / `READ_SMS`. Not on Play: sideload; Play Protect may block the install and grey out the SMS permission on Android 15+ (manual grant). 5.9k stars. OpenAPI published. |
| traccar/traccar-sms-gateway | GPL-3.0 | v7.0.4, 2026-07-05 | Phone-side HTTP API | Not verified | The pages I read describe an API for **sending**; no receive/read API documented there | Fork of Fossify Messages: a full messaging app, so as the *default* SMS app it would be exempt from the Android 17 delay. Would require changing the phone's default SMS role (owner decision). Reading incoming SMS over the API unverified. |
| bogkonstantin/android_income_sms_gateway_webhook | MIT | v2.4.0, 2026-06-10 | No server: **push only**, forwards each incoming SMS as JSON to a URL you set | none on the phone; you control the receiving URL | Push (retries with exponential backoff, optional heartbeat) | No pull API, so no "read recent messages" for the probe. Available on F-Droid and GitHub. Simple and auditable. |
| NdoleStudio/httpsms | AGPL-3.0 | v1.2.1, 2026-08-23 | No: cloud/Firebase architecture; self-hosting needs your own backend | API key header | Cloud push/poll | AGPL is awkward for a catalog plugin; heavy for a spare phone on USB. Details from project docs, not verified in the repo. |
| textbee/textbee | AGPL-3.0 | v2.9.0, 2026-09-21 | No: needs its API/web backend (docker-compose in repo) | API key | REST + webhooks via backend | Same backend weight as httpsms. |
| pppscn/SmsForwarder | BSD-2-Clause | v3.5.0, 2026-02-14 | Has an embedded HTTP server for remote control (not evaluated in depth) | Not evaluated | Forwards to many channels | Chinese-first docs; also hooks calls and notifications, a much larger permission surface than needed. |
| gonodono/adbsms | MIT | 0.0.13, 2026-09-23 | adb helper, not a gateway | adb | Grants READ_SMS to its own package and re-exposes the provider as `content://adbsms` | Relevant only if the shell is *refused* (SecurityException). It is also a non-default reader, so no help against the Android 17 delay. |
| ushahidi/SMSSync | LGPL-3.0 | v3.1.1, 2017-02-21 | | | | Unmaintained since 2021. Excluded. |

**Recommendation: capcom6 SMSGate, local-server mode, accessed through `adb forward tcp:18080 tcp:8080` so the plugin talks to `http://127.0.0.1:18080`.**

Why: permissive licence that suits a catalog plugin, actively released (six days before this spike), a real local mode with no account and no cloud, documented Basic auth plus signed webhooks, and both pull (`GET /inbox` with a time window, which is what an approved-task window needs) and push. `GatewaySource` is already written against its published OpenAPI document and accepts plain HTTP only for loopback, matching the app's own rule, so nothing is exposed on the Wi-Fi.

Costs to know about: sideloaded APK; it adds a permission-holding app and credentials on the phone; battery-optimisation exclusion is needed; `GET /inbox` returns `contentPreview`, and I have not confirmed that it carries the full body of long messages (not an issue for short OTP texts). **It does not escape Android 17's OTP delay**, because it is a non-default reader by design. If the probe shows the shell is delayed, the real alternatives are in the final report's decision options.

## 5. What was built (`spikes/s3-sms/`)

See the [README](../../spikes/s3-sms/README.md) for the layout. Summary of the design choices:

* **SMS source interface** (`SmsSource.fetch/check`): `AdbSource`, `GatewaySource`, `FixtureSource`. Filters are best effort on the device and always re-applied in Python.
* **OTP extraction** (`otp.py`): codes of 4-8 digits; grouped (`482 915`, `482-915`, `4829 1537`); letter-prefixed (`G-482915` gives `482915`); uppercase alphanumerics (`AB12CD`, `X7K-9QP2`). Candidates inside phone numbers, dates, money, URLs and e-mail addresses are dropped. Each candidate is scored by shape and by cues in the same sentence: verification cue words in about fifteen languages score up (11 have fixture cases: English, Turkish, German, French, Spanish, Russian, Arabic, Chinese, Japanese, Korean, and Hindi via the "OTP" word), order/reference/card-ending/flight words and unit words ("minutes", "points", "TL") score down; promo vocabulary without OTP vocabulary scores down; a code-only body scores up. Two close, different codes make the message `ambiguous` and no primary is chosen. Arabic-Indic, fullwidth digits, NBSP and zero-width characters are normalised first.
* **Task matching** (`matching.py`, the ADR 0001 "SMS code belongs to task" gate): sender rule (E.164 normalised phone, short code, case-folded alphanumeric ID, `*` glob) AND time window (start widened by a clock-skew allowance) AND one unambiguous code AND not released before. Outcomes: `release`, `no_message`, `no_code`, `ambiguous`, `already_used`, `expired`. `wait_for_code` polls, tolerates transient source errors, stops when the window closes.
* **Masking** (`masking.py`, `secret.py`): message bodies and codes are `SecretText`, so formatting cannot leak; `redact_all` (shape only) for logs and probe output, `mask_codes` (`[CODE]`) for owner-facing transcripts, `mask_sender`, and a `MaskingFilter` for `logging`. `.reveal()` has four call sites, pinned by a test. Only `MatchOutcome.release()` hands a code to a caller, and only for the `release` decision.

Known limits (also in the module docstrings): bodies over 4000 characters are treated as having no code (OTP texts are short; the cap bounds worst-case time); letter-only and lowercase alphanumeric codes are not detected; a bare 4-8 digit number in a short message with no cue is accepted by design, which is why sender and window matching are mandatory, not optional; `mask_codes` cannot see letter-only codes, so bodies are never logged through it; I did not tune any threshold on real messages (none were read).

## 6. Test output (offline, stdlib `unittest`, run 2026-11-04)

Checks beyond the pass count: the suite also passes with `socket.socket` disabled after imports (no network use); the leak sweep (`test_no_leak.py`) pushes every fixture body through repr/str/format, `log_summary`, probe output, matcher outcomes, `wait_for_code` logs captured with the masking filter **off**, and adb parse errors, then asserts no planted code and no body text appears; an adversarial-input timing test (19 crafted shapes up to 100 000 characters through `extract_otp`, `mask_codes`, `redact_all`) found and fixed two worst-case problems during this spike: the first e-mail pattern was cubic (2.9 s on 1600 `@` characters) and a look-ahead mask regex was quadratic (8.9 s on a 100 000-character hyphenated run). Mutation checks (leaky `SecretText.__str__`, id cross-check removed, sender check removed, phone regex removed, linear e-mail regex reverted to the cubic one) each make tests fail. Test output contains no message text or codes (synthetic fixtures use a fictional sender `ACME`, reserved 555-01xx numbers and invented codes).

```
$ spikes/s3-sms/run_tests.sh
test_bad_format_args_do_not_crash_logging (tests.test_masking.LogMaskingTests) ... ok
test_filter_masks_message_and_args (tests.test_masking.LogMaskingTests) ... ok
test_handler_level_covers_child_loggers (tests.test_masking.LogMaskingTests) ... ok
test_install_is_idempotent (tests.test_masking.LogMaskingTests) ... ok
test_alnum_codes (tests.test_masking.MaskCodesTests) ... ok
test_codes_are_replaced_and_context_stays (tests.test_masking.MaskCodesTests) ... ok
test_every_planted_code_is_masked_in_every_case (tests.test_masking.MaskCodesTests) ... ok
test_known_limit_letter_only_code_is_not_masked (tests.test_masking.MaskCodesTests) ... ok
test_length_is_hidden (tests.test_masking.MaskCodesTests) ... ok
test_obfuscated_digits (tests.test_masking.MaskCodesTests) ... ok
test_phone_numbers_and_dates_are_masked_too (tests.test_masking.MaskCodesTests) ... ok
test_small_numbers_and_words_survive (tests.test_masking.MaskCodesTests) ... ok
test_body_str_is_wrapped_automatically (tests.test_masking.MessageSurfaceTests) ... ok
test_message_repr_and_summary_have_no_body (tests.test_masking.MessageSurfaceTests) ... ok
test_empty (tests.test_masking.RedactAllTests) ... ok
test_nothing_of_the_original_survives_in_any_language (tests.test_masking.RedactAllTests) ... ok
test_shape_only (tests.test_masking.RedactAllTests) ... ok
test_truncation (tests.test_masking.RedactAllTests) ... ok
test_equality_and_bool (tests.test_masking.SecretTextTests) ... ok
test_every_formatting_path_is_redacted (tests.test_masking.SecretTextTests) ... ok
test_immutable_and_typed (tests.test_masking.SecretTextTests) ... ok
test_no_serialisation (tests.test_masking.SecretTextTests) ... ok
test_reveal_is_the_only_way_out (tests.test_masking.SecretTextTests) ... ok
test_empty_and_tiny (tests.test_masking.SenderMaskTests) ... ok
test_phone_and_alnum (tests.test_masking.SenderMaskTests) ... ok
test_short_codes_stay_readable (tests.test_masking.SenderMaskTests) ... ok
test_a_resend_wins_over_the_earlier_code (tests.test_matching.MatcherDecisions) ... ok
test_ambiguous_never_releases (tests.test_matching.MatcherDecisions) ... ok
test_ambiguous_newer_message_blocks_older_code (tests.test_matching.MatcherDecisions) ... ok
test_clock_skew_widens_the_start_only (tests.test_matching.MatcherDecisions) ... ok
test_expired_when_window_closed_and_nothing_found (tests.test_matching.MatcherDecisions) ... ok
test_in_window_message_without_code (tests.test_matching.MatcherDecisions) ... ok
test_log_line_has_no_content (tests.test_matching.MatcherDecisions) ... ok
test_other_senders_codes_are_not_considered (tests.test_matching.MatcherDecisions) ... ok
test_release_is_the_only_unmasked_path (tests.test_matching.MatcherDecisions) ... ok
test_release_newest_matching_code (tests.test_matching.MatcherDecisions) ... ok
test_short_code_sender (tests.test_matching.MatcherDecisions) ... ok
test_single_use (tests.test_matching.MatcherDecisions) ... ok
test_used_set_can_be_shared_across_tasks (tests.test_matching.MatcherDecisions) ... ok
test_window_end_is_hard (tests.test_matching.MatcherDecisions) ... ok
test_window_start_end (tests.test_matching.MatcherDecisions) ... ok
test_wrong_sender_never_releases (tests.test_matching.MatcherDecisions) ... ok
test_android17_style_delay_times_out_without_release (tests.test_matching.PollingTests)
A provider that withholds OTP messages for 3h looks like 'nothing arrived'. ... ok
test_code_arrives_while_polling (tests.test_matching.PollingTests) ... ok
test_persistent_source_error_is_raised_for_fallback (tests.test_matching.PollingTests) ... ok
test_transient_source_errors_are_retried (tests.test_matching.PollingTests) ... ok
test_window_close_stops_polling_before_timeout (tests.test_matching.PollingTests) ... ok
test_forms (tests.test_matching.SenderNormalisation) ... ok
test_lookalike_senders_do_not_match (tests.test_matching.SenderNormalisation) ... ok
test_rule_matching (tests.test_matching.SenderNormalisation) ... ok
test_no_planted_code_appears_anywhere (tests.test_no_leak.LeakSweep) ... ok
test_no_file_writes_in_library (tests.test_no_leak.RevealAudit) ... ok
test_no_print_or_stdout_in_library (tests.test_no_leak.RevealAudit) ... ok
test_release_is_the_only_code_reveal (tests.test_no_leak.RevealAudit) ... ok
test_reveal_call_sites (tests.test_no_leak.RevealAudit) ... ok
test_mask_codes_still_masks_in_a_long_body (tests.test_otp.AdversarialInput) ... ok
test_overlong_body_yields_no_code (tests.test_otp.AdversarialInput) ... ok
test_worst_case_time_is_bounded (tests.test_otp.AdversarialInput) ... ok
test_cases (tests.test_otp.OtpCaseTable) ... ok
test_accepts_secrettext_and_str (tests.test_otp.OtpDetails) ... ok
test_ambiguity_has_no_primary_but_lists_both (tests.test_otp.OtpDetails) ... ok
test_candidate_repr_never_shows_value (tests.test_otp.OtpDetails) ... ok
test_kinds (tests.test_otp.OtpDetails) ... ok
test_printed_form_kept_for_prefixed_and_grouped (tests.test_otp.OtpDetails) ... ok
test_reasons_never_contain_content (tests.test_otp.OtpDetails) ... ok
test_regexes_are_linear_enough (tests.test_otp.OtpDetails) ... ok
test_threshold_is_a_real_gate (tests.test_otp.OtpDetails) ... ok
test_very_long_body_does_not_blow_up (tests.test_otp.OtpDetails) ... ok
test_check_flag_off_on_android16 (tests.test_probe.ProbeTests) ... ok
test_check_ok_reports_android17_flag (tests.test_probe.ProbeTests) ... ok
test_describe_is_the_only_message_to_output_path (tests.test_probe.ProbeTests) ... ok
test_inbox_output_is_masked (tests.test_probe.ProbeTests) ... ok
test_inbox_respects_window_and_limit (tests.test_probe.ProbeTests) ... ok
test_inbox_without_device_is_waiting (tests.test_probe.ProbeTests) ... ok
test_no_device_is_waiting_for_owner_step (tests.test_probe.ProbeTests) ... ok
test_permission_refused_exit_4 (tests.test_probe.ProbeTests) ... ok
test_shape_is_short_and_shape_only (tests.test_probe.ProbeTests) ... ok
test_unauthorized_is_waiting_for_owner_step (tests.test_probe.ProbeTests) ... ok
test_watch_measures_visibility_lag (tests.test_probe.ProbeTests)
A plain message shows up fast; an OTP-shaped one only after the provider releases it. ... ok
test_crlf_output (tests.test_sources.AdbParsing) ... ok
test_garbage_raises_without_echoing_content (tests.test_sources.AdbParsing) ... ok
test_id_rows (tests.test_sources.AdbParsing) ... ok
test_multiline_body_and_commas_in_body (tests.test_sources.AdbParsing) ... ok
test_null_values_and_empty_result (tests.test_sources.AdbParsing) ... ok
test_simple_rows (tests.test_sources.AdbParsing) ... ok
test_where_builder (tests.test_sources.AdbParsing) ... ok
test_address_filter_is_client_side_and_normalised (tests.test_sources.AdbSourceBehaviour) ... ok
test_address_filter_on_device_when_enabled (tests.test_sources.AdbSourceBehaviour) ... ok
test_body_that_looks_like_an_error_is_still_data (tests.test_sources.AdbSourceBehaviour) ... ok
test_check_ok_reports_device_facts_and_reads_no_message (tests.test_sources.AdbSourceBehaviour) ... ok
test_fetch_filters_and_orders (tests.test_sources.AdbSourceBehaviour) ... ok
test_forged_row_inside_body_is_rejected (tests.test_sources.AdbSourceBehaviour) ... ok
test_forged_row_would_pass_without_the_id_cross_check (tests.test_sources.AdbSourceBehaviour) ... ok
test_limit (tests.test_sources.AdbSourceBehaviour) ... ok
test_no_device (tests.test_sources.AdbSourceBehaviour) ... ok
test_offline_device (tests.test_sources.AdbSourceBehaviour) ... ok
test_only_read_only_remote_verbs (tests.test_sources.AdbSourceBehaviour) ... ok
test_permission_denied_is_classified (tests.test_sources.AdbSourceBehaviour) ... ok
test_persistent_disagreement_raises (tests.test_sources.AdbSourceBehaviour) ... ok
test_race_between_the_two_queries_is_retried_once (tests.test_sources.AdbSourceBehaviour) ... ok
test_remote_command_shape_and_quoting (tests.test_sources.AdbSourceBehaviour) ... ok
test_serial_is_passed_through (tests.test_sources.AdbSourceBehaviour) ... ok
test_timeout_maps_to_device_unavailable (tests.test_sources.AdbSourceBehaviour) ... ok
test_unauthorized_device (tests.test_sources.AdbSourceBehaviour) ... ok
test_user_flag (tests.test_sources.AdbSourceBehaviour) ... ok
test_apply_filters_stable_newest_first (tests.test_sources.FixtureSourceTests) ... ok
test_filters (tests.test_sources.FixtureSourceTests) ... ok
test_visibility_delay (tests.test_sources.FixtureSourceTests) ... ok
test_check_reads_no_message (tests.test_sources.GatewayTests) ... ok
test_credentials_never_in_repr (tests.test_sources.GatewayTests) ... ok
test_fetch_maps_fields_and_builds_request (tests.test_sources.GatewayTests) ... ok
test_http_errors (tests.test_sources.GatewayTests) ... ok
test_limit_is_clamped (tests.test_sources.GatewayTests) ... ok
test_malformed_payloads (tests.test_sources.GatewayTests) ... ok
test_url_rules (tests.test_sources.GatewayTests) ... ok

----------------------------------------------------------------------
Ran 115 tests in 2.098s

OK
```

## 7. Open risks

1. **Android 17 OTP delay (high).** May hide fresh codes from the shell and the gateway for 3 hours. Not decidable from public sources; one probe run decides it.
2. **Shell access untested live (medium).** Source analysis says `READ_SMS` is held and a restricted view is served. Vendor patches could differ. Probe `check` settles it.
3. **Google Messages / RCS (medium).** If a service sends its code over RCS rather than SMS, it will not appear in the telephony provider. Unverified; the probe will show a missing expected message.
4. **Gateway adds a permission-holding app and credentials on the phone (low-medium).** Sideload, Play Protect warnings, battery exclusions. Mitigated by loopback-only access over `adb forward`.
5. **Heuristic extraction (low-medium).** Thresholds are untuned on real traffic. Mitigated by the mandatory sender and window gates, ambiguity refusal, single-use, and owner approval per task.
6. **Attacker-controlled SMS text (addressed).** Forged-row parsing is blocked; analysis time is bounded (tested); bodies never reach logs or the model; the code is revealed only through `release()`.
7. **Pixel clock vs Mac clock (low).** Window start is widened by 30 s by default; exposes `clock_skew_ms`.

## 8. Sources

* AOSP Shell manifest: https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/packages/Shell/AndroidManifest.xml
* AOSP `content` command: https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/cmds/content/src/com/android/commands/content/Content.java
* AOSP TelephonyProvider (android16-release): https://android.googlesource.com/platform/packages/providers/TelephonyProvider/+/refs/heads/android16-release/
* Telephony.TextBasedSmsColumns: https://developer.android.com/reference/android/provider/Telephony.TextBasedSmsColumns
* Android 17 behaviour changes (all apps): https://developer.android.com/about/versions/17/behavior-changes-all
* Android 17 behaviour changes (targeting 17): https://developer.android.com/about/versions/17/behavior-changes-17
* Android 17 release notes: https://developer.android.com/about/versions/17/release-notes
* adbsms: https://github.com/gonodono/adbsms
* SMS Gateway for Android: https://github.com/capcom6/android-sms-gateway, docs https://docs.sms-gate.app/ (webhooks, reading-messages, faq), OpenAPI https://capcom6.github.io/android-sms-gateway/
* Other gateways: https://github.com/traccar/traccar-sms-gateway, https://www.traccar.org/sms-gateway/, https://github.com/bogkonstantin/android_income_sms_gateway_webhook, https://github.com/NdoleStudio/httpsms, https://github.com/textbee/textbee, https://github.com/pppscn/SmsForwarder
* Pixel / Android 17 rollout (press): https://www.androidupdatetracker.com/p/google-pixel-10-pro-xl, https://www.slashgear.com/2211384/android-17-coming-to-these-google-pixel-phones/, https://nokiamob.net/2026/06/17/android-17-rollout-begins-for-pixel-the-list-of-eligible-devices/

