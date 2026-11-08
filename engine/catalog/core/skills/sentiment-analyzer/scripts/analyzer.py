import json
import re

def run(input_text: str) -> str:
    text = input_text.lower()
    positive_words = {"good","great","excellent","amazing","wonderful","fantastic","love","happy","best","awesome","brilliant","outstanding","perfect","enjoy","pleased"}
    negative_words = {"bad","terrible","awful","horrible","hate","worst","poor","disappointing","annoying","frustrating","ugly","boring","broken","fail","useless"}
    words = set(re.findall(r"\w+", text))
    pos = len(words & positive_words)
    neg = len(words & negative_words)
    total = pos + neg
    if total == 0:
        return json.dumps({"sentiment":"neutral","confidence":0.5,"positive_signals":0,"negative_signals":0})
    score = pos / total
    if score > 0.6:
        label = "positive"
    elif score < 0.4:
        label = "negative"
    else:
        label = "neutral"
    confidence = round(max(score, 1 - score), 2)
    return json.dumps({"sentiment":label,"confidence":confidence,"positive_signals":pos,"negative_signals":neg})