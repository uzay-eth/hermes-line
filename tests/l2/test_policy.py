import concurrent.futures
from datetime import datetime, timezone
import unittest
from types import SimpleNamespace
from line.policy import Settings, ConsentStore, check_destination, is_quiet

FI = '+17755550102'

class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings.load({'allowed_destinations': [FI]})

    def test_canonical_approved(self):
        result = check_destination(FI, self.settings)
        self.assertTrue(result.allowed)
        self.assertEqual(result.e164, FI)

    def test_s2_rejection_shapes(self):
        # Covers S2 §6: emergency aliases, wrappers, prefixes, short codes,
        # ambiguous formats, postdial, arbitrary SIP, Bora, non-US/NANP.
        bad = [None, 911, '', '911', '112', '999', '000', '106', '9-1-1',
               '9 1 1', '(911)', ' 911 ', 'tel:911', 'sip:911@example.test',
               '+1911', '+44999', '+44112', '+61000', '+61106', '+61112',
               '1911', '011911', '00911', '01144999', '123456', '1234567',
               '*911', '#911', '*67+17755550102', '+17755550102#',
               '+17755550102,911', '+17755550102;911', '+17755550102w911',
               '+17755550102 ext 911', '+17755550102;ext=911',
               'tel:+17755550102', 'sip:+17755550102@example.test',
               '+1 (775) 555-0102', '17755550102', '7755550102',
               ' +17755550102', '+17755550102\n', '＋17755550102',
               '+١٧٧٥٥٥٥٠١٠٢', '+17755550172', '+442079460123',
               '+14165550123', '+12425550123', '+18095550123', '+19395550123',
               '+18005550123', '+10005550123', '+17751110123', '+17750000123',
               '+1775555010200000', '+17755550102\x00', 'https://example.test']
        self.assertEqual(len(bad), 57)
        for raw in bad:
            with self.subTest(raw=raw):
                result = check_destination(raw, self.settings)
                self.assertFalse(result.allowed)
                self.assertIsNone(result.e164)

    def test_hard_denies_survive_settings_bypass(self):
        for raw in ('911', '+1911', '+19005550123', '+14165550123', '+442079460123'):
            fake = SimpleNamespace(allowed_destinations=(raw,), allowlist_only=False)
            self.assertFalse(check_destination(raw, fake).allowed)

    def test_empty_allowlist_denies(self):
        self.assertFalse(check_destination(FI, Settings()).allowed)

    def test_no_suffix_emergency_false_block(self):
        raw = '+17755550911'
        self.assertTrue(check_destination(raw, Settings(allowed_destinations=(raw,))).allowed)

    def test_allowlist_is_exact(self):
        self.assertFalse(check_destination('+17755550103', self.settings).allowed)

    def test_rejected_paths_do_not_invoke_fake_provider(self):
        # Local adapter-shaped harness, not an L1 integration claim.
        calls=[]
        for path in ('business','bridge','callback','retry','transfer','verification','twiml'):
            for raw in ('911', '+1911', '+17755550172', '+14165550123'):
                decision=check_destination(raw,self.settings)
                if decision.allowed:
                    calls.append((path,decision.e164))
        self.assertEqual(calls, [])

class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s=Settings.load({})
        self.assertTrue(s.disclosure_enabled and s.quiet_hours_enabled and s.require_approval)
        self.assertFalse(s.recording_enabled)
        self.assertEqual((s.max_calls_per_line,s.allowlist_only,s.quiet_hours_start,s.quiet_hours_end),(1,True,'21:00','08:00'))

    def test_invalid_settings(self):
        for value in ({'unknown':True},{'recording_enabled':'false'},{'require_approval':False},
                      {'allowlist_only':'false'},{'max_calls_per_line':True},{'max_calls_per_line':2},
                      {'quiet_hours_start':'25:00'},{'quiet_hours_end':'8:00'},
                      {'quiet_hours_start':'08:00'},{'owner_timezone':'No/Such_Zone'},
                      {'allowed_destinations':['+19005550123']},{'allowed_destinations':FI}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings.load(value)

    def test_settings_snapshot(self):
        values=[FI]; settings=Settings.load({'allowed_destinations':values}); values.clear()
        self.assertEqual(settings.allowed_destinations,(FI,))

    def test_quiet_boundaries_and_timezone(self):
        for hour,minute,expected in [(7,59,True),(8,0,False),(20,59,False),(21,0,True)]:
            dt=datetime(2026,11,4,hour,minute,tzinfo=__import__('zoneinfo').ZoneInfo('America/Los_Angeles'))
            self.assertEqual(is_quiet(dt.astimezone(timezone.utc),Settings()),expected)

    def test_dst_fold_and_gap(self):
        for hour in (8,9):
            self.assertTrue(is_quiet(datetime(2026,11,1,hour,30,tzinfo=timezone.utc),Settings()))
        self.assertTrue(is_quiet(datetime(2026,3,8,10,30,tzinfo=timezone.utc),Settings()))

    def test_daytime_window_and_disabled(self):
        dt=datetime(2026,1,1,12,tzinfo=timezone.utc)
        self.assertTrue(is_quiet(dt,Settings(owner_timezone='UTC',quiet_hours_start='10:00',quiet_hours_end='14:00')))
        self.assertFalse(is_quiet(dt,Settings(quiet_hours_enabled=False)))

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError): is_quiet(datetime(2026,1,1),Settings())

class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.store=ConsentStore(clock=lambda:self.now)
        self.binding={'destination':FI,'role':'business','revision':1}

    def issue(self): return self.store.issue('dial','task',self.binding,10)
    def use(self,token,**kw): return self.store.consume(token,kw.get('action','dial'),kw.get('task','task'),kw.get('binding',self.binding))

    def test_single_use(self):
        t=self.issue(); self.assertTrue(self.use(t)); self.assertFalse(self.use(t))

    def test_expiry_at_boundary(self):
        t=self.issue(); self.now=10; self.assertFalse(self.use(t))

    def test_expiry_before_boundary(self):
        t=self.issue(); self.now=9.999; self.assertTrue(self.use(t))

    def test_mismatches_burn_token(self):
        for kw in ({'action':'bridge'},{'task':'another'}, {'binding':dict(self.binding,role='owner')},
                   {'binding':dict(self.binding,destination='+17755550103')}, {'binding':dict(self.binding,revision=2)}):
            t=self.issue(); self.assertFalse(self.use(t,**kw)); self.assertFalse(self.use(t))

    def test_snapshot_and_key_order(self):
        t=self.issue(); b=dict(self.binding); self.binding['revision']=2
        self.assertTrue(self.use(t,binding=dict(reversed(list(b.items())))))

    def test_invalid_token_and_binding(self):
        for token in (None,{},'unknown','x'*129): self.assertFalse(self.use(token))
        t=self.issue(); self.assertFalse(self.use(t,binding={'x':float('nan')}))

    def test_invalid_ttl(self):
        for ttl in (0,-1,3601,True,float('inf'),float('nan'),'10'):
            with self.assertRaises(ValueError): self.store.issue('dial','task',{},ttl)

    def test_restart_revokes(self):
        self.assertFalse(ConsentStore().consume(self.issue(),'dial','task',self.binding))

    def test_concurrent_consume_one_winner(self):
        t=self.issue()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(self.use,[t]*32)),1)

    def test_capacity_and_expiry_pruning(self):
        store=ConsentStore(clock=lambda:self.now,capacity=1)
        store.issue('a','t',{},1)
        with self.assertRaises(ValueError): store.issue('a','t',{},1)
        self.now=1; store.issue('a','t',{},1)


class Round3PolicyTests(unittest.TestCase):
    def test_toll_free_all_codes_in_both_allowlist_modes(self):
        for code in ('800','833','844','855','866','877','888'):
            number='+1'+code+'5550123'
            with self.subTest(code=code):
                self.assertTrue(check_destination(number,Settings(allowlist_only=False)).allowed)
                self.assertFalse(check_destination(number,Settings()).allowed)
                decision=check_destination(number,Settings(allowed_destinations=[number]))
                self.assertTrue(decision.allowed)
                self.assertEqual(decision.e164,number)

    def test_open_mode_keeps_approval_and_geographic_routing(self):
        s=Settings.load({'allowlist_only':False})
        self.assertTrue(s.require_approval)
        self.assertTrue(check_destination(FI,s).allowed)
        with self.assertRaises(ValueError): Settings.load({'allowlist_only':False,'require_approval':False})

    def test_premium_and_n11_denies_cannot_be_allowlisted(self):
        numbers=('+19005550123','+19765550123','+17759760123','+18009760123',
                 '+12115550123','+17752110123','+18009110123',
                 '+97955550123','+449055550123','+18765550123')
        for mode in (False,True):
            for number in numbers:
                with self.subTest(number=number,mode=mode):
                    settings=SimpleNamespace(allowlist_only=mode,allowed_destinations=[number],blocked_destinations=[])
                    decision=check_destination(number,settings)
                    self.assertFalse(decision.allowed)
                    self.assertIsNone(decision.e164)
                    with self.assertRaises(ValueError): Settings(allowlist_only=mode,allowed_destinations=[number])

    def test_emergency_and_unsafe_formats_denied_in_open_mode(self):
        s=Settings(allowlist_only=False)
        for raw in ('911','112','999','000','106','9-1-1','+1911','011911','*67'+FI,
                    'tel:'+FI,FI+';ext=123',FI+',911',FI+'#',FI+'\n','+14165550123'):
            with self.subTest(raw=raw): self.assertFalse(check_destination(raw,s).allowed)

    def test_blocked_list_wins_over_allowlist_and_open_mode(self):
        for mode in (False,True):
            for number in (FI,'+18005550123'):
                settings=Settings(allowlist_only=mode,allowed_destinations=[number],blocked_destinations=[number])
                decision=check_destination(number,settings)
                self.assertEqual((decision.allowed,decision.e164,decision.reason),(False,None,'blocked_destination'))

    def test_empty_blocklist_adds_no_number_deny(self):
        # The formerly hard-coded protected number is ordinary config data now.
        for number in (FI,'+18005550123','+17755550172'):
            for mode in (False,True):
                settings=Settings(allowlist_only=mode,allowed_destinations=[number],blocked_destinations=[])
                self.assertTrue(check_destination(number,settings).allowed)
        self.assertEqual(Settings().blocked_destinations,())

    def test_config_blocklist_normalizes_spaces_dashes_and_country_prefix(self):
        for value in (FI,'7755550102','17755550102','775-555-0102','1 775 555 0102',
                      '+1 775-555-0102','  +1-775-555-0102  '):
            for mode in (False,True):
                with self.subTest(value=value,mode=mode):
                    settings=Settings.load({'blocked_destinations':[value],'allowlist_only':mode,'allowed_destinations':[FI]})
                    self.assertEqual(settings.blocked_destinations,(FI,))
                    self.assertEqual(check_destination(FI,settings).reason,'blocked_destination')
                    # Noncanonical dial input stays rejected too; it cannot bypass blocking.
                    self.assertFalse(check_destination(value,settings).allowed)
                    # L1 can pass an unnormalized list via a settings adapter as well.
                    self.assertEqual(check_destination(FI,SimpleNamespace(blocked_destinations=[value],allowlist_only=False)).reason,'blocked_destination')

    def test_blocklist_matches_exact_numbers_not_prefixes_or_suffixes(self):
        s=Settings(allowlist_only=False,blocked_destinations=['775-555-0102'])
        self.assertTrue(check_destination('+17755550103',s).allowed)
        self.assertTrue(check_destination('+17025550102',s).allowed)
        with self.assertRaises(ValueError): Settings(blocked_destinations=['+1775*'])

    def test_blocklist_snapshot_and_duplicate_normalization(self):
        values=[FI,'775-555-0102']
        s=Settings.load({'blocked_destinations':values})
        values.clear()
        self.assertEqual(s.blocked_destinations,(FI,))

    def test_malformed_blocklist_fails_closed(self):
        for value in (None,True,FI,{},[None],['911'],['tel:'+FI],[FI+';ext=123'],
                      [FI+',911'],[FI+'\n'],['＋17755550102'],['+١٧٧٥٥٥٥٠١٠٢']):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): Settings.load({'blocked_destinations':value})
                d=check_destination(FI,SimpleNamespace(allowlist_only=False,blocked_destinations=value))
                self.assertFalse(d.allowed)

    def test_blocked_config_rechecked_at_each_fake_dispatch(self):
        # Simulates a changed policy between preview and dispatch, with no provider I/O.
        settings=SimpleNamespace(allowlist_only=False,blocked_destinations=[])
        self.assertTrue(check_destination(FI,settings).allowed)
        settings.blocked_destinations=['775-555-0102']
        provider_calls=[]
        for role in ('business','bridge','callback','retry','transfer','verification','twiml'):
            decision=check_destination(FI,settings)
            if decision.allowed: provider_calls.append((role,decision.e164))
        self.assertEqual(provider_calls,[])

    def test_invalid_allowlist_mode_fails_closed_at_gate(self):
        self.assertFalse(check_destination(FI,SimpleNamespace(allowlist_only='false')).allowed)
