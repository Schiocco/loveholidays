import json
from openai import OpenAI
from pydantic import BaseModel, Field

from config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_MODEL_TASKS
from api import check_api_health, lookup_booking_reference, get_luggage_options, add_luggage
from tools import human_escalation, trigger_human_escalation, create_dynamic_add_luggage_tool, get_tools
from utils import get_total_price, generate_transcript, log_event

import state 

client = OpenAI(api_key=OPENAI_API_KEY)


# Check if the user's message is a confirmation or cancellation using OpenAI.

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


# Refresh the add_luggage tool based on the latest luggage options.

def _refresh_add_luggage_tool(tools, options_payload):
    tools = [tool for tool in tools if tool["function"]["name"] != "add_luggage"]
    tools.append(create_dynamic_add_luggage_tool(options_payload))
    return tools


# Handle human escalation requests.

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


# Handle lookup_booking_reference tool call. 
# Start a counter for failed attempts and escalate after 3 failures. 
# Return the booking reference and passenger list if successful.

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


# Handle get_luggage_options tool call.
# Return the luggage options and refresh the add_luggage tool with updated luggage options if successful.

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


# Generate a human-readable description of the luggage items for confirmation.

def _build_items_description(luggages, options):
    items_description = []
    
    for luggage in luggages:
        passenger_id = luggage.get("passenger_id")
        luggage_type = luggage.get("type")
        quantity = luggage.get("quantity")
        passenger_name = state.current_passenger_list.get(passenger_id, passenger_id)

        luggage_name = luggage_type
        if options and passenger_id in options and luggage_type in options[passenger_id]:
            luggage_name = options[passenger_id][luggage_type].get("name", luggage_type)

        items_description.append(f"{quantity}x {luggage_name} for {passenger_name}")

    return ", ".join(items_description)


# Handle add_luggage tool call.
# Check if the user can add luggage, confirm the items and total price with the user, and call add_luggage if confirmed.
# If the user cancels, return a cancellation message. 
# If there is a baggage mismatch error, trigger human escalation.
# Refresh the add_luggage tool with updated luggage options.

def _handle_add_luggage(args, booking_reference, tools):
    if state.can_add_luggage is False:
        print("Agent: Sorry, you cannot add luggages to your flight")
        return {"error": "cannot_add_luggage"}, tools, "cannot_add_luggage"

    luggages = args.get("luggages", [])
    options = state.current_luggage_options.get(booking_reference, {})
    total_price = get_total_price(luggages, options)
    items_str = _build_items_description(luggages, options)

    user_confirm = input(
        f"Agent: Just to confirm, you'd like to add {items_str} for a total of {total_price}? Is that correct?\nYou: "
    )
    if not check_confirmation_message(user_confirm):
        return {"status": "cancelled", "message": "User cancelled the operation. Ask what they want to do next."}, tools, None

    result = add_luggage(booking_reference, luggages)
    escalation_reason = None
    if result and result.get("error_code") == "baggage_mismatch":
        escalation_reason = "baggage_mismatch"
    tools = _refresh_add_luggage_tool(tools, get_luggage_options(booking_reference))
    
    return result, tools, escalation_reason


# Process tool calls based on the function name returned by the tool call.

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


# Execute tool calls returned by the agent, handle escalation if needed, and return updated tools.

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
        trigger_human_escalation(messages, last_booking_reference)

    return tools


# Run the agent loop, handle user input, and process tool calls until the user exits.
# If the API is unreachable, trigger human escalation and ask the user to leave a message.
# Add system messages to instruct the agent on how to handle luggage requests and human escalation.
# Add guards to ensure the agent only answers questions we want it to answer.
# To enable agent logs, set show_agent_logs to True.

def run_agent(show_agent_logs = False):
    if not check_api_health():
        print("The Loveholidays Mock API is currently unreachable.")
        human_escalation("", "system_error_api_unreachable", "")
        print("Please leave a message here. We escalated your request and we will get back to you as soon as possible.")
        return
    
    messages = [
        {
            "role": "system", 
            "content": "You are a customer service assistant for adding luggage to a flight booking.\n\nRules:\n- Only help with luggage-related requests.\n- Do not provide input examples.\n- Do not use Markdown formatting (for example **bold**) when displaying luggage type IDs or other strings.\n\nBooking and luggage flow:\n- If the user asks about luggage options, call lookup_booking_reference first.\n- If booking is valid, call get_luggage_options.\n- Always call get_luggage_options when the user asks for luggage options.\n- If there is more than one passenger, show available options grouped by passenger.\n- Use the lookup_booking_reference payload to map passenger name to passenger ID.\n- When the user selects luggage, ask which passenger only if there are multiple passengers.\n- If there is one passenger, assume the luggage goes to that passenger.\n- Finally, call add_luggage to confirm.\n\nEscalation behavior:\n- If the user asks to remove baggage, trigger escalation flow.\n- In human escalation flow, ask the user to confirm or cancel escalation, then call human_escalation_request."
        },
        {
            "role": "system", 
            "content": "Only answer questions related to human escalation, booking reference, managing luggage for a flight booking, greetings, thanks. Do not provide external knowledge. Do not answer unrelated questions. If the user asks unrelated questions, politely inform them that you can only assist with human escalation and adding luggage to a flight booking."
        }
    ]
    
    tools = get_tools()

    print("Agent: Hello! How can I help you today?")
    
    while True:
        user_input = input("You: ")
        messages.append({"role": "user", "content": user_input})
        
        while True:       
            try:
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    tools=tools if tools else None
                )
            except Exception as e:
                log_event(f"run_agent exception: {e}")
                print("Agent: Sorry, there was an error processing your request. Please try again later.")
                human_escalation("", "system_error_openai_api", "")
                print("Please leave a message here. We escalated your request and we will get back to you as soon as possible.")
                return
            
            message = response.choices[0].message
            messages.append(message)

            if not message.tool_calls:
                print(f"Agent: {message.content}")
                break

            tools = _execute_tool_calls(message, messages, tools, show_agent_logs)

if __name__ == "__main__":
    run_agent()