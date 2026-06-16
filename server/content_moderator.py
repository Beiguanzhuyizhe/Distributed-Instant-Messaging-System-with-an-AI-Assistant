"""
内容审核模块
关键词匹配 + Aho-Corasick 自动机实现 O(n) 多模式匹配
检测类别：辱骂/暴力/色情/政治敏感
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class ModerationResult:
    passed: bool
    reason: str = ""
    level: str = "low"  # high / mid / low


class AhoCorasick:
    """Aho-Corasick 多模式匹配自动机"""

    def __init__(self):
        self._goto = [{}]      # 转移表
        self._fail = [0]       # 失配链接
        self._output = [set()] # 每个状态的输出: {(word, category), ...}
        self._built = False

    def add_word(self, word: str, category: str):
        """添加关键词及其类别，节点存储 (word, category) 以支持精确匹配反馈"""
        node = 0
        for ch in word:
            if ch not in self._goto[node]:
                self._goto[node][ch] = len(self._goto)
                self._goto.append({})
                self._fail.append(0)
                self._output.append(set())
            node = self._goto[node][ch]
        self._output[node].add((word, category))
        self._built = False

    def build(self):
        """构建 fail 指针（BFS）"""
        from collections import deque
        q = deque()
        for ch, nxt in self._goto[0].items():
            self._fail[nxt] = 0
            q.append(nxt)

        while q:
            r = q.popleft()
            for ch, u in self._goto[r].items():
                q.append(u)
                v = self._fail[r]
                while v and ch not in self._goto[v]:
                    v = self._fail[v]
                self._fail[u] = self._goto[v].get(ch, 0)
                self._output[u] |= self._output[self._fail[u]]
        self._built = True

    def search(self, text: str) -> List[Tuple[str, str]]:
        """搜索文本，返回所有匹配 (关键词, 类别) 列表"""
        if not self._built:
            self.build()

        result = []
        node = 0
        for ch in text:
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            if self._output[node]:
                for word, cat in self._output[node]:
                    result.append((word, cat))
        return result

    def search_with_positions(self, text: str) -> List[Tuple[str, str, int, int]]:
        """搜索文本，返回 (关键词, 类别, start, end) 列表，用于精准替换"""
        if not self._built:
            self.build()

        result = []
        node = 0
        for i, ch in enumerate(text):
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            if self._output[node]:
                for word, cat in self._output[node]:
                    start = i - len(word) + 1
                    result.append((word, cat, start, i + 1))
        return result

    def has_match(self, text: str) -> bool:
        """快速检查是否有匹配"""
        if not self._built:
            self.build()

        node = 0
        for ch in text:
            while node and ch not in self._goto[node]:
                node = self._fail[node]
            node = self._goto[node].get(ch, 0)
            if self._output[node]:
                return True
        return False


class ContentModerator:
    """
    内容审核器
    支持关键词匹配 + 违规等级判定
    """

    def __init__(self):
        self._automaton = AhoCorasick()
        self._word_map = {}  # keyword -> category
        self._init_sensitive_words()

    def _init_sensitive_words(self):
        """初始化敏感词库"""
        # ===== 辱骂/人身攻击 =====
        abuse_words = [
            "傻逼", "草泥马", "fuck", "shit", "asshole", "bitch",
            "操你妈", "去死", "废物", "垃圾", "混蛋", "畜生",
            "sb", "SB", "2B", "nmsl", "NMSL",
        ]
        for w in abuse_words:
            self._add_word(w, "abuse")

        # ===== 暴力/威胁 =====
        violence_words = [
            "杀了你", "打死你", "弄死", "砍死", "炸死",
            "kill", "murder", "bomb", "attack",
        ]
        for w in violence_words:
            self._add_word(w, "violence")

        # ===== 色情 =====
        porn_words = [
            "色情", "av", "成人电影", "裸聊", "约炮",
            "porn", "sex", "xxx",
        ]
        for w in porn_words:
            self._add_word(w, "porn")

        # ===== 政治敏感 =====
        political_words = [
            "台独", "藏独", "疆独", "法轮功",
        ]
        for w in political_words:
            self._add_word(w, "political")

    def _add_word(self, word: str, category: str):
        normalized = self._normalize(word)
        self._automaton.add_word(normalized, category)
        self._word_map[normalized] = category

    @staticmethod
    def _normalize(text: str) -> str:
        """统一大小写，使英文敏感词检测对 Fuck/SB 等变体也生效。"""
        return text.lower()

    @staticmethod
    def _is_ascii_word_char(ch: str) -> bool:
        return ch.isascii() and (ch.isalnum() or ch == "_")

    @classmethod
    def _match_allowed(cls, text: str, word: str, start: int, end: int) -> bool:
        # English/number keywords should match whole tokens only. Otherwise
        # normal words such as "leave" (av), "skill" (kill) or "a2b" (2B)
        # get altered by the simple keyword filter.
        if word and all(cls._is_ascii_word_char(ch) for ch in word):
            before = text[start - 1] if start > 0 else ""
            after = text[end] if end < len(text) else ""
            return not cls._is_ascii_word_char(before) and not cls._is_ascii_word_char(after)
        return True

    def _filtered_matches(self, normalized_content: str) -> List[Tuple[str, str, int, int]]:
        matches = self._automaton.search_with_positions(normalized_content)
        return [
            (word, cat, start, end)
            for word, cat, start, end in matches
            if self._match_allowed(normalized_content, word, start, end)
        ]

    def moderate(self, content: str) -> ModerationResult:
        """
        审核内容，返回审核结果
        等级判定:
          - high: 政治敏感/暴力 → 直接屏蔽
          - mid: 辱骂/色情 → 替换敏感词 + 警告
          - low: 无违规 → 放行但不记录
        """
        if not content or not content.strip():
            return ModerationResult(passed=True, level="low")

        normalized_content = self._normalize(content)
        matches = self._filtered_matches(normalized_content)
        if not matches:
            return ModerationResult(passed=True, level="low")

        categories = {cat for _, cat, _, _ in matches}

        # high 级别: 政治敏感、暴力
        if "political" in categories or "violence" in categories:
            return ModerationResult(
                passed=False,
                reason=f"内容包含违规词汇: {', '.join(categories)}",
                level="high"
            )

        # mid 级别: 辱骂、色情
        if "abuse" in categories or "porn" in categories:
            return ModerationResult(
                passed=False,
                reason=f"内容包含不当言论: {', '.join(categories)}",
                level="mid"
            )

        return ModerationResult(passed=True, level="low")

    def replace_sensitive(self, content: str, replacement: str = "***") -> str:
        """替换内容中的敏感词为 ***，使用 AC 自动机精准定位"""
        if not content:
            return content

        matches = self._filtered_matches(self._normalize(content))
        if not matches:
            return content

        # 按 start 排序，合并重叠区域
        positions = sorted(set((s, e) for _, _, s, e in matches), key=lambda x: x[0])
        merged = []
        for s, e in positions:
            if merged and s < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))

        # 从右向左替换，避免 offset 偏移
        result = list(content)
        for start, end in reversed(merged):
            result[start:end] = list(replacement)
        return "".join(result)

    def _find_match_positions(self, text: str) -> List[Tuple[int, int]]:
        """找到所有匹配词的位置 [(start, end), ...]（兼容旧接口）"""
        matches = self._automaton.search_with_positions(text)
        return sorted(set((s, e) for _, _, s, e in matches), key=lambda x: x[0])

    def add_custom_word(self, word: str, category: str = "custom"):
        """动态添加自定义敏感词"""
        self._add_word(word, category)


# 全局单例
_moderator: Optional[ContentModerator] = None


def get_moderator() -> ContentModerator:
    global _moderator
    if _moderator is None:
        _moderator = ContentModerator()
    return _moderator
