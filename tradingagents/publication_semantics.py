"""Offset-preserving script aliases for publication policy classification.

This is not report translation. Classifiers inspect equivalent Chinese script
forms while findings retain the exact original string, offsets and claim SHA.
One character maps to one character; Japanese-specific vocabulary stays in its
existing classifiers rather than being translated into invented Chinese prose.
"""

_SCRIPT_ALIASES = str.maketrans({
    "評": "评", "級": "级", "議": "议", "薦": "荐", "結": "结",
    "論": "论", "綜": "综", "終": "终", "決": "决", "選": "选",
    "擇": "择", "倉": "仓", "減": "减", "賣": "卖",
    "碼": "码", "續": "续", "現": "现", "為": "为", "與": "与",
    "態": "态", "調": "调", "這": "这", "將": "将",
    "強": "强", "對": "对", "應": "应",
    "產": "产", "戶": "户", "價": "价", "線": "线", "機": "机",
    "會": "会", "險": "险", "風": "风", "營": "营",
    "準": "准", "際": "际", "觀": "观", "單": "单", "動": "动",
    "從": "从", "過": "过", "實": "实", "證": "证", "歷": "历",
    "執": "执", "們": "们", "綱": "纲", "領": "领", "籌": "筹",
    "構": "构", "壓": "压",
})


def semantic_script(text: str) -> str:
    """Fold classifier spelling only, preserving every source offset."""
    return "".join(char if (char in {"強", "弱"} and text[index + 1:index + 2] == "気"
                           or char == "評" and text[index + 1:index + 2] == "価")
                   else char.translate(_SCRIPT_ALIASES)
                   for index, char in enumerate(text))
