import state 
import json
from api import escalation
from utils import log_event


# Tools for handling human escalation.

def human_escalation(booking_reference: str, reason: str, user_message: str):
    if not state.is_human_escalation_active:
        try:
            escalation(booking_reference, reason, user_message)  
        except Exception as e:
            log_event(f"human_escalation exception for booking '{booking_reference}': {e}")
        
        state.is_human_escalation_active = True


# Trigger a human_escalation_request tool call causing the agent to recognize missing data and explicitly query the user next.

def trigger_human_escalation(messages, booking_reference: str):
    import uuid
    manual_call_id = f"call_{uuid.uuid4().hex[:20]}"
    
    messages.append({
         "role": "assistant",
         "content": None,
         "tool_calls": [{
             "id": manual_call_id,
             "type": "function",
             "function": {
                 "name": "human_escalation_request",
                 "arguments": json.dumps({
                     "booking_reference": booking_reference if booking_reference else ""
                 })
             }
         }]
    })
    
    messages.append({
        "role": "tool",
        "tool_call_id": manual_call_id,
        "name": "human_escalation_request",
        "content": json.dumps({"error": "Missing required parameter 'confirmation_or_cancellation'. You MUST ask the user if they confirm or cancel the escalation."})
    })
    

# Dynamically create the add_luggage tool based on the given luggage options.
    
def create_dynamic_add_luggage_tool(luggage_options):
    enum_values = set()
    passenger_ids = set()
    
    for passenger, items in luggage_options.items():
        passenger_ids.add(passenger)
        for option_id in items.keys():
            enum_values.add(option_id)
            
    enum_values = list(enum_values)
    passenger_ids = list(passenger_ids)
    
    return {
        "type": "function",
        "function": {
            "name": "add_luggage",
            "description": "Add luggage to the booking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_reference": {
                        "type": "string",
                        "description": "The booking reference. Examples: 'LH123456', 'LH777888'."
                    },
                    "luggages": {
                        "type": "array",
                        "description": "List of luggages to add",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": enum_values,
                                    "description": "The ID of the luggage to add."
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": "Quantity to add"
                                },
                                "passenger_id": {
                                    "type": "string",
                                    "enum": passenger_ids,
                                    "description": "The ID of the passenger to add luggage to."
                                }
                            },
                            "required": ["type", "quantity", "passenger_id"]
                        }
                    }
                },
                "required": ["booking_reference", "luggages"]
            }
        }
    }


# Returns the list of static tools available to the agent.

def get_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "lookup_booking_reference",
                "description": "Verify a booking reference",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "booking_reference": {
                            "type": "string",
                            "description": "The booking reference to verify. Examples: 'LH123456', 'LH777888'"
                        }
                    },
                    "required": ["booking_reference"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_luggage_options",
                "description": "Get available luggage options using a verified booking reference",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "booking_reference": {
                            "type": "string",
                            "description": "The booking reference to fetch luggage options for. Examples: 'LH123456', 'LH777888'"
                        }
                    },
                    "required": ["booking_reference"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "human_escalation_request",
                "description": "User want to contact a human support agent or team member. User want human support. Example: 'How can I have support?'. Always ask for confirmation and booking reference (only if booking reference not already provided in previous messages) before contacting a human agent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "confirmation_or_cancellation": {
                            "type": "string",
                            "description": "Positive affirmation, confirmation, or approval (e.g., 'ok', 'yes', 'confirmed', 'yes please', 'sure') or explicit cancellation (e.g., 'no', 'cancel', 'not now')."
                        },
                        "booking_reference": {
                            "type": "string",
                            "description": "The booking reference to fetch luggage options for. Examples: 'LH123456', 'LH777888'"
                        }
                    },
                    "required": ["confirmation_or_cancellation", "booking_reference"]
                }
            }
        }
    ]

