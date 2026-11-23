"""The call's language follows the bulk of what the caller says, not a greeting."""

from app.orchestra.scout import majority_language


def test_arabic_greeting_then_french_order_stays_french():
    votes = {0: ("ar", 2), 1: ("fr", 14)}  # « Salam aleykoum » then « pour demain tu me mets quatre agneaux… »
    assert majority_language(votes, "fr") == "fr"
    assert majority_language({0: ("ar", 2)}, "fr") == "fr"  # a greeting alone, first turn: still French
    assert majority_language({0: ("ar", 3), 1: ("fr", 4)}, "ar") == "fr"  # back to the bulk once it outweighs


def test_a_real_switch_happens_and_ties_keep_the_current_language():
    assert majority_language({0: ("fr", 4), 1: ("en", 20)}, "fr") == "en"  # an English chef who keeps talking English
    assert majority_language({0: ("fr", 5), 1: ("en", 5)}, "en") == "en"
    assert majority_language({}, "fr") == "fr"
