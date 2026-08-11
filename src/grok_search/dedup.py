import re
from typing import Set, List

# --- Sentence splitting ---
_URL_PATTERN = re.compile(r'https?://[^\s<>"\x27`，。、；：！？》）】\)]+')
_MARKDOWN_LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(https?://[^)]+\)')
_CITATION_PATTERN = re.compile(r'\[\d+\]')

def split_sentences(text: str) -> List[str]:
    if not text or not text.strip():
        return []
    parts = re.split(r'(?<=[。！？!?])\s*', text)
    sentences = []
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        sub_parts = [s.strip() for s in stripped.split('\n') if s.strip()]
        sentences.extend(sub_parts)
    return sentences

# --- Tokenization ---
_CHINESE_CHAR = re.compile(r'[\u4e00-\u9fff]')
_ENGLISH_WORD = re.compile(r'[a-zA-Z]{2,}')
_NUMBER = re.compile(r'\d+(?:\.\d+)?')

_STOP_WORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'can', 'shall', 'to', 'of', 'in', 'for',
    'on', 'at', 'by', 'with', 'from', 'as', 'into', 'through', 'during',
    'before', 'after', 'above', 'below', 'between', 'out', 'off', 'over',
    'under', 'again', 'further', 'then', 'once', 'here', 'there', 'when',
    'where', 'why', 'how', 'all', 'each', 'every', 'both', 'few', 'more',
    'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
    'same', 'so', 'than', 'too', 'very', 'just', 'because', 'but', 'and',
    'or', 'if', 'while', 'although', 'this', 'that', 'these', 'those',
    'it', 'its', 'he', 'she', 'they', 'them', 'their', 'we', 'you', 'your',
    '的', '了', '是', '在', '和', '也', '就', '都', '而', '及', '与',
    '着', '或', '一个', '没有', '我们', '你们', '他们', '这个', '那个',
    '不', '很', '会', '有', '可以', '应该', '要', '让', '被', '把',
    '从', '对', '到', '上', '下', '中', '大', '小', '多', '少',
}

def tokenize(text: str) -> Set[str]:
    clean = _URL_PATTERN.sub('', text)
    clean = _MARKDOWN_LINK_PATTERN.sub(r'\1', clean)
    clean = _CITATION_PATTERN.sub('', clean)
    tokens = set()
    for ch in _CHINESE_CHAR.findall(clean):
        if ch not in _STOP_WORDS:
            tokens.add(ch)
    words = _ENGLISH_WORD.findall(clean.lower())
    for w in words:
        if w not in _STOP_WORDS and len(w) >= 2:
            tokens.add(w)
    for n in _NUMBER.findall(clean):
        tokens.add(f'__NUM_{n}__')
    return tokens

# --- Similarity ---
def jaccard_similarity(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0

def extract_entities(text: str) -> Set[str]:
    entities = set()
    for match in re.finditer(r'(\d+(?:\.\d+)?)\s*(分|星|级|/10|/100)', text):
        entities.add(f'__RATING_{match.group(1)}__')
    for match in re.finditer(r'「([^」]+)」|《([^》]+)》|"([^"]+)"', text):
        term = match.group(1) or match.group(2) or match.group(3)
        if term and len(term) >= 2:
            entities.add(f'__TERM_{term}__')
    for match in re.finditer(r'(评分|评价|推荐指数|打分)[：:]?\s*(\d+(?:\.\d+)?)', text):
        entities.add(f'__SCORE_{match.group(2)}__')
    return entities

def is_substantive(sentence: str) -> bool:
    s = sentence.strip()
    if not s:
        return False
    content_chars = _CHINESE_CHAR.findall(s)
    content_words = _ENGLISH_WORD.findall(s)
    meaningful = len(content_chars) + len(content_words)
    if meaningful < 4:
        return False
    if len(s) < 10 and meaningful < 6:
        return False
    if _URL_PATTERN.fullmatch(s):
        return False
    if re.match(r"^[-*#=]{3,}$", s):
        return False
    return True
def is_redundant(sentence: str, primary_sentences: List[str], token_cache: dict = None) -> bool:
    if not is_substantive(sentence):
        return True
    sent_tokens = tokenize(sentence)
    sent_entities = extract_entities(sentence)
    if not sent_tokens and not sent_entities:
        return True
    if token_cache is None:
        token_cache = {}
    for primary_sent in primary_sentences:
        if primary_sent in token_cache:
            p_tokens, p_entities = token_cache[primary_sent]
        else:
            p_tokens = tokenize(primary_sent)
            p_entities = extract_entities(primary_sent)
            token_cache[primary_sent] = (p_tokens, p_entities)
        combined_s = sent_tokens | sent_entities
        combined_p = p_tokens | p_entities
        sim = jaccard_similarity(combined_s, combined_p)
        if sim > 0.65:
            return True
    return False

def extract_supplementary(primary_text: str, secondary_text: str) -> str:
    if not secondary_text or not secondary_text.strip():
        return ""
    if not primary_text or not primary_text.strip():
        return secondary_text
    primary_sentences = split_sentences(primary_text)
    secondary_sentences = split_sentences(secondary_text)
    if not primary_sentences:
        return secondary_text
    if not secondary_sentences:
        return ""
    token_cache = {}
    for ps in primary_sentences:
        if ps not in token_cache:
            token_cache[ps] = (tokenize(ps), extract_entities(ps))
    supplementary_parts = []
    seen = set()
    for s in secondary_sentences:
        s_stripped = s.strip()
        if not s_stripped or s_stripped in seen:
            continue
        if not is_redundant(s_stripped, primary_sentences, token_cache):
            seen.add(s_stripped)
            supplementary_parts.append(s_stripped)
    return ' '.join(supplementary_parts) if supplementary_parts else ""
