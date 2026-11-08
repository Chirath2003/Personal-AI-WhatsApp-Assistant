import re

RULES = [
    (r"\b(otp|one.time password|verification code|password|passcode)\b", "SENSITIVE"),
    (r"\b(bank|banking|transaction|transfer|payment|account number|credit card)\b", "FINANCIAL"),
    (r"\b(suicide|kill myself|emergency|urgent help|accident|hospital)\b", "EMERGENCY"),
    (r"\b(send money|loan me|lend me|pay for me|commit|promise)\b", "COMMITMENT"),
    (r"\b(unsubscribe|limited.time.offer|buy.now|promo.code|promotion|casino|lottery|claim.your.prize)\b", "SPAM"),
]

def classify(text):
    value = text.lower()
    for pattern, category in RULES:
        if re.search(pattern, value):
            if category == "SPAM":
                return {"category": category, "action": "IGNORE", "requires_approval": False, "reason": "Promotional or spam-like message filtered."}
            return {"category": category, "action": "HUMAN_ATTENTION", "requires_approval": True, "reason": "Sensitive or high-impact content needs your direct review."}
    return {"category": "GENERAL", "action": "REQUIRE_APPROVAL", "requires_approval": True, "reason": "All replies require your approval by default."}
