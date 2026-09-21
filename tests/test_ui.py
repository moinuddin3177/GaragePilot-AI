from garagepilot.ui import esc


def test_esc_escapes_dollars_and_markdown():
    assert esc("estimated $150 to $435") == r"estimated \$150 to \$435"
    assert esc("Joe's *Best* _Auto_") == r"Joe's \*Best\* \_Auto\_"
    assert esc("[link](http://x) `code` <b>") == r"\[link\](http://x) \`code\` \<b\>"


def test_esc_leaves_plain_text_and_handles_non_strings():
    assert esc("Downtown Brake & Tire, 4.6 stars") == "Downtown Brake & Tire, 4.6 stars"
    assert esc(2015) == "2015"
    assert esc("") == ""
