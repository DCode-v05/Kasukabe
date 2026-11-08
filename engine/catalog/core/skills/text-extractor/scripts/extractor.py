import json
import re

def run(input_text: str) -> str:
    results = {"names": [], "dates": [], "amounts": [], "emails": [], "urls": [], "phone_numbers": []}

    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    results["emails"] = list(set(email_pattern.findall(input_text)))

    url_pattern = re.compile(r"https?://[^\s<>]+|www\.[^\s<>]+")
    results["urls"] = list(set(url_pattern.findall(input_text)))

    phone_pattern = re.compile(r"(?:\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}")
    results["phone_numbers"] = list(set(phone_pattern.findall(input_text)))

    money_pattern = re.compile(r"\$[\d,]+\.?\d*|\d+\.?\d*\s*(?:USD|EUR|GBP|dollars|euros|pounds)", re.IGNORECASE)
    results["amounts"] = list(set(money_pattern.findall(input_text)))

    date_pattern = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\w+ \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}")
    results["dates"] = list(set(date_pattern.findall(input_text)))

    name_pattern = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
    results["names"] = list(set(name_pattern.findall(input_text)))

    results = {k: v for k, v in results.items() if v}
    return json.dumps(results)