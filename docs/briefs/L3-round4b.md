# L3 round 4b (coordinator check of your round-4 screenshots, before they go to the owner)

Two defects remain in `round4/human_detected.png` (and likely the other states):
1. Bottom edge: below the fading 0:04 row there is still one more row whose glyph tops are sliced by the edge
   (faint, but visible at y ~1785-1800). The fade must end in empty space: no glyph of any row may touch or cross the
   visible edge. Check every state, light and dark, with a pixel test (bottom 12 px of the scroll area are background
   colour only) and add that test.
2. Timeline: the card says "Said 39:10 into the call" but Menu 0:41 + Hold 38:32 = 39:13 (person answers 39:13 per
   your own fixture). The quoted line, the strip, the "After 38:32 on hold" line and the transcript must use the same
   moment. Add a test that the quote's time equals the person-answer time in the fixture.
Also check the strip labels sit under their own segment (Menu under the short one, Hold under the long one).
Re-shoot `round4/` in place (keep the before/after). Same LIMITS. DONE: paths, test output, SHA.
