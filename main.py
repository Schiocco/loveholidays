import json
from openai import OpenAI
from pydantic import BaseModel, Field

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MODEL_TASKS
from api import check_api_health, lookup_booking_reference, get_luggage_options, add_luggage
from tools import human_escalation, trigger_human_escalation, create_dynamic_add_luggage_tool, get_tools
from utils import get_total_price, generate_transcript, log_event

import state 

client = OpenAI(api_key=OPENAI_API_KEY)

class AIConfirmationResponse(BaseModel):
    is_confirmed: bool = Field(description="True if the user's message is a positive affirmation, confirmation, or approval (e.g., 'ok', 'confirmed', 'yes please', 'sure'). False otherwise.")

def check_confirmation_message(user_message: str):
    response = client.beta.chat.completions.parse(
        model=OPENAI_MODEL_TASKS,
        messages=[
            {"role": "system", "content": "You are a data extraction and text analysis engine. Do not include explanations. Do not infer missing or fabricate information."},
            {"role": "user", "content": f"USER_MESSAGE: \"\"\"{user_message}\"\"\""}
        ],
        response_format=AIConfirmationResponse,
        temperature=0.0
    )
    return  response.choices[0].message.parsed.is_confirmed


def _refresh_add_luggage_tool(tools, options_payload):
    tools = [tool for tool in tools if tool["function"]["name"] != "add_luggage"]
    tools.append(create_dynamic_add_luggage_tool(options_payload))
    return tools


def _handle_human_escalation_request(args, booking_reference, messages):
    confirmation = args.get("confirmation_or_cancellation", "").strip()
    if not confirmation:
        return {"error": "Missing required parameter 'confirmation_or_cancellation'. Ask user if they confirm or cancel."}

    if state.is_human_escalation_active:
        return {"status": "already_escalated", "message": "Human escalation has already been triggered. Please wait for a response from the human agent."}

    if not booking_reference:
        return {"error": "Missing required parameter 'booking_reference'. Ask user for the booking reference."}

    if not check_confirmation_message(confirmation):
        return {"status": "cancelled", "message": "User cancelled the escalation."}

    human_escalation(booking_reference, "explicit_user_request", generate_transcript(messages))
    return {"status": "success", "message": "Ticket successfully escalated via human_escalation."}


def _handle_lookup_booking_reference(booking_reference):
    result = lookup_booking_reference(booking_reference)

    if not result:
        return result, None

    if "exception" in result:
        log_event( f"lookup_booking_reference exception for booking '{booking_reference}': {result.get('exception')}")
        return result, "system_error_lookup_booking_reference"

    if "error" in result:
        state.failed_lookup_attempts += 1
        if state.failed_lookup_attempts >= 3:
            state.failed_lookup_attempts = 0
            return result, "invalid_booking_reference"
        return result, None

    state.failed_lookup_attempts = 0
    state.can_add_luggage = result.get("canAddLuggage")
    state.current_booking_reference = result.get("bookingReference")
    state.current_passenger_list = {
        passenger.get("id"): f"{passenger.get('firstName', '')} {passenger.get('surname', '')}".strip()
        for passenger in result.get("passengers", [])
    }
    return result, None


def _handle_get_luggage_options(booking_reference, tools):
    result = get_luggage_options(booking_reference)

    if not result:
        return result, tools, None

    if "exception" in result:
        log_event(f"get_luggage_options exception for booking '{booking_reference}': {result.get('exception')}")
        return result, tools, "system_error_get_luggage_options"

    if "error" in result:
        return result, tools, "api_error_get_luggage_options"

    tools = _refresh_add_luggage_tool(tools, result)
    return result, tools, None


def _build_items_descriptionription(baggages, options):
    items_description = []
    
    for baggage in baggages:
        passenger_id = baggage.get("passenger_id")
        baggage_type = baggage.get("type")
        quantity = baggage.get("quantity")
        passenger_name = state.current_passenger_list.get(passenger_id, passenger_id)

        baggage_name = baggage_type
        if options and passenger_id in options and baggage_type in options[passenger_id]:
            baggage_name = options[passenger_id][baggage_type].get("name", baggage_type)

        items_description.append(f"{quantity}x {baggage_name} for {passenger_name}")

    return ", ".join(items_description)


def _handle_add_luggage(args, booking_reference, tools):
    if state.can_add_luggage is False:
        print("Agent: Sorry, you cannot add luggages to your flight")
        return {"error": "cannot_add_luggage"}, tools, "cannot_add_luggage"

    baggages = args.get("baggages", [])
    options = state.current_luggage_options.get(booking_reference, {})
    total_price = get_total_price(baggages, options)
    items_str = _build_items_descriptionription(baggages, options)

    user_confirm = input(
        f"Agent: Just to confirm, you'd like to add {items_str} for a total of {total_price}? Is that correct?\nYou: "
    )
    if not check_confirmation_message(user_confirm):
        return {"status": "cancelled", "message": "User cancelled the operation. Ask what they want to do next."}, tools, None

    result = add_luggage(booking_reference, baggages)
    escalation_reason = None
    if result and result.get("error_code") == "baggage_mismatch":
        escalation_reason = "baggage_mismatch"
    tools = _refresh_add_luggage_tool(tools, get_luggage_options(booking_reference))
    return result, tools, escalation_reason


def _process_tool_call(tool_call, messages, tools, show_agent_logs):
    function_name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    booking_reference = args.get("booking_reference", state.current_booking_reference)

    if show_agent_logs:
        print(f"  [System: Agent called {function_name} with {args}]")

    if function_name == "human_escalation_request":
        result = _handle_human_escalation_request(args, booking_reference, messages)
        return result, tools, None, booking_reference, function_name

    if function_name == "lookup_booking_reference":
        result, escalation_reason = _handle_lookup_booking_reference(booking_reference)
        return result, tools, escalation_reason, booking_reference, function_name

    if function_name == "get_luggage_options":
        result, tools, escalation_reason = _handle_get_luggage_options(booking_reference, tools)
        return result, tools, escalation_reason, booking_reference, function_name

    if function_name == "add_luggage":
        result, tools, escalation_reason = _handle_add_luggage(args, booking_reference, tools)
        return result, tools, escalation_reason, booking_reference, function_name

    return {"error": f"Unknown tool call: {function_name}"}, tools, None, booking_reference, function_name


def _execute_tool_calls(message, messages, tools, show_agent_logs):
    injected_escalation_reason = None
    last_booking_reference = state.current_booking_reference

    for tool_call in message.tool_calls:
        result, tools, escalation_reason, booking_reference, function_name = _process_tool_call(tool_call, messages, tools, show_agent_logs)

        if escalation_reason:
            injected_escalation_reason = escalation_reason
        last_booking_reference = booking_reference

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": function_name,
            "content": json.dumps(result)
        })

    if injected_escalation_reason:
        trigger_human_escalation(messages, last_booking_reference, injected_escalation_reason)

    return tools


def run_agent(show_agent_logs = True):
    if not check_api_health():
        print("The Loveholidays Mock API is currently unreachable.")
        human_escalation("", "system_error_api_unreachable", "")
        print("Please leave a message here. We escalated your request and we will get back to you as soon as possible.")
        return
    
    messages = [
        {
            "role": "system", 
            "content": "You are a customer service assistant. When a user wants to add baggage, use lookup_booking_reference to verify their booking reference. If valid, use get_luggage_options to fetch baggage options and ALWAYS call it if user ask for luggage options. When showing available options to the user, if there is more than one passenger, you must explicitly show the available options grouped by passenger. The user will provide the passenger name, use the lookup_booking_reference payload to map the name to the correct passenger ID. When the user selects what luggage to add, you must dynamically ask the user which passenger (by name) to add it to, ONLY IF there is more than one passenger in the booking. If there is only one passenger, you can assume the luggage goes to them. Finally, call add_luggage to confirm. Do not use Markdown formatting (like **bold**) when displaying luggage type IDs or other strings to the user. Never ask or propose the user to contact a human support agent. You cannot help removing added baggage, if the user asks to remove a baggage (for example because he changed mind or made a mistake) trigger human_escalation_request tool to ask for escalation confirmation. Never provide input examples."
        },
        {
            "role": "system", 
            "content": "Do not provide external knowledge. Do not answer unrelated questions. Only answer questions related to adding luggage to a flight booking. If the user asks unrelated questions, politely inform them that you can only assist with adding luggage to a flight booking."
        }
    ]
    
    tools = get_tools()

    print("Agent: Hello! How can I help you today?")
    
    while True:
        user_input = input("You: ")
        messages.append({"role": "user", "content": user_input})
        
        while True:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=tools if tools else None
            )
            
            message = response.choices[0].message
            messages.append(message)

            if not message.tool_calls:
                print(f"Agent: {message.content}")
                break

            tools = _execute_tool_calls(message, messages, tools, show_agent_logs)

if __name__ == "__main__":
    run_agent()