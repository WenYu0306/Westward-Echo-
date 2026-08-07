"""Shared fixtures for Westward Echo integration tests."""
import json

import pytest


@pytest.fixture
def mock_llm_response():
    """Return a valid translation JSON response that parse_llm_response can handle."""

    def _build(
        translated_text=(
            "She stepped into the grand hall, her heels clicking against the marble floor."
        ),
        new_terms_found=None,
        adaptation_notes=None,
        chapter_summary="The heroine enters the grand hall and faces the CEO.",
    ):
        return json.dumps({
            "translated_text": translated_text,
            "new_terms_found": new_terms_found or [
                {"term_cn": "大殿", "term_en": "grand hall", "category": "location"},
                {"term_cn": "霸总", "term_en": "Alpha CEO", "category": "culture"},
            ],
            "adaptation_notes": adaptation_notes or [
                "Adapted '霸总' to 'Alpha CEO' for Western romance genre context"
            ],
            "chapter_summary": chapter_summary,
        })

    return _build


@pytest.fixture
def sample_chapter():
    """Return a sample Chinese chapter for testing."""
    return {
        "title": "第一章 穿成霸总文女主",
        "content": (
            "苏念醒过来的时候，发现自己躺在一张陌生的大床上。\n\n"
            "她揉了揉眼睛，试图回忆昨晚发生了什么。脑海里突然响起一个机械的声音：\n\n"
            "【叮——恭喜宿主绑定霸总攻略系统！】\n\n"
            "苏念愣住了。什么系统？什么霸总？她只是加了个班，怎么就穿越了？\n\n"
            "她环顾四周，房间奢华得像五星级酒店。落地窗外是城市的夜景，霓虹灯闪烁。\n\n"
            "门外传来脚步声。一个高大英俊的男人走了进来，穿着剪裁考究的西装。\n\n"
            '"你就是苏念？"男人冷冷地说，"从今天起，你就是裴家的人了。"\n\n'
            "苏念心里一万匹草泥马奔腾而过。她只是一个普通社畜，怎么就变成了霸总文女主？\n\n"
            "她深吸一口气，决定先弄清楚目前的处境再说。毕竟既来之，则安之。"
        ),
        "number": 1,
    }


@pytest.fixture
def sample_chapter_2():
    """Return a second sample chapter for multi-chapter tests."""
    return {
        "title": "第二章 裴总的契约",
        "content": (
            "第二天一早，苏念被楼下传来的钢琴声吵醒了。\n\n"
            "她走下楼，看到裴衍舟正坐在客厅的钢琴前弹奏。晨光洒在他身上，画面美好得不真实。\n\n"
            '"起来了？"裴衍舟头也不抬地说，"正好，我让律师拟了一份契约，你签一下。"\n\n'
            "苏念接过文件，快速扫了一眼。契约上写着：假扮三个月情侣，对外宣称已经结婚。\n\n"
            '"这是什么意思？"她皱起眉头。\n\n'
            '"字面上的意思。"裴衍舟终于抬起头，"三个月后，一切结束，你拿钱走人。"\n\n'
            "苏念冷笑一声。这剧情她可太熟了。上一世看了不下五十本霸总文，每个女主都是这么开始的。\n\n"
            "系统提示音又响了起来：【支线任务：拒绝契约。奖励：气场+10】\n\n"
            '苏念把契约放了回去，微笑道：“不好意思，裴总，我不演。”\n\n'
            "裴衍舟眼神一暗。"
        ),
        "number": 2,
    }


@pytest.fixture
def sample_chapter_3():
    """Return a third sample chapter for multi-chapter tests."""
    return {
        "title": "第三章 父凭子贵",
        "content": (
            "苏念在裴家已经住了一周了。\n\n"
            "她发现这里每天都有各种白莲花女配来找茬，如果不是穿书，她真会以为自己在演宫斗剧。\n\n"
            '"苏小姐，裴总请你去书房。"管家恭敬地说道。\n\n'
            "苏念走进书房，看到裴衍舟正站在落地窗前。他的侧脸在逆光中格外分明。\n\n"
            '"你这周的面试表现不错，"他说，"公司那边已经准备好了，明天开始上班。"\n\n'
            "苏念点了点头。她的社畜之魂在这几天里又觉醒了。\n\n"
            '"不过——"裴衍舟转身看着她，"你之前认识楚淮？"\n\n'
            "苏念心跳漏了一拍。楚淮，原书的男二号，是裴衍舟的竞争对手。\n\n"
            '"不认识。\"她说，"只是在找工作时去过耀星集团面试。"\n\n'
            "裴衍舟的目光锐利起来。他可不信。"
        ),
        "number": 3,
    }


@pytest.fixture
def sample_glossary():
    """Return a pre-populated glossary dict for testing."""
    return {
        "苏念": "Su Nian",
        "裴衍舟": "Pei Yanzhou",
        "楚淮": "Chu Huai",
        "林婉清": "Lin Wanqing",
        "裴氏集团": "Pei Group",
        "耀星集团": "Starbright Group",
    }


@pytest.fixture
def mock_translate_invoke(mock_llm_response):
    """Patch ChatOpenAI.invoke in the translate node to return a mock response.

    Returns a context-manager-ready helper so each test can customise the
    response or model class path.
    """

    def _patcher(
        translated_text=(
            "Su Nian opened her eyes and found herself lying on a large, unfamiliar bed."
        ),
        new_terms_found=None,
        target_path="src.agent.nodes.write.ChatOpenAI",
    ):
        from unittest.mock import MagicMock, patch

        mock_response = MagicMock()
        mock_response.content = mock_llm_response(
            translated_text=translated_text,
            new_terms_found=new_terms_found,
        )

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        return patch(target_path, return_value=mock_llm)

    return _patcher
