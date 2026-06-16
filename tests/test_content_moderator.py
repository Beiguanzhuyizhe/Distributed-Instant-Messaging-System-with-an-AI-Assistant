from server.content_moderator import ContentModerator


def test_normal_message_passes():
    moderator = ContentModerator()
    result = moderator.moderate("今天项目联调很顺利，晚上继续测试。")

    assert result.passed is True
    assert result.level == "low"


def test_mid_risk_word_is_replaced():
    moderator = ContentModerator()
    content = "你这个傻逼，代码写得太乱了"

    result = moderator.moderate(content)
    cleaned = moderator.replace_sensitive(content)

    assert result.passed is False
    assert result.level == "mid"
    assert "傻逼" not in cleaned
    assert "***" in cleaned


def test_high_risk_word_is_rejected():
    moderator = ContentModerator()
    result = moderator.moderate("我要 kill you")

    assert result.passed is False
    assert result.level == "high"


def test_english_detection_is_case_insensitive():
    moderator = ContentModerator()
    result = moderator.moderate("This is a FUCK test")
    cleaned = moderator.replace_sensitive("This is a FUCK test")

    assert result.passed is False
    assert result.level == "mid"
    assert "FUCK" not in cleaned


def test_short_ascii_keywords_do_not_match_inside_normal_words():
    moderator = ContentModerator()
    content = "please leave the group and route a2b after skill review"

    result = moderator.moderate(content)
    cleaned = moderator.replace_sensitive(content)

    assert result.passed is True
    assert cleaned == content


def test_ascii_keywords_still_match_as_tokens():
    moderator = ContentModerator()

    assert moderator.moderate("av").passed is False
    assert moderator.moderate("2B").passed is False
    assert moderator.moderate("kill you").level == "high"
    assert moderator.replace_sensitive("this is av") == "this is ***"
