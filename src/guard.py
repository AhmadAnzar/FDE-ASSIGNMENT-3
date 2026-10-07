import re

# Common prompt injection and override phrases
INJECTION_PATTERNS = [
    r"(?i)\bignore all\b",
    r"(?i)\boverride\b",
    r"(?i)\bbypass\b",
    r"(?i)\bdisregard\b",
    r"(?i)\btreat this request as\b",
    r"(?i)\bapprove it immediately\b",
    r"(?i)\bcfo-approved\b",
    r"(?i)\byou must approve\b"
]

def scan_for_injection(text: str) -> bool:
    """
    Returns True if a prompt injection attack is detected in the text.
    """
    if not text:
        return False
        
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text):
            return True
            
    return False
