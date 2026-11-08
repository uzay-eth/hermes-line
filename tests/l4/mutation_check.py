"""Mutation check: break one safety property at a time and require the suite to fail.

Run from the repo root:  python3 tests/l4/mutation_check.py
Each mutation edits a plugin/line/sms source file in place and restores it in a finally block.
Exit 0 only if every mutation is KILLED by tests/l4.
"""
import subprocess, shutil, sys, os
ROOT = os.getcwd()
MUTS = [
 ("scrub hook disabled", "plugin/line/sms/hooks.py", "        out = sc.scrub(result)\n        return out if out != result else None", "        return None"),
 ("llm hook disabled", "plugin/line/sms/hooks.py", "        out = sc.scrub(text)\n        return out if out != text else None", "        return None"),
 ("fill result leaks code", "plugin/line/sms/delivery.py", '        if self.filled:\n            d["filled_fields"] = self.filled', '        d["code"] = getattr(self, "_x", "") or ""\n        if self.filled:\n            d["filled_fields"] = self.filled'),
 ("consent never consumed", "plugin/line/sms/service.py", "ok = bool(store.consume(token, OTP_ACTION, task.task_id, json_binding(approval)))", "ok = True"),
 ("used set ignored", "plugin/line/sms/service.py", "                already = key in self.used", "                already = False"),
 ("sender check removed", "plugin/line/sms/matching.py", "m for m in messages if t.sender_ok(m.address) and t.in_window(m.date_ms)", "m for m in messages if t.in_window(m.date_ms)"),
 ("window check removed", "plugin/line/sms/matching.py", "m for m in messages if t.sender_ok(m.address) and t.in_window(m.date_ms)", "m for m in messages if t.sender_ok(m.address)"),
 ("log the code", "plugin/line/sms/service.py", "        LOG.info(res.log_line())", "        LOG.info(res.log_line() + ' ' + (res.secret.reveal() if res.secret else ''))"),
 ("slot kept after fill", "plugin/line/sms/delivery.py", "        self._drop(task_id)   # delivered: the sealed copy is dropped at once\n", "        pass\n"),
 ("scrub not registered on release", "plugin/line/sms/service.py", "                register_released(outcome.code)", "                pass"),
 ("ambiguous released", "plugin/line/sms/matching.py", "            if res.ambiguous:", "            if False:"),
 ("binding from caller, window not enforced after close", "plugin/line/sms/service.py", "        if int(self._clock() * 1000) > task.window_end_ms:", "        if False:"),
 ("sink error logged with exception text", "plugin/line/sms/delivery.py", 'LOG.warning("otp sink %s failed: %s", self._tool, type(exc).__name__)', 'LOG.warning("otp sink %s failed: %s", self._tool, exc)'),
 ("origin check removed", "plugin/line/sms/delivery.py", "        if current != dest.origin:", "        if False:"),
 ("unreadable origin allowed", "plugin/line/sms/delivery.py", "        if current is None:\n            return FillStatus.ORIGIN_UNKNOWN.value if first else FillStatus.ORIGIN_CHANGED.value", "        if False:\n            return FillStatus.ORIGIN_UNKNOWN.value"),
 ("destination left out of the consent binding", "plugin/line/sms/service.py", '        "destination": task.destination.binding(),\n', ""),
 ("call_id check removed", "plugin/line/sms/delivery.py", "dest.kind != CALL or dest.call_id != call_id", "dest.kind != CALL"),
 ("call-approved task may fill", "plugin/line/sms/delivery.py", "if dest is None or dest.kind != FILL:", "if dest is None:"),
 ("dtmf digit check removed", "plugin/line/sms/delivery.py", '        if mode == "dtmf" and not slot.secret.fullmatch(r"[0-9]{1,16}"):', "        if False:"),
 ("live counterpart ignored", "plugin/line/sms/delivery.py", "            if live != dest.counterpart:", "            if False:"),
 ("store write failure ignored", "plugin/line/sms/service.py", '                LOG.warning("otp task=%s released-store error: %s", task.task_id, type(exc).__name__)\n                return ReleaseResult(ReleaseStatus.STORE_ERROR, task.task_id, None, _who(outcome), outcome.considered)\n            # From this moment', '                pass\n            # From this moment'),
 ("H1 slot generation/binding checks removed (reuse + revalidate)", "plugin/line/sms/delivery.py", ("if slot is not None and (slot.generation != approval.generation or slot.binding != approval.binding):", "        if (cur is None or cur.generation != slot.generation or cur.binding != slot.binding\n                or (approval_generation is not None and approval_generation != slot.generation)):"), ("if False:", "        if False:")),
 ("H1 eager invalidation on replacement removed", "plugin/line/sms/delivery.py", "        service.registry.on_change(self._drop)", "        pass"),
 ("H1 delivery not serialized against replacement", "plugin/line/sms/delivery.py", "        with self.service.registry.gate:\n            status = self._revalidate(task_id, slot, approval_generation)\n            if status is not None:\n                return self._r(status, task_id)\n            cur =", "        if True:\n            status = self._revalidate(task_id, slot, approval_generation)\n            if status is not None:\n                return self._r(status, task_id)\n            cur ="),
 ("H1 commit does not recheck the generation", "plugin/line/sms/service.py", "            if cur is None or cur.generation != approval.generation:", "            if False:"),
 ("M1 generation not compared at release", "plugin/line/sms/service.py", "if approval_generation is not None and approval_generation != approval.generation:", "if False:"),
 ("M1 unknown pending approval not refused", "plugin/line/sms/hooks.py", "    return g if isinstance(g, int) and not isinstance(g, bool) else -1", "    return g if isinstance(g, int) and not isinstance(g, bool) else None"),
 ("BF re-probe after typing removed", "plugin/line/sms/delivery.py", '        return "ok" if self.origin_probe(browser_task_id) == origin else "origin_changed"', '        return "ok"'),
 ("BF re-probe before typing removed", "plugin/line/sms/delivery.py", "            bad = self._check_fill(cur, browser_task_id, first=False)   # re-probe immediately before typing", "            bad = None"),
 ("BF browser fill on by default", "plugin/line/sms/delivery.py", "otp_browser_fill: bool = False,", "otp_browser_fill: bool = True,"), ("N1 gate held across the call sink", "plugin/line/sms/delivery.py", "            generation = slot.generation\n        if not self.call_sink.send(call_id, slot.secret, mode, approval_generation=generation):\n            return self._r(FillStatus.SINK_FAILED.value, task_id, sender=slot.sender)\n        self._drop(task_id, slot)\n        return self._r(FillStatus.SENT.value, task_id, sender=slot.sender)", "            generation = slot.generation\n            if not self.call_sink.send(call_id, slot.secret, mode, approval_generation=generation):\n                return self._r(FillStatus.SINK_FAILED.value, task_id, sender=slot.sender)\n            self._drop(task_id, slot)\n            return self._r(FillStatus.SENT.value, task_id, sender=slot.sender)"),
 ("N1 generation not passed to the sink", "plugin/line/sms/delivery.py", "return bool(send(call_id, secret, mode, approval_generation=approval_generation))", "return bool(send(call_id, secret, mode, approval_generation=0))"),
 ("N1 missing generation accepted", "plugin/line/sms/delivery.py", "        if not isinstance(approval_generation, int) or isinstance(approval_generation, bool):", "        if False:"),
 ("N1 sink capability not checked before release", "plugin/line/sms/delivery.py", "        if not self.call_sink.accepts_generation():", "        if False:"),
 ("N1 finished send drops any slot", "plugin/line/sms/delivery.py", "            if only is None or self._slots.get(task_id) is only:", "            if True:"), ("W1 configured serial ignored (-s dropped)", "plugin/line/sms/sources/adb.py", '        if target:\n            base += ["-s", target]', '        if False:\n            base += ["-s", target]'),
 ("W2 unlimited reconnects (no one-per-fetch bound)", "plugin/line/sms/sources/adb.py", "        try:\n            res = self._once(remote, addr)", "        for _ in range(1):\n          pass\n        self._reconnect(\"again\")\n        try:\n            res = self._once(remote, addr)"),
 ("W3 mdns match by prefix, any phone", "plugin/line/sms/sources/adb.py", "if n == base and t == MDNS_SUFFIX[1:]]", "if t == MDNS_SUFFIX[1:]]"),
 ("W4 connect address not validated", "plugin/line/sms/sources/adb.py", "if len(parts) == 3 and _HOSTPORT.match(parts[2].strip()):", "if len(parts) == 3:"),
 ("W5 length check dropped", "plugin/line/sms/sources/adb.py", "        if text[p] != \"\\n\":\n            raise ParseError(\"row %d: text after the reported length\" % (len(rows) - 1))", "        if False:\n            pass"),
 ("W6 unauthorized wireless retried", "plugin/line/sms/sources/adb.py", "            gone = isinstance(exc, _Gone) or \"timed out\" in str(exc)", "            gone = True"),
 ("W7 poll interval not shortened by read time", "plugin/line/sms/service.py", "max(0.0, self._interval_s - (self._clock() - loop_start))", "self._interval_s"),
 ('P1 probe sent to any number', 'tools/coordinator/send_latency_probe.py', '    if to != FI or FI not in t.ALLOW or to == t.BORA:', '    if False:'),
 ('P2 probe sender not checked (Fi / owner / Bora)', 'tools/coordinator/send_latency_probe.py', '    if line in (t.BORA, FI) or line in t.ALLOW:', '    if False:'),
 ('P3 nonce reusable (no exclusive record)', 'tools/coordinator/send_latency_probe.py', 'os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644', 'os.O_WRONLY | os.O_CREAT, 0o644'),
 ('P4 second probe within 15 min allowed', 'tools/coordinator/send_latency_probe.py', '    if busy:', '    if False:'),
 ('P5 probe sent before the reader is watching', 'tools/coordinator/send_latency_probe.py', '    if wait_ready_s > 0:', '    if False:'),
 ('P6 balance floor ignored', 'tools/coordinator/send_latency_probe.py', '    if s != 200 or b[0] - BALANCE_MARGIN < ops.STOP_BALANCE:', '    if s != 200:'),
 ('P7 unvalidated SID used in a URL', 'tools/coordinator/send_latency_probe.py', 'SID_RE.fullmatch(j.get("sid")) else None', 'True else None'),
 ('P8 reader matches the nonce inside other words', 'tests/l4/live/otp_delay.py', '    tok = r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(nonce)', '    tok = re.escape(nonce)'),
 ('P9 reader keeps waiting after a failed send', 'tests/l4/live/otp_delay.py', '                if not probe["seen"] and send_failed(sr):', '                if False:'),
 ('P10 reader ready file not removed', 'tests/l4/live/otp_delay.py', '                os.remove(ready)', '                pass'),
]
bad = 0
for name, path, old, new in MUTS:
    src = open(path).read()
    olds, news = (old, new) if isinstance(old, tuple) else ((old,), (new,))
    if any(o not in src for o in olds):
        print("SKIP (pattern missing):", name); bad += 1; continue
    mutated = src
    for o, n in zip(olds, news):
        mutated = mutated.replace(o, n, 1)
    open(path, "w").write(mutated)
    try:
        r = subprocess.run(["tests/l4/run_tests.sh"], capture_output=True, text=True)
    finally:
        open(path, "w").write(src)
    killed = r.returncode != 0
    print(("KILLED  " if killed else "SURVIVED"), name)
    if not killed: bad += 1
sys.exit(1 if bad else 0)
