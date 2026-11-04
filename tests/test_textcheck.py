from promptgen import textcheck as tc


def test_join_blocks_no_double_dots():
    out = tc.join_blocks("A stone passage, camera fixed.", "cinematic realism, no logos.", "Avoid: avoid jitter.")
    assert ".." not in out
    assert "Avoid: jitter" in out


def test_fix_dashes_and_compounds():
    assert tc.fix_dashes("on-screen text — a quiet hall") == "on screen text, a quiet hall"


def test_strip_fixed_removes_pasted_blocks():
    style = "Style: authentic early 2000s consumer DV camcorder footage. Handheld, imperfect, nostalgic."
    scene = "The host stands in the passage. " + style + " Avoid: logos, watermarks."
    assert tc.strip_fixed(scene, [style]) == "The host stands in the passage."


def test_verbatim_check():
    src = 'End with this fixed style tag string: "cinematic historical realism, film grain, no logos."'
    assert tc.verbatim_in("cinematic historical realism, film grain, no logos.", src)
    assert not tc.verbatim_in("cinematic realism, 4K", src)


def test_moderation_and_years():
    assert tc.moderation_hits("a pool of blood on the floor", tc.MODERATION_HARD) == ["blood"]
    assert tc.moderation_hits("bloodline of kings", tc.MODERATION_HARD) == []
    assert tc.year_drift("Late Victorian, 1912 street", {"1885", "1880", "1901"}) == {"1912"}
    assert tc.year_drift("Late Victorian street", {"1885"}) == set()


def test_spoken_copy_detection():
    text = "The butler has told me, in a low voice, that if a single glass reaches the drawing room with a smear"
    assert tc.copied_from("The butler has told me, in a low voice, that if a single glass", text)
    assert not tc.copied_from("Look, one smear and he'll know it was me.", text)


def test_placeholder():
    assert tc.placeholder_issue("a hall [insert here]")
    assert tc.placeholder_issue("a hall, line or silence")
    assert tc.placeholder_issue("a calm hall") is None
