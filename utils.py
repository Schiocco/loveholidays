from datetime import datetime
from pathlib import Path


# Calculates total price of luggage items.

def get_total_price(items: list, luggage_options: dict):
    try:
        total = 0
        currency = "GBP"
        for b in items:
            passenger_id = b.get("passenger_id")
            baggage_type = b.get("type")
            quantity = b.get("quantity", 0)
            
            if passenger_id in luggage_options and baggage_type in luggage_options[passenger_id]:
                opt = luggage_options[passenger_id][baggage_type]
                total += opt.get("unitPrice", 0) * quantity
                currency = opt.get("currency", "GBP")
                
        currency_symbol = "£" if currency == "GBP" else ("$" if currency == "USD" else ("€" if currency == "EUR" else currency + " "))
        return f"{currency_symbol}{total}"
    except Exception as e:
        log_event(f"get_total_price exception: {e}")
        return "Unknown Price"
    

# Generates a human-readable transcript of the conversation

def generate_transcript(messages):
    transcript_lines = ["Assistant: Hello! How can I help you today?"]
    
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", False)
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            transcript_lines.append(f"{role[0].upper() + role[1:]}: {content.strip()}")
            
    return "\n".join(transcript_lines)


# Appends a timestamped log line to app.log in the current folder

def log_event(message: str, level: str = "CRITICAL"):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path = Path("app.log")
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] [{level}] {message}\n")
    except Exception:
        return