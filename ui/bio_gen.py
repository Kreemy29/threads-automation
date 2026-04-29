"""
LLM-powered bio generator using DeepSeek API.
Generates varied, natural-sounding bios from a niche/theme prompt.
"""
import requests


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"


def generate_bios(api_key: str, niche: str, count: int = 15) -> list[str]:
    """
    Generate `count` unique bio variations for the given niche.
    Returns a list of bio strings.
    Raises RuntimeError on API failure.
    """
    if not api_key:
        raise ValueError("DeepSeek API key is required")

    prompt = (
        f"Generate {count} unique, natural-sounding social media bios for a '{niche}' account on Threads.\n"
        "Rules:\n"
        "- Each bio is 1 to 2 lines max\n"
        "- Include 1-2 relevant emojis per bio\n"
        "- Vary the tone: some playful, some confident, some mysterious\n"
        "- No hashtags\n"
        "- Sound like a real person, not a brand\n"
        "- Output ONLY the bios, one per line, no numbering, no quotes, nothing else"
    )

    resp = requests.post(
        DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 1.1,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    text = data["choices"][0]["message"]["content"].strip()
    bios = [line.strip() for line in text.splitlines() if line.strip()]
    return bios[:count]
