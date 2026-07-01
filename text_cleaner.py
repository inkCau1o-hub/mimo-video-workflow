import re

class TextCleaner:
    def __init__(self):
        self.fillers = [
            '啊', '嗯', '呃', '这个', '那个', '就是', '然后', '其实',
            '怎么说呢', '你比如说', '比如说', '我告诉你', '大家看',
            '那么', '那么说', '那么样', '什么样', '什么呀', '什么啊',
            '干嘛', '干啥', '咋样', '咋的', '对吧', '是不是', '有没有',
            '好吧', '那啥', '那个啥', '内个', '嗨', '哎呀', '我擦',
            '我靠', '我了个去', '我次奥', '我靠', '我擦', '我日',
            '哇塞', '我天', '妈呀', '我的天', '我的妈呀', '乖乖',
            '牛逼', '牛叉', '厉害', '厉害了我的哥', '吓死宝宝了',
            '笑死了', '笑喷了', '笑Cry', '笑尿', '哭笑不得',
            '醉了', '碉堡了', '炸裂', '666', '真香', '打脸',
            '真难', '太难了', '好难', '好累', '累死了', '困死了',
            '困得一批', '饿死了', '渴死了', '热死了', '冷死了',
            '冻死了', '疼死了', '痛死了', '爽死了', '美死了',
            '臭美', '得瑟', '嘚瑟', '拽什么拽', '拽啥拽',
            '有啥了不起', '有什么了不起', '了不起呀', '了不起啊',
            '切', '切~', '切~', '切~~~~', '哼', '哼~', '哼~~~~',
            '略略略', '略略略~~~~', '去你的', '滚蛋', '去死',
            '死一边去', '一边去', '玩去', '玩蛋去', '歇菜',
            '歇逼', '歇比', '傻逼', '傻叉', '二逼', '二货',
            '蠢货', '笨蛋', '白痴', '脑残', '智障', '沙雕',
            '煞笔', '伞兵', '杂碎', '人渣', '败类', '畜生',
            '禽兽', '变态', '恶心', '虚伪', '装逼', '装叉',
            '装蒜', '装傻', '装嫩', '装酷', '装逼犯', '心机',
            '心机婊', '心机boy', '心机girl', '绿茶婊', '白莲花',
            '圣母', '圣母婊', '直男', '直女', '钢铁直男',
            '直男癌', '直女癌', '单身狗', '单身贵族', '单身汪',
            '虐狗', '撒狗粮', '吃狗粮', '柠檬精', '柠檬狗',
            '酸了', '酸死了', '羡慕', '嫉妒', '恨', '仇富',
            '仇穷', '仇美', '仇帅', '仇可爱', '玻璃心',
            '玻璃心碎', '心碎', '心塞', '扎心', '戳心',
            '戳心窝子', '感动', '感动哭了', '感动天地',
            '哭死', '哭瞎', '哭成狗', '哭晕在厕所', '笑成狗',
            '笑晕在厕所', '笑到肚子疼', '笑到头掉', '头笑掉',
            '头笑掉在地上', '6666', '8888', '9999', '0000',
            '520', '1314', '5201314', '888888', '999999',
            '000000', '123456', '666666', '888888', '999999',
            'wsnd', 'nmbs', 'cnm', 'nmsl', 'qnmlgb', 'rnm',
            'rnmlgb', 'wqnmlgb', 'djb', 'djbdjb', 'sb', 'nm',
            'wtf', 'damn', 'hell', 'suck', 'shit', 'fuck',
            'asshole', 'bastard', 'idiot', 'stupid', 'dumb',
            'moron', 'jerk', 'loser', 'psycho', 'scumbag'
        ]

        self.filler_pattern = None
        self._build_pattern()

    def _build_pattern(self):
        fillers_with_word = []
        for filler in self.fillers:
            if len(filler) > 1:
                fillers_with_word.append(filler)
            else:
                fillers_with_word.append(r'\s?' + re.escape(filler) + r'\s*')

        pattern = r'\b(' + '|'.join(fillers_with_word) + r')\b'
        self.filler_pattern = re.compile(pattern, re.IGNORECASE)

    def clean_text(self, text):
        if not text:
            return ""

        result = text

        result = self.filler_pattern.sub('', result)

        result = re.sub(r'(.)\1{2,}', r'\1\1', result)

        result = re.sub(r'[^\S\n]+', ' ', result)

        result = re.sub(r'\n{3,}', '\n\n', result)

        result = result.strip()

        return result

    def remove_fillers(self, text):
        if not text:
            return ""

        result = self.filler_pattern.sub('', text)

        result = re.sub(r'\s+', ' ', result)

        return result.strip()

    def remove_repeated_chars(self, text):
        if not text:
            return ""

        result = re.sub(r'(.)\1{2,}', r'\1\1', text)

        return result

    def normalize_spaces(self, text):
        if not text:
            return ""

        result = re.sub(r'[^\S\n]+', ' ', text)

        result = result.strip()

        return result

    def remove_special_chars(self, text):
        if not text:
            return ""

        result = re.sub(r"[^\w\s\u4e00-\u9fff，。！？、；：\"'“”‘’（）【】《》…—.,!?;:()\[\]<>\-]", "", text)

        return result
