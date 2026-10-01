from prat.scoring import score, words


def test_words_keeps_norwegian_letters_and_drops_punctuation():
    assert words("Hvor mye koster blåbær-syltetøy?") == ["hvor", "mye", "koster", "blåbær-syltetøy"]


def test_perfect_match_ignores_case_and_punctuation():
    r = score("Kan jeg få en kaffe, takk?", "kan jeg få en kaffe takk")
    assert r["score"] == 100
    assert all(w["status"] == "ok" for w in r["words"])
    assert [w["word"] for w in r["words"]] == ["Kan", "jeg", "få", "en", "kaffe", "takk"]


def test_near_miss_counts_as_close():
    r = score("Jeg vil ha en kanelbolle", "jeg vil ha en kanelboller")
    statuses = {w["word"]: w["status"] for w in r["words"]}
    assert statuses["kanelbolle"] == "close"
    assert 80 <= r["score"] < 100


def test_missing_words_are_marked():
    r = score("Jeg har bodd i Norge i to år", "jeg har bodd i norge")
    assert [w["status"] for w in r["words"]][-3:] == ["missed", "missed", "missed"]
    assert r["score"] < 70


def test_extra_words_are_penalised():
    clean = score("Hei på deg", "hei på deg")["score"]
    rambling = score("Hei på deg", "hei på deg og deg og deg")
    assert rambling["extra"] and rambling["score"] < clean


def test_empty_inputs():
    assert score("", "hei")["score"] == 0
    assert score("Hei", "")["score"] == 0
